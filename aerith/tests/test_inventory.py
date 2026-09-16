"""The coverage instrument's own tests, deliberately kept OUT of the mutation campaign.

These check that the extracted inventory of the gate's conditions, the registry that disposes of
each one, and the rows the tests actually have agree with each other. They are bookkeeping, not
behaviour, and a mutant that deletes a condition makes them fail for that reason alone. Run inside
the campaign they would report every such mutant as killed, which would turn the campaign into a
check of its own paperwork: measured on 2026-09-16, three mutants died here and nowhere else.
So the campaign runs `tests.test_edges` and `tests.test_controller`, and this module runs with the
suite, where a stale registry must still fail loudly.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_inventory


class GateCoverageInstrument(unittest.TestCase):
    def test_gate_conditions_registry_matches_the_gate_as_written(self):
        """REQ-LC-023, round 25: the list of conditions is extracted, and every one is disposed of.

        Round 24 failed its Spec review because the plan's account of this list had drifted from
        the code inside one round, and round 23 failed because a condition that was not a field
        of the record was invisible to the surfaces that reported on it. So the conditions are
        read out of the source here, every one must carry a disposition in `gate_conditions.json`
        and every disposition must match a condition, and a condition with a row must name a row
        this suite actually has. The coverage ledger in the plan repository joins the same file to
        the mutation results; this test is the half that runs on every suite run.
        """
        registry = json.loads((Path(__file__).resolve().parent / "gate_conditions.json").read_text(encoding="utf-8"))
        package = Path(__file__).resolve().parents[1]
        extracted, calls = {}, {}
        for module, functions in registry["scope"].items():
            source = (package / module).read_text(encoding="utf-8")
            atoms, called = gate_inventory.extract(source, module, functions)
            extracted |= {atom.id: atom for atom in atoms}
            calls |= {f"{function} -> {callee}": None for function, names in called.items() for callee in names}
        entries = {entry["id"]: entry for entry in registry["conditions"]}
        self.assertEqual(sorted(entries), sorted(list(extracted) + list(calls)),
                         "every condition and every call out of the scope carries a disposition, and "
                         "every disposition matches one that is still there")
        labels = set(gate_inventory.row_labels(
            (package / "tests" / "test_edges.py").read_text(encoding="utf-8"),
            "test_capability_admission_refuses_on_every_condition_it_states", "expectations"))
        labels |= {"lock:" + label for label in gate_inventory.row_labels(
            (package / "tests" / "test_controller.py").read_text(encoding="utf-8"),
            "test_reviewed_launch_lock_refuses_every_way_it_states", "cases")}
        for entry in registry["conditions"]:
            with self.subTest(entry["id"][:70]):
                self.assertIn(entry["disposition"], {"covered", "witnessed", "debt", "implicit", "structural"})
                for row in entry["rows"]:
                    self.assertIn(row.split(":", 1)[1] if row.startswith("edges:") else row, labels)
                if entry["disposition"] == "covered":
                    self.assertTrue(entry["rows"] and entry["mutants"], "a covered condition has both")
                if entry["disposition"] in {"debt", "witnessed", "implicit", "structural"}:
                    self.assertTrue(entry["reason"], "a judgement carries its reason")

    def test_every_launch_precondition_that_reads_only_the_record_is_a_gate_condition(self):
        """R24-SPEC-01 / R24-SEC-01, as a check rather than as a claim.

        `invoke` refused on the vendor key; the gate did not; both readiness surfaces reported
        `proof-current` for an adapter every launch of which was refused. Whatever `invoke`
        refuses on before it launches, using nothing but the record and its key, the gate must
        refuse on too. Anything it reads besides the record is listed in the registry by name.
        """
        package = Path(__file__).resolve().parents[1]
        registry = json.loads((Path(__file__).resolve().parent / "gate_conditions.json").read_text(encoding="utf-8"))
        source = (package / "project_creator" / "providers.py").read_text(encoding="utf-8")
        atoms, _ = gate_inventory.extract(source, "project_creator/providers.py",
                                          registry["scope"]["project_creator/providers.py"])
        gate = gate_inventory.gate_texts(atoms, source)
        gate |= gate_inventory.gate_texts([atom for atom in atoms if atom.function == "_launch_refusal"], source,
                                          "_launch_refusal")
        checked = 0
        for atom, resolved, record_only in gate_inventory.launch_preconditions(source):
            with self.subTest(resolved[:70]):
                if record_only:
                    self.assertIn(resolved, gate, "the gate must refuse on this too")
                    checked += 1
                else:
                    self.assertIn(atom.text, registry["launch_exempt"],
                                  "a launch precondition that reads more than the record is listed by name")
        # An equality, not a floor. Round 25 asserted `>= 4`, which a NEW precondition could not
        # fail: adding one inside the block that encloses the launch left the count at 4 and the
        # check green (R25-SEC-01). Changing this number is now part of changing `invoke`.
        self.assertEqual(checked, registry["launch_precondition_count"],
                         "invoke's record-only preconditions changed; the registry must say so")

    def test_the_inventory_refuses_what_it_does_not_understand(self):
        """The extractor's own refusals, because a control never seen to refuse is unproven.

        Each source below can refuse a caller in a way this module was not taught to read. If any
        of them extracts cleanly, the inventory is reporting a subset of the gate as the whole of
        it, which is the failure the whole design exists to prevent (INVERTER F1).
        """
        cases = {
            "a shape with no pattern": "def gate(x):\n    while x:\n        raise GateError('no')\n",
            "a refusal with no condition": "def gate(x):\n    raise GateError('always')\n",
            "a handler this module cannot read":
                "def gate(x):\n    try:\n        x.read()\n    except OSError:\n        return 7\n",
            "a function with no condition at all": "def gate(x):\n    return x\n",
        }
        for label, source in cases.items():
            with self.subTest(label), self.assertRaises(gate_inventory.Unrecognised):
                gate_inventory.extract(source, "fixture", ["gate"])
        # A condition inside an expression is not refused, because the gate has such selections;
        # what matters is that it is RECORDED, so it cannot be disposed of without being seen.
        hidden, _ = gate_inventory.extract(
            "def gate(x):\n    y = 1 if x.a and x.b else 2\n    if y:\n        raise GateError('no')\n",
            "fixture", ["gate"])
        self.assertEqual({atom.text for atom in hidden}, {"x.a", "x.b", "y"})
        # The same rule for a guard whose body neither exits nor decides anything a refusal reads.
        # Round 25 dropped that shape silently, and one such guard decided whether the launch-time
        # hash lock covers the executable at all (R25-SPEC-02). It is recorded, not refused: the
        # gate has guards of exactly this shape, and what matters is that each is disposed of.
        guarded, _ = gate_inventory.extract(
            "def gate(x):\n    if x.a:\n        x.c = 1\n    if x.d:\n        raise GateError('no')\n",
            "fixture", ["gate"])
        self.assertEqual({(atom.kind, atom.text) for atom in guarded},
                         {("expression", "x.a"), ("condition", "x.d")})
        # And the round 24 shape of `invoke`, whose vendor key the gate could not see.
        round24 = ("_ADMITTED = {'codex-data-only'}\n\n\n"
                   "class CLIProvider:\n"
                   "    def invoke(self, stage, model, packet, directory):\n"
                   "        if self.name != 'codex' or self.config.get('output') not in _ADMITTED:\n"
                   "            raise GateError('only a reviewed data-only provider worker may be launched')\n"
                   "        return execute(self.config['argv'])\n")
        preconditions = gate_inventory.launch_preconditions(round24)
        self.assertEqual([resolved for _, resolved, record_only in preconditions if record_only],
                         ["name != 'codex'", "config.get('output') not in _ADMITTED"])
