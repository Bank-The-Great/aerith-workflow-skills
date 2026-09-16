"""Synthetic workflow E2E. This is not live-provider or host-admission proof."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_creator.contracts import digest, parse_artifact
from project_creator.engine import Engine, RESOURCE_FILES, configure_runtime_resources, start
from project_creator.store import Store
from project_creator.workspace import configure_git, git

PACKAGE = Path(__file__).resolve().parents[1]
GIT_EXE = Path(os.environ.get(
    "PROJECT_CREATOR_TEST_GIT", r"C:\Program Files\Git\mingw64\bin\git.exe"))
configure_git({"executable": str(GIT_EXE), "sha256": digest(GIT_EXE.read_bytes())})
configure_runtime_resources({name: (PACKAGE / name).read_bytes()
                             for name in RESOURCE_FILES.values()})


def catalog():
    return {"project_creator": {"vendors": {
        "codex": {"highest": "synthetic-codex-high", "second-highest": "synthetic-codex-second",
                  "verified_at": datetime.now(timezone.utc).isoformat(),
                  "source": "https://developers.openai.com/api/docs/models"},
        "claude": {"highest": "claude-high", "second-highest": "claude-second",
                   "verified_at": datetime.now(timezone.utc).isoformat(),
                   "source": "https://platform.claude.com/docs/en/models/overview"},
    }}}


def spec():
    return {"title": "Two sequential slices", "non_goals": ["network"], "requirements": [
        {"id": "REQ-1", "text": "increment", "acceptance": [
            {"id": "AC-1", "text": "increment adds one", "test_ids": ["unit-calc"]}]},
        {"id": "REQ-2", "text": "report", "acceptance": [
            {"id": "AC-2", "text": "report uses increment", "test_ids": ["unit-report"]}]},
    ]}


class Scenario:
    def __init__(self):
        self.calls = []
        self.contexts = 0
        self.first_ticket_spec_reviews = 0

    def provider(self, run, vendor):
        self.contexts += 1
        context = self.contexts
        scenario = self

        class Provider:
            def invoke(self, stage, model, packet, directory):
                scenario.calls.append({"stage": stage, "vendor": vendor, "model": model,
                                       "context": context, "packet": copy.deepcopy(packet)})
                if stage == "grill-with-docs":
                    return {"brief": {"summary": "two slices", "decisions": [], "glossary": {}}, "questions": []}
                if stage == "to-spec":
                    return {"spec": spec(), "questions": []}
                if stage == "to-tickets":
                    return {"tickets": [
                        {"id": "T-1", "title": "Increment", "criteria": ["AC-1"],
                         "blocked_by": [], "write_set": ["calc.py"]},
                        {"id": "T-2", "title": "Report", "criteria": ["AC-2"],
                         "blocked_by": ["T-1"], "write_set": ["report.py"]},
                    ], "questions": []}
                if stage == "implement":
                    ticket = packet["ticket"]["id"]
                    if ticket == "T-1":
                        return {"changes": [{"path": "calc.py",
                            "expected_sha256": packet["source"]["file_sha256"]["calc.py"],
                            "content": "def increment(x):\n    return x + 1\n"}], "questions": []}
                    return {"changes": [{"path": "report.py",
                        "expected_sha256": packet["source"]["file_sha256"]["report.py"],
                        "content": "from calc import increment\n\ndef report(x):\n    return str(increment(x))\n"}], "questions": []}
                checked = packet["criteria_in_scope"]
                if stage == "spec-review" and packet["ticket"] and packet["ticket"]["id"] == "T-1":
                    scenario.first_ticket_spec_reviews += 1
                    if scenario.first_ticket_spec_reviews == 1:
                        return {"verdict": "fail", "checked_criteria": checked, "limitations": [],
                                "findings": [{"id": "F-P2", "priority": 2, "path": "calc.py", "line": 2,
                                              "message": "Synthetic blocking finding forces a repair pass."}]}
                findings = []
                if stage == "defect-review":
                    findings = [{"id": "F-P3", "priority": 3, "path": "calc.py", "line": 1,
                                 "message": "Synthetic non-blocking cleanup note.",
                                 "disposition": "Accepted for this bounded pilot."}]
                return {"verdict": "pass", "checked_criteria": checked,
                        "findings": findings, "limitations": []}
        return Provider()


class Verifier:
    def run(self, ids, root, *, expected_files=None):
        results = []
        for test_id in sorted(set(ids)):
            passed = (root / "calc.py").read_text(encoding="utf-8") == "def increment(x):\n    return x + 1\n"
            if test_id == "unit-report":
                passed = passed and (root / "report.py").read_text(encoding="utf-8") == (
                    "from calc import increment\n\ndef report(x):\n    return str(increment(x))\n")
            output = "synthetic-pass" if passed else "synthetic-fail"
            results.append({"test_id": test_id, "exit_code": 0 if passed else 1,
                            "diagnostic": output, "stdout_sha256": digest(output),
                            "stderr_sha256": digest(""), "elapsed_seconds": 0.0})
        return results


class WorkflowE2E(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="project-creator-e2e-")
        self.root = Path(self.temp.name)
        self.project = self.root / "source"
        self.project.mkdir()
        subprocess.run([str(GIT_EXE), "init", "-q", str(self.project)], check=True)
        git(self.project, "config", "user.name", "Fixture")
        git(self.project, "config", "user.email", "fixture@example.invalid")
        (self.project / "calc.py").write_text("def increment(x):\n    return x\n", encoding="utf-8")
        (self.project / "report.py").write_text("", encoding="utf-8")
        git(self.project, "add", "calc.py", "report.py")
        git(self.project, "commit", "-qm", "fixture base")
        self.store = Store(self.root / "state", create=True)
        self.config = {"read_set": ["calc.py", "report.py"],
                       "write_set": ["calc.py", "report.py"],
                       "tests": {"unit-calc": ["python", "test_calc.py"],
                                 "unit-report": ["python", "test_report.py"]}}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_sequential_fix_rereview_and_fresh_integrated_delivery(self):
        scenario = Scenario()
        engine = Engine(self.store, provider_factory=scenario.provider,
                        verifier_factory=lambda run: Verifier(),
                        admission_check=lambda run, stage: {"synthetic_fixture": True})
        run = start(self.store, self.project, "Build two slices", self.config,
                    catalog(), "codex", spec_vendor="claude", accept_catalog=True)
        result = engine.run(run["id"], max_steps=30)
        self.assertEqual(result["status"], "completed", result.get("last_error"))
        self.assertEqual(result["done_tickets"], ["T-1", "T-2"])
        self.assertEqual(result["repair_attempts"], {"T-1": 2, "T-2": 1})
        self.assertEqual(result["review_sequence"], 4)
        self.assertIsNone(result["active_review"])
        self.assertEqual((self.project / "calc.py").read_text(encoding="utf-8"), "def increment(x):\n    return x\n")
        self.assertNotEqual(result["base"], result["delivery_commit"])

        tickets = []
        for ticket_id, expected_hash in result["ticket_artifacts"].items():
            path = self.store.root / run["id"] / "tickets" / (ticket_id + ".md")
            self.assertEqual(digest(path.read_bytes()), expected_hash)
            tickets.append(parse_artifact(path.read_text(encoding="utf-8")))
        self.assertEqual([ticket["id"] for ticket in tickets], ["T-1", "T-2"])

        reviews = [call for call in scenario.calls if call["stage"].endswith("review")]
        self.assertEqual(len({call["context"] for call in scenario.calls}), len(scenario.calls))
        first_ticket_specs = [call for call in reviews if call["stage"] == "spec-review"
                              and call["packet"]["ticket"] and call["packet"]["ticket"]["id"] == "T-1"]
        self.assertEqual(len(first_ticket_specs), 2)
        self.assertNotEqual(first_ticket_specs[0]["packet"]["review_attempt"]["id"],
                            first_ticket_specs[1]["packet"]["review_attempt"]["id"])
        self.assertTrue(all(call["vendor"] == "claude" for call in reviews if call["stage"] == "spec-review"))
        self.assertTrue(all(call["vendor"] == "codex" for call in reviews if call["stage"] == "defect-review"))
        integrated = [call for call in reviews if call["packet"]["review_attempt"]["binding"]["review_kind"] == "integrated"]
        self.assertEqual([call["stage"] for call in integrated], ["spec-review", "defect-review"])
        self.assertTrue(all(call["packet"]["ticket"] is None for call in integrated))

        receipt_path = self.store.root / run["id"] / "receipts" / (result["last_receipt"] + ".json")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["evidence_binding"]["review_kind"], "integrated")
        self.assertEqual(receipt["evidence_binding"]["scope"], ["AC-1", "AC-2"])
        self.assertEqual(receipt["evidence_binding"]["tests_hash"], digest(receipt["tests"]))
        self.assertEqual(receipt["evidence_binding"]["ticket_artifacts"], result["ticket_artifacts"])
        self.assertEqual(receipt["review_attempt_id"], integrated[0]["packet"]["review_attempt"]["id"])
        p3 = receipt["reviews"]["defect-review"]["findings"][0]
        self.assertEqual(p3["priority"], 3)
        self.assertTrue(p3["disposition"])
        self.assertTrue(self.store.verify())

    def test_changed_per_ticket_artifact_invalidates_workflow(self):
        scenario = Scenario()
        engine = Engine(self.store, provider_factory=scenario.provider,
                        verifier_factory=lambda run: Verifier(),
                        admission_check=lambda run, stage: {"synthetic_fixture": True})
        run = start(self.store, self.project, "Build two slices", self.config, catalog(), "codex", accept_catalog=True)
        engine.run(run["id"], max_steps=3)
        ticket = self.store.root / run["id"] / "tickets" / "T-1.md"
        ticket.write_text(ticket.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
        result = engine.run(run["id"], max_steps=1)
        self.assertEqual(result["status"], "paused")
        self.assertIn("ticket artifact", result["last_error"])
        self.assertNotIn("delivery_commit", result)

    def test_standalone_ticket_stage_writes_canonical_files_and_stops(self):
        scenario = Scenario()
        engine = Engine(self.store, provider_factory=scenario.provider,
                        verifier_factory=lambda run: Verifier(),
                        admission_check=lambda run, stage: {"synthetic_fixture": True})
        config = copy.deepcopy(self.config)
        config["input_artifacts"] = {"spec": spec()}
        run = start(self.store, self.project, "Plan two slices", config, catalog(), "codex",
                    standalone="to-tickets", accept_catalog=True)
        result = engine.run(run["id"], max_steps=5)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([call["stage"] for call in scenario.calls], ["to-tickets"])
        self.assertEqual(sorted(result["ticket_artifacts"]), ["T-1", "T-2"])
        self.assertNotIn("delivery_commit", result)


if __name__ == "__main__":
    unittest.main()
