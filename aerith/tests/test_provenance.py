"""Synthetic protocol tests; these do not admit a runtime or prove live access."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_creator.contracts import GateError
from project_creator.provenance import parse_codex_provenance, parse_gemini_provenance


def codex():
    return [
        {"type": "thread.started", "thread_id": "fresh"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": '{"ok":true}'}},
        {"type": "aerith.response.completed", "thread_id": "fresh", "turn_id": "t1", "response_id": "r1", "server_models": ["chosen"]},
        {"type": "turn.completed", "usage": {}},
    ]


def gemini():
    return {"session_id": "fresh", "response": '{"ok":true}', "aerith_model_provenance": {
        "schema": 1, "runtime": "aerith-gemini-provenance-v1", "overflow": False,
        "responses": [{"requested_model": "chosen", "observed_models": ["chosen"], "response_ids": ["r1"],
                       "source": "provider-response.modelVersion", "chunks": 2, "missing_model_chunks": 0,
                       "tool_call_chunks": 0, "completed": True}]}}


class ProvenanceTests(unittest.TestCase):
    def read_codex(self, events):
        return parse_codex_provenance("\n".join(json.dumps(e) for e in events), "chosen")

    def test_valid_codex_and_gemini(self):
        for result in (self.read_codex(codex()), parse_gemini_provenance(json.dumps(gemini()), "chosen")):
            self.assertEqual(result[0], {"ok": True})
            self.assertEqual(result[1]["model"], "chosen")
            self.assertEqual(result[1]["session_id"], "fresh")

    def test_codex_rejects_requested_only_absent_mixed_overflow(self):
        for models in (None, [], [""], ["other"], ["chosen", "other"], "chosen", ["chosen", "chosen"]):
            events = codex()
            events[3]["server_models"] = models
            with self.subTest(models=models), self.assertRaises(GateError):
                self.read_codex(events)
        events = codex()
        events.pop(3)
        with self.assertRaises(GateError):
            self.read_codex(events)

    def test_codex_checks_every_response_and_identity(self):
        events = codex()
        second = events[3] | {"response_id": "r2"}
        events.insert(4, second)
        self.assertEqual(self.read_codex(events)[1]["response_ids"], ["r1", "r2"])
        for field, value in (("server_models", []), ("server_models", ["other"]),
                             ("turn_id", "stale"), ("thread_id", "stale"), ("response_id", "r1")):
            changed = copy.deepcopy(events)
            changed[4][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(GateError):
                self.read_codex(changed)

    def test_codex_rejects_effects_and_broken_lifecycle(self):
        for kind in ("command_execution", "file_change", "mcp_tool_call", "collab_tool_call", "web_search", "unknown"):
            events = codex()
            events[2]["item"]["type"] = kind
            with self.subTest(kind=kind), self.assertRaises(GateError):
                self.read_codex(events)
        base = codex()
        for events in (base[:-1], base[1:], base + [base[3]], base + [base[-1]],
                       base[:2] + [{"type": "error"}] + base[2:], base[:2] + [base[1]] + base[2:]):
            with self.assertRaises(GateError):
                self.read_codex(events)

    def test_gemini_rejects_requested_only_and_mixed_metadata(self):
        changes = {"observed_models": ([], ["other"], ["chosen", "other"]),
                   "source": ("requested", None), "requested_model": ("other",),
                   "completed": (False, 1), "chunks": (0, True, "2"),
                   "missing_model_chunks": (-1, 2, True), "tool_call_chunks": (1, False),
                   "response_ids": ([], ["r1", "r2"], ["bad\nidentity"])}
        for field, values in changes.items():
            for value in values:
                envelope = gemini()
                envelope["aerith_model_provenance"]["responses"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(GateError):
                    parse_gemini_provenance(json.dumps(envelope), "chosen")

    def test_gemini_requires_current_worker_proof(self):
        for field, value in (("overflow", True), ("schema", True), ("runtime", "stock-cli"), ("responses", [])):
            envelope = gemini()
            envelope["aerith_model_provenance"][field] = value
            with self.subTest(field=field), self.assertRaises(GateError):
                parse_gemini_provenance(json.dumps(envelope), "chosen")
        envelope = gemini()
        envelope["error"] = {}
        with self.assertRaises(GateError):
            parse_gemini_provenance(json.dumps(envelope), "chosen")


if __name__ == "__main__":
    unittest.main()
