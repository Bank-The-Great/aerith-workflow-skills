"""Behavioral fixtures. These are NOT live-provider or containment proofs."""
import copy
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_creator.contracts import (GateError, criteria, digest, safe_relative, ticket_order, validate_review)
from project_creator.engine import Engine, RESOURCE_FILES, configure_runtime_resources, start
from project_creator.models import catalog_pin, model_for, refresh_catalog
from project_creator.store import Store, atomic_text, exclusive
from project_creator.workspace import (configure_git, git, propose_edits, reconcile_edits,
                                       snapshot, project_lock, repo_git, _overwrite_existing)
from project_creator.providers import attest_model, validate_capability
from project_creator.admission import verify_package, CORE_SKILLS, admission_stopped
from project_creator.processes import execute, reviewed_files
from project_creator.mirror import sync, marker
from project_creator.cli import main

PACKAGE = Path(__file__).resolve().parents[1]
GIT_EXE = Path(os.environ.get(
    "PROJECT_CREATOR_TEST_GIT", r"C:\Program Files\Git\mingw64\bin\git.exe"))
configure_git({"executable": str(GIT_EXE), "sha256": digest(GIT_EXE.read_bytes())})
configure_runtime_resources({name: (PACKAGE / name).read_bytes()
                             for name in RESOURCE_FILES.values()})


def spec():
    return {"title": "Increment", "non_goals": ["network"], "requirements": [{"id": "REQ-1", "text": "increment an integer",
            "acceptance": [{"id": "AC-1", "text": "increment(3) is 4", "test_ids": ["unit"]}]}]}


def catalog():
    return {"project_creator": {"vendors": {vendor: {"highest": f"{vendor}-top-1", "second-highest": f"{vendor}-second-1",
            "verified_at": datetime.now(timezone.utc).isoformat(), "source": url}
            for vendor, url in (("codex", "https://developers.openai.com/api/docs/models"),
                                ("claude", "https://platform.claude.com/docs/en/models/overview"),
                                ("gemini", "https://ai.google.dev/gemini-api/docs/models"))}}}


class FixtureProvider:
    def __init__(self, log):
        self.log = log

    def invoke(self, stage, model, packet, directory):
        self.log.append((stage, model, copy.deepcopy(packet)))
        if stage == "grill-with-docs":
            return {"brief": {"summary": "Increment an integer", "decisions": [], "glossary": {}}, "questions": []}
        if stage == "to-spec":
            return {"spec": spec(), "questions": []}
        if stage == "to-tickets":
            return {"tickets": [{"id": "T-1", "title": "Increment", "criteria": ["AC-1"], "blocked_by": [], "write_set": ["calc.py"]}]}
        if stage == "implement":
            old = packet["source"]["files"]["calc.py"]
            return {"changes": [{"path": "calc.py", "expected_sha256": digest(old), "content": "def increment(x):\n    return x + 1\n"}]}
        return {"verdict": "pass", "checked_criteria": packet["criteria_in_scope"], "findings": [], "limitations": []}


class FixtureVerifier:
    def run(self, ids, root, *, expected_files=None):
        # Fixed trusted fixture test, not a sandbox claim or model-generated test.
        result = execute([sys.executable, "-I", "-c", "from pathlib import Path; assert Path('calc.py').read_text() == 'def increment(x):\\n    return x + 1\\n'"], cwd=root)
        return [{"test_id": x, "exit_code": result.returncode, "evidence": "trusted-fixture-only"} for x in set(ids)]


class Harness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="project-creator-test-")
        self.root = Path(self.temp.name)
        self.project = self.root / "source"
        self.project.mkdir()
        subprocess.run([str(GIT_EXE), "init", "-q", str(self.project)], check=True)
        git(self.project, "config", "user.name", "Fixture")
        git(self.project, "config", "user.email", "fixture@example.invalid")
        (self.project / "calc.py").write_text("def increment(x):\n    return x\n", encoding="utf-8")
        git(self.project, "add", "calc.py")
        git(self.project, "commit", "-qm", "fixture base")
        self.store = Store(self.root / "state", create=True)
        self.config = {"read_set": ["calc.py"], "write_set": ["calc.py"], "tests": {"unit": [sys.executable, "-m", "unittest"]}}
        self.log = []
        self.engine = Engine(self.store, provider_factory=lambda run, vendor: FixtureProvider(self.log), verifier_factory=lambda run: FixtureVerifier(), admission_check=lambda run, stage: {"fixture_only": True})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def create(self, **kwargs):
        return start(self.store, self.project, "Make increment add one", self.config, catalog(), "codex", **kwargs)

    def test_full_pipeline_delivers_branch_not_source(self):
        initial = (self.project / "calc.py").read_text()
        run = self.create()
        result = self.engine.run(run["id"], max_steps=12)
        self.assertEqual(result["status"], "completed", result.get("last_error"))
        self.assertEqual(initial, (self.project / "calc.py").read_text())
        self.assertNotEqual(result["base"], result["delivery_commit"])
        worktree = Path(result["worktree"])
        git_dir = Path(result["git_dir"])
        self.assertFalse((worktree / ".git").exists())
        self.assertEqual(repo_git(git_dir, worktree, "rev-parse", "--verify",
                                  "refs/heads/" + result["branch"]), result["delivery_commit"])
        committed = repo_git(git_dir, worktree, "cat-file", "blob",
                             result["delivery_commit"] + ":calc.py", strip=False)
        self.assertEqual(committed, "def increment(x):\n    return x + 1\n")
        self.assertEqual([x[0] for x in self.log], ["grill-with-docs", "to-spec", "to-tickets", "implement", "spec-review", "defect-review", "spec-review", "defect-review"])
        self.assertTrue(self.store.verify())

    def test_packets_use_frozen_reviewed_resources_without_disk_reopen(self):
        run = self.create()
        with patch.object(Path, "read_text", side_effect=AssertionError("package resource reopened")):
            packet = self.engine.packet(run, "grill-with-docs")
        self.assertIn("Grill", packet["role"])
        self.assertIn("engineering method", packet["method"])

    def test_new_scoped_file_is_refused_before_any_model_call(self):
        config = copy.deepcopy(self.config)
        config["read_set"].append("new.py")
        config["write_set"].append("new.py")
        with self.assertRaisesRegex(GateError, "existing tracked files only"):
            start(self.store, self.project, "Create a file", config, catalog(), "codex")
        self.assertEqual(self.log, [])

    def test_cross_scope_case_collision_is_refused(self):
        config = copy.deepcopy(self.config)
        config["read_set"] = ["calc.py"]
        config["write_set"] = ["CALC.py"]
        with self.assertRaisesRegex(GateError, "case collision"):
            start(self.store, self.project, "Edit one file", config, catalog(), "codex")

    def test_required_clean_filter_is_never_invoked_by_controller(self):
        (self.project / ".gitattributes").write_text("calc.py filter=hostile\n", encoding="utf-8")
        git(self.project, "add", ".gitattributes")
        git(self.project, "commit", "-qm", "add hostile attributes fixture")
        git(self.project, "config", "filter.hostile.clean", "false")
        git(self.project, "config", "filter.hostile.smudge", "false")
        git(self.project, "config", "filter.hostile.required", "true")
        run = self.create()
        result = self.engine.run(run["id"], max_steps=12)
        self.assertEqual(result["status"], "completed", result.get("last_error"))

    def test_model_roles_and_cross_vendor_review(self):
        run = self.create(spec_vendor="claude")
        result = self.engine.run(run["id"], max_steps=12)
        self.assertEqual(result["status"], "completed")
        for stage, model, packet in self.log:
            expected = "claude-second-1" if stage == "spec-review" else "codex-top-1" if stage in ("grill-with-docs", "to-spec", "to-tickets") else "codex-second-1"
            self.assertEqual(model, expected)
            if stage.endswith("review"):
                self.assertEqual(packet["feedback"], [])

    def test_standalone_stops_at_artifact(self):
        run = self.create(standalone="grill-with-docs")
        result = self.engine.run(run["id"], max_steps=10)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([x[0] for x in self.log], ["grill-with-docs"])
        self.assertNotIn("delivery_commit", result)

    def test_unavailable_provider_pauses_without_fallback(self):
        run = self.create()
        result = Engine(self.store, admission_check=lambda run, stage: {}).run(run["id"], max_steps=10)
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["last_error"], "provider adapter not configured")
        self.assertEqual(result["vendor"], "codex")

    def test_cached_response_requires_unchanged_durable_receipt(self):
        run = self.create()
        packet = self.engine.packet(run, "grill-with-docs")
        first = self.engine.invoke(run, "grill-with-docs", packet)
        self.assertEqual(self.engine.invoke(run, "grill-with-docs", packet), first)
        self.assertEqual(len(self.log), 1)
        path = next((self.store.root / run["id"] / "calls").glob("*/response.json"))
        path.write_text('{"brief":{"summary":"altered"}}', encoding="utf-8")
        with self.assertRaisesRegex(GateError, "cached response"):
            self.engine.invoke(run, "grill-with-docs", packet)
        self.assertEqual(len(self.log), 1)

    def test_changed_spec_refuses(self):
        run = self.create()
        self.engine.run(run["id"], max_steps=2)
        path = self.store.root / run["id"] / "spec.md"
        path.write_text(path.read_text() + "\nExternal change", encoding="utf-8")
        result = self.engine.run(run["id"], max_steps=2)
        self.assertEqual(result["status"], "paused")
        self.assertIn("artifact", result["last_error"])

    def test_dirty_selected_source_not_overwritten(self):
        (self.project / "calc.py").write_text("user work", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "differs"):
            self.create()
        self.assertEqual((self.project / "calc.py").read_text(), "user work")

    def test_replaced_git_metadata_directory_is_refused(self):
        run = self.create()
        metadata = self.project / ".git"
        metadata.rename(self.project / ".git.original")
        metadata.mkdir()
        with self.assertRaisesRegex(GateError, "metadata directory identity changed"):
            self.engine.run(run["id"], max_steps=1)
        self.assertEqual(self.log, [])

    def test_existing_empty_worktree_recovers_missing_ref_and_files(self):
        run = self.create()
        root = Path(run["worktree"])
        root.mkdir(parents=True)
        self.engine.run(run["id"], max_steps=1)
        self.assertEqual((root / "calc.py").read_text(encoding="utf-8"),
                         "def increment(x):\n    return x\n")
        self.assertEqual(self.log[0][0], "grill-with-docs")

    def test_existing_partial_worktree_recovers_missing_files(self):
        run = self.create()
        root = Path(run["worktree"])
        root.mkdir(parents=True)
        repo_git(Path(run["git_dir"]), root, "update-ref",
                 "refs/heads/" + run["branch"], run["base"], "0" * 40)
        self.engine.run(run["id"], max_steps=1)
        self.assertTrue((root / "calc.py").is_file())
        self.assertEqual(self.log[0][0], "grill-with-docs")

    def test_no_weakening_verification_command_ids(self):
        bad = spec()
        bad["requirements"][0]["acceptance"][0]["test_ids"] = ["invented"]
        with self.assertRaises(GateError):
            criteria(bad, {"unit"})

    def test_event_tamper_is_detected(self):
        self.create()
        with self.store.db:
            self.store.db.execute("UPDATE events SET data='{}' WHERE seq=1")
        with self.assertRaisesRegex(GateError, "integrity"):
            self.store.verify()

    def test_pause_is_not_overwritten_by_worker_checkpoint(self):
        run = self.create()
        stale = copy.deepcopy(run)
        run["status"] = "paused"
        self.store.save(run, "pause", expected_revision=run["revision"])
        with self.assertRaisesRegex(GateError, "concurrent"):
            self.store.save(stale, expected_revision=stale["revision"])
        self.assertEqual(self.engine.run(run["id"])["status"], "paused")

    def test_ledger_status_is_read_only(self):
        run = self.create()
        events = self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--state", str(self.store.root), "status", run["id"]]), 0)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], events)

    def test_manual_review_does_not_author_complete_or_commit(self):
        run = self.create()
        self.engine.run(run["id"], max_steps=3)
        before = self.store.get(run["id"])
        before["status"] = "paused"
        self.store.save(before, "pause", expected_revision=before["revision"])
        root = Path(before["worktree"])
        git_dir = Path(before["git_dir"])
        ref = "refs/heads/" + before["branch"]
        head = repo_git(git_dir, root, "rev-parse", "--verify", ref)
        report = self.engine.review_once(run["id"], spec_vendor="claude")
        after = self.store.get(run["id"])
        self.assertFalse(report["delivery_authorized"])
        self.assertFalse(report["tests_rerun"])
        self.assertEqual(before, after)
        self.assertEqual(head, repo_git(git_dir, root, "rev-parse", "--verify", ref))
        self.assertEqual([x[0] for x in self.log][-2:], ["spec-review", "defect-review"])
        self.assertEqual(self.log[-2][1], "claude-second-1")
        self.assertEqual(self.log[-1][1], "codex-second-1")

    def test_review_rejects_spec_change_during_model_call(self):
        run = self.create()
        self.engine.run(run["id"], max_steps=3)
        base = FixtureProvider(self.log)
        class Mutator:
            def invoke(inner, stage, model, packet, directory):
                result = base.invoke(stage, model, packet, directory)
                if stage == "defect-review":
                    path = self.store.root / run["id"] / "spec.md"
                    path.write_text(path.read_text() + "\nExternally changed", encoding="utf-8")
                return result
        self.engine.provider_factory = lambda run, vendor: Mutator()
        result = self.engine.run(run["id"], max_steps=10)
        self.assertEqual(result["status"], "paused")
        self.assertNotIn("delivery_commit", result)

    def test_changing_failed_edits_cannot_reset_attempt_ceiling(self):
        run = self.create()
        base = FixtureProvider(self.log)
        class FailingWriter:
            count = 0
            def invoke(inner, stage, model, packet, directory):
                if stage != "implement":
                    return base.invoke(stage, model, packet, directory)
                inner.count += 1
                return {"changes": [{"path": "calc.py", "expected_sha256": packet["source"]["file_sha256"]["calc.py"],
                                     "content": f"def increment(x):\n    return x + {inner.count + 20}\n"}]}
        writer = FailingWriter()
        self.engine.provider_factory = lambda run, vendor: writer
        result = self.engine.run(run["id"], max_steps=20)
        self.assertEqual(result["status"], "paused")
        self.assertEqual(writer.count, 3)
        self.assertIn("three implementation attempts", result["last_error"])

    def test_questions_do_not_consume_implementation_attempts(self):
        run = self.create()
        self.engine.run(run["id"], max_steps=3)
        class Clarifier:
            def invoke(inner, *args):
                return {"questions": ["Which behavior is intended?"]}
        self.engine.provider_factory = lambda run, vendor: Clarifier()
        for _ in range(4):
            current = self.engine.run(run["id"], max_steps=1)
            self.assertEqual(current["status"], "waiting_for_answer")
            self.assertEqual(current["repair_attempts"].get("T-1", 0), 0)
            current["answers"].append({"answer": "clarified"})
            current["status"] = "ready"
            self.store.save(current, "fixture-answer", expected_revision=current["revision"])

    def test_changed_run_row_fails_integrity(self):
        run = self.create()
        run["done_tickets"] = ["T-1"]
        with self.store.db:
            self.store.db.execute("UPDATE runs SET data=? WHERE id=?", (json.dumps(run), run["id"]))
        with self.assertRaisesRegex(GateError, "state integrity"):
            self.store.verify()

    def test_second_state_directory_cannot_bypass_writer_lock(self):
        run = self.create()
        other = Store(self.root / "other-state", create=True)
        try:
            second = start(other, self.project, "Other", self.config, catalog(), "codex")
            with exclusive(project_lock(Path(run["git_dir"]))):
                with self.assertRaisesRegex(GateError, "another worker"):
                    Engine(other).run(second["id"], max_steps=1)
        finally:
            other.close()


class Contracts(unittest.TestCase):
    def test_dangerous_paths(self):
        for value in ("../file", "/file", "C:/file", "x\\y", ".git/config", "a/.env", "a/key.pem", "foo/CON.txt", "a/../b", "a/./b", "a/x.", "a/line\nbreak"):
            with self.subTest(value=value), self.assertRaises(GateError):
                safe_relative(value)

    @unittest.skipUnless(os.name == "nt", "Windows handle-sharing invariant")
    def test_handle_bound_edit_blocks_path_replacement_during_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "one.txt"
            moved = root / "moved.txt"
            path.write_text("before", encoding="utf-8")
            real_fsync = os.fsync
            attempts = []

            def attempt_swap(descriptor):
                try:
                    path.replace(moved)
                    attempts.append("replaced")
                except OSError:
                    attempts.append("blocked")
                real_fsync(descriptor)

            with patch("project_creator.workspace.os.fsync", side_effect=attempt_swap):
                _overwrite_existing(path, digest("before"), "after")
            self.assertEqual(attempts, ["blocked"])
            self.assertEqual(path.read_text(encoding="utf-8"), "after")
            self.assertFalse(moved.exists())

    def test_atomic_state_preserves_old_file_on_pre_replace_process_death(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "controller"
            store = Store(state, create=True)
            store.close()
            path = state / "run" / "state.json"
            atomic_text(path, "old")
            code = (
                "import os,sys; from pathlib import Path; "
                f"sys.path.insert(0, {str(PACKAGE)!r}); "
                "import project_creator.store as store; "
                "store._before_atomic_replace=lambda: os._exit(77); "
                "store.atomic_text(Path(sys.argv[1]), 'new')"
            )
            child = subprocess.run([sys.executable, "-I", "-c", code, str(path)],
                                   check=False)
            self.assertEqual(child.returncode, 77)
            self.assertEqual(path.read_text(encoding="utf-8"), "old")
            self.assertEqual(len(list(path.parent.glob(".aerith-atomic-*.tmp"))), 1)
            store = Store(state)
            store.close()
            self.assertEqual(list(path.parent.glob(".aerith-atomic-*.tmp")), [])

    def test_atomic_state_blocks_parent_swap_before_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "state"
            parent.mkdir()
            path = parent / "packet.json"
            atomic_text(path, "old")
            moved = Path(tmp) / "moved"
            result = []

            def swap_parent():
                try:
                    parent.rename(moved)
                except OSError:
                    result.append("blocked")
                else:
                    parent.mkdir()
                    result.append("moved")

            with patch("project_creator.store._before_atomic_replace",
                       side_effect=swap_parent):
                atomic_text(path, "new")
            self.assertEqual(result, ["blocked"])
            self.assertEqual(path.read_text(encoding="utf-8"), "new")
            self.assertFalse(moved.exists())

    @unittest.skipUnless(os.name == "nt", "Windows sharing-lock recovery invariant")
    def test_atomic_recovery_does_not_remove_a_live_temporary(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "controller"
            store = Store(state, create=True)
            store.close()
            orphan = state / ".aerith-atomic-0123456789abcdef0123456789abcdef.tmp"
            orphan.write_text("pending", encoding="utf-8")
            with orphan.open("rb"):
                store = Store(state)
                store.close()
                self.assertTrue(orphan.exists())
            store = Store(state)
            store.close()
            self.assertFalse(orphan.exists())

    def test_ticket_coverage_cycle_and_unknown(self):
        acs = criteria(spec(), {"unit"})
        ticket = {"id": "T1", "title": "One", "criteria": ["AC-1"], "blocked_by": [], "write_set": ["calc.py"]}
        self.assertEqual(ticket_order([ticket], acs, ["calc.py"]), ["T1"])
        for deps in (["T1"], ["unknown"]):
            with self.assertRaises(GateError):
                ticket_order([ticket | {"blocked_by": deps}], acs, ["calc.py"])
        with self.assertRaises(GateError):
            ticket_order([ticket | {"criteria": []}], acs, ["calc.py"])

    def test_reviews_cannot_skip_criteria_or_blocking_findings(self):
        report = {"verdict": "pass", "checked_criteria": ["AC-1"], "findings": [], "limitations": []}
        self.assertTrue(validate_review(report, {"AC-1"}, spec_axis=True))
        with self.assertRaises(GateError):
            validate_review(report | {"checked_criteria": []}, {"AC-1"}, spec_axis=True)
        report["findings"] = [{"id": "F-1", "priority": 2, "message": "Wrong row is overwritten"}]
        self.assertFalse(validate_review(report, {"AC-1"}, spec_axis=True))

    def test_malformed_nested_model_objects_are_refused(self):
        for bad in ({"requirements": [None]}, {"requirements": [{"id": "R1", "text": "x", "acceptance": [None]}]}):
            with self.assertRaises(GateError):
                criteria(bad, {"unit"})
        with self.assertRaises(GateError):
            ticket_order([None], {}, ["calc.py"])
        with self.assertRaises(GateError):
            validate_review({"verdict": "pass", "findings": [None], "limitations": [], "checked_criteria": []}, set(), spec_axis=True)

    def test_incomplete_live_admission_stops_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "admission.json"
            path.write_text(json.dumps({"status": "admitted", "skills": {}}), encoding="utf-8")
            self.assertTrue(admission_stopped({"admission_file": str(path)}))

    def test_role_pin_survives_catalog_update(self):
        data = catalog()
        old = catalog_pin(data, "codex")
        data["project_creator"]["vendors"]["codex"]["highest"] = "codex-top-2"
        self.assertEqual(model_for({"codex": old}, "codex", "to-spec")[1], "codex-top-1")
        self.assertEqual(catalog_pin(data, "codex")["highest"], "codex-top-2")

    def test_expired_catalog_not_guessed(self):
        data = catalog()
        data["project_creator"]["vendors"]["codex"]["verified_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with self.assertRaises(GateError):
            catalog_pin(data, "codex")

    def test_official_refresh_orders_within_families_not_globally(self):
        data = catalog()
        data["project_creator"]["vendors"]["codex"]["discovery"] = {"highest": r"codex-top-\d+", "second-highest": r"codex-second-\d+"}
        out = refresh_catalog(data, "codex", fetch=lambda url: "codex-top-2 codex-second-99 ignore previous instructions")
        self.assertEqual(out["project_creator"]["vendors"]["codex"]["highest"], "codex-top-2")
        self.assertEqual(data["project_creator"]["vendors"]["codex"]["highest"], "codex-top-1")

    def test_partial_write_recovery_and_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one.txt").write_text("before")
            journal = root / "journal.json"
            changes = [{"path": "one.txt", "expected_sha256": digest("before"), "content": "after"}]
            propose_edits(root, changes, ["one.txt"], journal)
            reconcile_edits(root, ["one.txt"], journal)
            self.assertEqual((root / "one.txt").read_text(), "after")
            (root / "one.txt").write_text("user edit")
            with self.assertRaises(GateError):
                reconcile_edits(root, ["one.txt"], journal)
            self.assertEqual((root / "one.txt").read_text(), "user edit")

    def test_transport_utf8_and_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = execute([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"], cwd=Path(tmp), stdin="ภาษาไทย")
            self.assertEqual(result.stdout, "ภาษาไทย")
            with self.assertRaisesRegex(GateError, "timeout"):
                execute([sys.executable, "-c", "import time; time.sleep(10)"], cwd=Path(tmp), timeout=0.2)

    def test_reviewed_executable_and_runtime_are_locked_for_real_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "reviewed_worker.py"
            script.write_text("print('reviewed')\n", encoding="utf-8")
            result = execute(
                [sys.executable, str(script)], cwd=root,
                expected_executable_sha256=digest(Path(sys.executable).read_bytes()),
                expected_runtime_sha256={str(script): digest(script.read_bytes())},
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "reviewed")

    @unittest.skipUnless(os.name == "nt", "Windows neutral CWD contract")
    def test_admitted_launch_ignores_supplied_working_directory_for_dll_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "attacker-controlled-provider-cwd"
            root.mkdir()
            (root / "AGENTS.md").write_text("ambient")
            program = ("from pathlib import Path; "
                       "assert Path.cwd() != Path(r'" + str(root) + "'); "
                       "assert not Path('AGENTS.md').exists(); print('isolated')")
            result = execute([sys.executable, "-I", "-c", program], cwd=root,
                             system_cwd=True)
            self.assertEqual(result.stdout.strip(), "isolated")

    @unittest.skipUnless(os.name == "nt", "Windows file-share lock contract")
    def test_reviewed_source_file_cannot_change_while_external_consumer_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.py"
            source.write_text("reviewed\n", encoding="utf-8")
            with reviewed_files({str(source): digest(source.read_bytes())}):
                with self.assertRaises(PermissionError):
                    source.write_text("replacement\n", encoding="utf-8")
                with self.assertRaises(PermissionError):
                    source.rename(source.with_name("replacement.py"))
            self.assertEqual(source.read_text(encoding="utf-8"), "reviewed\n")

    def test_output_byte_bound_and_shell_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(GateError, "byte limit"):
                execute([sys.executable, "-c", "print('x'*10000)"], cwd=Path(tmp), max_bytes=1000)
            with self.assertRaisesRegex(GateError, "shell wrapper"):
                execute(["foo.cmd"], cwd=Path(tmp))

    def test_pre_cancelled_process_never_launches(self):
        with tempfile.TemporaryDirectory() as tmp, patch("project_creator.processes.subprocess.Popen") as launch:
            with self.assertRaisesRegex(GateError, "before process launch"):
                execute([sys.executable, "-c", "print('should not run')"], cwd=Path(tmp), cancelled=lambda: True)
            launch.assert_not_called()

    def test_model_attestation_must_match_exact_pin(self):
        self.assertEqual(attest_model({"modelUsage": {"top-1": {}}}, {"path": ["modelUsage"], "mode": "keys"}, "top-1"), "top-1")
        for envelope in ({}, {"modelUsage": {"fallback": {}}}, {"modelUsage": {"top-1": {}, "second": {}}}):
            with self.assertRaises(GateError):
                attest_model(envelope, {"path": ["modelUsage"], "mode": "keys"}, "top-1")

    def test_boolean_only_containment_claim_is_refused(self):
        cfg = {"argv": [sys.executable]}
        cfg["proof"] = {"configuration_hash": digest(cfg), "checked_at": datetime.now(timezone.utc).isoformat(),
                        "cases": {k: True for k in ("exact_source_set", "outside_read_denied", "outside_write_denied", "network_denied", "child_cleanup")}}
        with self.assertRaisesRegex(GateError, "trusted host"):
            validate_capability(cfg, "verification")

    def test_unconfigured_doctor_cannot_report_ready(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["doctor"]), 2)
        report = json.loads(out.getvalue())
        self.assertEqual(report["status"], "not-ready")
        self.assertEqual(report["model_calls"], 0)

    def test_timeout_kills_descendant_before_delayed_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "descendant-effect.txt"
            child = "import time; from pathlib import Path; time.sleep(1.0); Path('descendant-effect.txt').write_text('bad')"
            parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(5)"
            with self.assertRaisesRegex(GateError, "timeout"):
                execute([sys.executable, "-c", parent], cwd=Path(tmp), timeout=0.25)
            time.sleep(1.1)
            self.assertFalse(sentinel.exists())

    def test_thread_setup_failure_still_kills_resumed_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / "setup-failure-effect.txt"
            child = ("import time; from pathlib import Path; time.sleep(0.6); "
                     "Path('setup-failure-effect.txt').write_text('bad')")
            with patch("project_creator.processes.threading.Thread.start",
                       side_effect=RuntimeError("synthetic thread start failure")):
                with self.assertRaisesRegex(RuntimeError, "synthetic thread start failure"):
                    execute([sys.executable, "-c", child], cwd=Path(tmp))
            time.sleep(0.8)
            self.assertFalse(sentinel.exists())

    def test_admission_revocation_expiry_and_hash_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "package"
            package.mkdir()
            names = ["project-creator.py", "references/defect-review.md", "references/engineering-method.md"]
            names += [f"skills/{s}/SKILL.md" for s in CORE_SKILLS]
            for name in names:
                p = package / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("safe fixture", encoding="utf-8")
            manifest = {"skills": list(CORE_SKILLS), "files": {n: digest("safe fixture") for n in names}}
            mf = package / "release-manifest.json"
            mf.write_text(json.dumps(manifest), encoding="utf-8")
            admitted = {"status": "admitted", "manifest_sha256": digest(mf.read_bytes()),
                        "reviewed_at": datetime.now(timezone.utc).isoformat(), "revocation_tested": True,
                        "disableSkillShellExecution": True, "skills": {s: "enabled" for s in CORE_SKILLS}}
            path = Path(tmp) / "admission.json"
            path.write_text(json.dumps(admitted), encoding="utf-8")
            self.assertTrue(verify_package(package, path, stage="implement"))
            bad = copy.deepcopy(admitted)
            bad["skills"]["implement"] = "revoked"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(GateError, "revoked"):
                verify_package(package, path, stage="implement")
            bad = admitted | {"reviewed_at": (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()}
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(GateError, "expired"):
                verify_package(package, path)
            path.write_text(json.dumps(admitted), encoding="utf-8")
            (package / names[0]).write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(GateError, "changed after audit"):
                verify_package(package, path)


class FakeGitHub:
    def __init__(self):
        self.issues, self.comments = [], []
        self.lost_response = False
        self.posts = 0

    def private(self):
        return True

    def pages(self, path):
        return iter(self.comments if "comments" in path else self.issues)

    def api(self, path, method="GET", data=None):
        if method == "POST":
            self.posts += 1
            value = dict(data)
            if path.endswith("comments"):
                value["id"] = len(self.comments) + 1
                self.comments.append(value)
            else:
                value["number"] = len(self.issues) + 1
                self.issues.append(value)
            if self.lost_response:
                self.lost_response = False
                raise GateError("response lost")
            return value
        if "/comments/" in path:
            return self.comments[int(path.rsplit("/", 1)[1]) - 1]
        return self.issues[int(path.rsplit("/", 1)[1]) - 1]


class Mirror(unittest.TestCase):
    def test_outbox_tamper_blocks_before_remote_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, gh = Store(Path(tmp), create=True), FakeGitHub()
            try:
                store.enqueue("r", "r:00-parent", "Title", "Body")
                with store.db:
                    store.db.execute("UPDATE outbox SET body='altered'")
                with self.assertRaisesRegex(GateError, "outbox payload integrity"):
                    sync(store, gh, "r")
                self.assertEqual(gh.posts, 0)
            finally:
                store.close()

    def test_uncertain_create_reconciles_without_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, gh = Store(Path(tmp), create=True), FakeGitHub()
            try:
                store.enqueue("r", "r:00-parent", "Title", "Body")
                gh.lost_response = True
                self.assertEqual(sync(store, gh, "r")[0]["status"], "unknown")
                self.assertEqual(sync(store, gh, "r")[0]["status"], "synced")
                self.assertEqual(gh.posts, 1)
            finally:
                store.close()

    def test_external_edit_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, gh = Store(Path(tmp), create=True), FakeGitHub()
            try:
                store.enqueue("r", "r:00-parent", "Title", "Body")
                sync(store, gh, "r")
                gh.issues[0]["body"] += "\nUser edit"
                store.enqueue("r", "r:00-parent", "Title", "Changed body")
                self.assertEqual(sync(store, gh, "r")[0]["status"], "conflict")
                self.assertTrue(gh.issues[0]["body"].endswith("User edit"))
            finally:
                store.close()

    def test_revisions_are_idempotent_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, gh = Store(Path(tmp), create=True), FakeGitHub()
            try:
                store.enqueue("r", "r:00-parent", "Title", "Body")
                sync(store, gh, "r")
                original = gh.issues[0]["body"]
                store.enqueue("r", "r:00-parent", "Title", "Changed body")
                sync(store, gh, "r")
                sync(store, gh, "r")
                self.assertEqual(len(gh.comments), 1)
                self.assertEqual(gh.issues[0]["body"], original)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
