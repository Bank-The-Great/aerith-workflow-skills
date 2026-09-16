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
from project_creator.models import accept_pin, catalog_pin, declare_pin, model_for, refresh_catalog
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
        # REQ-LC-021: a run never acquires its models by default. Tests about other
        # behaviour take the catalog-confirmed path an operator would take.
        kwargs.setdefault("accept_catalog", True)
        return start(self.store, self.project, "Make increment add one", self.config, catalog(), "codex", **kwargs)


    def test_a_run_never_acquires_its_models_by_default(self):
        # REQ-LC-021. The refusal is the point: before D10 a run took whatever the catalog
        # proposed, and nobody had said so.
        with self.assertRaisesRegex(GateError, "accept the catalog pins"):
            start(self.store, self.project, "No models chosen", self.config, catalog(), "codex")

    def test_declared_models_replace_the_catalog_proposal_and_are_recorded(self):
        run = self.create(accept_catalog=False,
                          models={"codex": {"highest": "codex-chosen-1", "second-highest": "codex-chosen-2"}})
        self.assertEqual(run["pins"]["codex"]["highest"], "codex-chosen-1")
        self.assertEqual(run["pins"]["codex"]["second-highest"], "codex-chosen-2")
        self.assertEqual(run["pins"]["codex"]["declared_by"], "operator")
        self.assertEqual(run["model_declaration"]["vendors"]["codex"], "operator")
        self.assertTrue(run["model_declaration"]["at"])
        # A vendor the operator did not name stays unusable rather than inheriting a pin.
        self.assertIsNone(run["pins"].get("claude", {}).get("declared_by"))
        with self.assertRaisesRegex(GateError, "no operator-declared model profile"):
            model_for(run["pins"], "claude", "to-spec")

    def test_accepting_the_catalog_covers_the_handoff_pins_it_shows(self):
        run = self.create()
        for vendor, pin in run["pins"].items():
            self.assertEqual(pin["declared_by"], "catalog-confirmed", vendor)
            self.assertEqual(run["model_declaration"]["vendors"][vendor], "catalog-confirmed")

    def test_declaration_cannot_name_a_vendor_the_run_has_no_pin_for(self):
        with self.assertRaisesRegex(GateError, "no pin for"):
            self.create(models={"nowhere": {"highest": "a", "second-highest": "b"}})
        with self.assertRaisesRegex(GateError, "both tiers"):
            self.create(models={"codex": "codex-chosen-1"})

    def test_the_panel_names_every_configured_provider_not_a_filtered_subset(self):
        # R23-SEC-04: the old filter read a field that does not govern dispatch, so it could omit
        # the provider about to be called. Every configured provider is announced.
        run = self.create()
        run["config"] = dict(run["config"], providers={"codex": "unreadable", "claude": "unreadable"})
        self.store.save(run, "providers_for_panel", expected_revision=run["revision"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["--state", str(self.store.root), "run", run["id"]])
        # The closing status line names every pinned vendor too, so asserting over all of
        # stdout passed even when the panel was filtered. Decode the panel document alone.
        panel, _ = json.JSONDecoder().raw_decode(out.getvalue())
        self.assertEqual(sorted(panel["provider_attestation"]), ["claude", "codex"])

    def test_every_dispatching_command_announces_the_provider_panel_first(self):
        # R22-SPEC-04 / R22-SEC-12 / R23-SPEC-01: the panel existed only at start, so a run
        # resumed weeks later dispatched against a proof whose age the operator had last seen
        # when it was fresh; `review`, which spends two calls per invocation, had none at all.
        run = self.create()
        for command, entry in (("run", "run"), ("resume", "run"), ("review", "review_once")):
            with self.subTest(command):
                out = io.StringIO()
                seen = {}
                original = getattr(Engine, entry)

                def spy(engine, *args, _original=original, **kwargs):
                    # R23-SPEC-07, second attempt: the first version compared the panel against
                    # the command's closing line, which prints last either way, so it could not
                    # see the announce move past the dispatch. This reads stdout AT the dispatch.
                    seen["at_dispatch"] = out.getvalue()
                    return _original(engine, *args, **kwargs)

                with contextlib.redirect_stdout(out), patch.object(Engine, entry, spy):
                    main(["--state", str(self.store.root), command, run["id"]])
                self.assertIn("provider_attestation", seen.get("at_dispatch", ""),
                              "the panel must already be printed when the engine is entered")

    def test_cli_start_announces_the_panel_before_the_engine_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps(self.config))
            catalog_file = Path(tmp) / "catalog.json"
            catalog_file.write_text(json.dumps(catalog()))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["--state", str(Path(tmp) / "state"), "start", "--project", str(self.project),
                      "--config", str(config), "--catalog", str(catalog_file), "--vendor", "codex",
                      "--objective", "announce the panel", "--accept-catalog-pins"])
            printed = out.getvalue()
            self.assertIn("provider_attestation", printed)
            self.assertIn("model_declaration", printed)
            self.assertLess(printed.index("provider_attestation"), printed.index('"status"'))

    def test_cli_refuses_spec_model_flags_without_a_distinct_spec_vendor(self):
        # REQ-LC-021: the spec tiers belong to a second vendor. Without one they would silently
        # overwrite the author's declaration, which is the shape this refusal exists to stop.
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps(self.config))
            catalog_file = Path(tmp) / "catalog.json"
            catalog_file.write_text(json.dumps(catalog()))
            argv = ["--state", str(Path(tmp) / "state"), "start", "--project", str(self.project),
                    "--config", str(config), "--catalog", str(catalog_file), "--vendor", "codex",
                    "--objective", "declare the models", "--spec-model-highest", "a",
                    "--spec-model-second", "b"]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(argv), 2)
            self.assertIn("different from --vendor", json.loads(out.getvalue())["reason"])

    @unittest.skipUnless(os.name == "nt", "the reviewed-path lock is a Windows contract")
    def test_execute_locks_the_reviewed_file_before_it_launches_anything(self):
        """R23-SPEC-09, first asked for at R21-SEC-02: the helper's refusals were proven, the
        call site's ordering was not, and the ordering is the whole claim."""
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "reviewed_worker.py"
            script.write_text("print('reviewed')\n", encoding="utf-8")
            with patch("subprocess.Popen") as popen:
                with self.assertRaisesRegex(GateError, "reviewed file changed before launch"):
                    execute([sys.executable, str(script)], cwd=Path(tmp),
                            expected_executable_sha256="0" * 64)
            popen.assert_not_called()

    def test_doctor_carries_the_gate_verdict_into_the_panel_it_prints(self):
        # R22-SEC-01: a panel printed beside a refusal must not read as a clean bill of health.
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"providers": {"codex": {"output": "codex-data-only"}}}))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["doctor", "--config", str(config)]), 2)
            report = json.loads(out.getvalue())
            self.assertIn("codex", report["checks"])
            self.assertNotEqual(report["checks"]["codex"], "proof-current")
            self.assertIs(report["provider_attestation"]["codex"]["verified"], False)

    def test_doctor_survives_a_malformed_provider_entry(self):
        # R22-SPEC-06 / R22-SEC-05b: both of these used to leave doctor with a traceback, because
        # neither AttributeError nor a non-mapping record was handled anywhere on the path.
        for label, entry in (("record is a string", "oops"), ("proof is not a mapping", {"proof": []})):
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                config = Path(tmp) / "config.json"
                config.write_text(json.dumps({"providers": {"codex": entry}}))
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(main(["doctor", "--config", str(config)]), 2)
                report = json.loads(out.getvalue())
                self.assertNotEqual(report["checks"]["codex"], "proof-current")
                self.assertIs(report["provider_attestation"]["codex"]["available"], False)
                self.assertIs(report["provider_attestation"]["codex"]["verified"], False)
                self.assertTrue(report["provider_attestation"]["codex"]["reason"])

    def test_panel_handles_a_pinned_record_whose_proof_is_not_a_mapping(self):
        # The CLI cases above never reach this path: an unpinned record is refused by the host
        # pin first. Only a record the host HAS pinned can take the gate as far as reading the
        # proof, so this is the one input that exercises the AttributeError branch.
        from project_creator.cli import provider_panel
        cfg = {"argv": [sys.executable, "--model", "{model}"], "output": "codex-data-only",
               "filesystem_scope": "codex-home-auth-only",
               "loader_policy": "pe-dependent-load-system32", "auth_mode": "codex-subscription",
               "attestation": {"mode": "recorded-response-model"}, "proof": []}
        with patch.dict("project_creator.providers._HOST_CAPABILITIES", {digest(cfg): "provider"}):
            verdict, facts = provider_panel(cfg)
        self.assertIn("unreadable", verdict)
        self.assertIs(facts["available"], False)
        self.assertIs(facts["verified"], False)
        self.assertIn("unreadable", facts["reason"])

    def test_doctor_reports_measured_attestation_beside_its_verdict(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["doctor"]), 2)
        # REQ-LC-022: the key is always present, so an operator never has to know whether the
        # display happened to be built for this run.
        self.assertEqual(json.loads(out.getvalue())["provider_attestation"], {})

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
            start(self.store, self.project, "Create a file", config, catalog(), "codex", accept_catalog=True)
        self.assertEqual(self.log, [])

    def test_cross_scope_case_collision_is_refused(self):
        config = copy.deepcopy(self.config)
        config["read_set"] = ["calc.py"]
        config["write_set"] = ["CALC.py"]
        with self.assertRaisesRegex(GateError, "case collision"):
            start(self.store, self.project, "Edit one file", config, catalog(), "codex", accept_catalog=True)

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
            second = start(other, self.project, "Other", self.config, catalog(), "codex", accept_catalog=True)
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
        old = accept_pin(catalog_pin(data, "codex"))
        data["project_creator"]["vendors"]["codex"]["highest"] = "codex-top-2"
        self.assertEqual(model_for({"codex": old}, "codex", "to-spec")[1], "codex-top-1")
        self.assertEqual(catalog_pin(data, "codex")["highest"], "codex-top-2")

    def test_undeclared_pin_is_not_usable(self):
        # REQ-LC-021: a pin the run carries for a later handoff is not a chosen model, so a
        # resume or review that switches to it must refuse rather than inherit it silently.
        pin = catalog_pin(catalog(), "codex")
        with self.assertRaisesRegex(GateError, "no operator-declared model profile"):
            model_for({"codex": pin}, "codex", "to-spec")
        self.assertEqual(model_for({"codex": accept_pin(pin)}, "codex", "to-spec")[1], "codex-top-1")
        declared = declare_pin(pin, "codex-chosen-1", "codex-chosen-2")
        self.assertEqual(model_for({"codex": declared}, "codex", "to-spec")[1], "codex-chosen-1")
        self.assertEqual(model_for({"codex": declared}, "codex", "implement")[1], "codex-chosen-2")
        # R22-SEC-09: an unhashable id must refuse as a GateError, not a TypeError from set().
        for highest, second in (("same", "same"), ("bad id", "other"), (None, "other"), ("ok", 5),
                                ([], []), ({"a": 1}, "other")):
            with self.subTest(highest=repr(highest)), self.assertRaisesRegex(GateError, "two distinct valid model ids"):
                declare_pin(pin, highest, second)

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

    @unittest.skipUnless(os.name == "nt", "the reviewed-path lock is a Windows contract")
    def test_reviewed_launch_lock_refuses_every_way_it_states(self):
        """R22-SPEC-01(b): the lock had only a success-path test, so none of its refusals was
        exercised. This is what makes "the executable we proved" hold at the moment of launch."""
        from project_creator.processes import _reviewed_locks
        shaped = "a" * 64
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "reviewed.bin"
            target.write_bytes(b"reviewed bytes")
            real = digest(target.read_bytes())
            argv = [str(target)]
            cases = (
                ("closure is not a mapping", (real, [], argv), "invalid reviewed runtime closure"),
                ("closure name is not a string", (real, {1: shaped}, argv), "invalid reviewed runtime closure"),
                ("closure hash is not a digest", (real, {str(target): "nope"}, argv),
                 "invalid reviewed runtime closure"),
                ("two hashes for one file", (real, {str(target): shaped}, argv),
                 "conflicting reviewed file hashes"),
                ("relative reviewed path", (real, {"relative.bin": shaped}, argv),
                 "reviewed runtime path is not immutable"),
                ("file changed before launch", (shaped, {}, argv), "reviewed file changed before launch"),
            )
            for label, args, message in cases:
                with self.subTest(label), self.assertRaisesRegex(GateError, message):
                    locks = _reviewed_locks(*args)
                    for lock in locks:
                        lock.close()

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
