"""REQ-PC-013: the stage schemas, the examples shown to workers, and the document contracts agree."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_creator.contracts import GateError, review_refusal, validate_review
from project_creator.engine import CONTRACTS
from project_creator.output_schemas import (SCHEMAS, assumption_questions, validate_brief, validate_output,
                                            validate_spec)

TESTS = {"unit", "integration"}


def brief():
    return {"summary": "Operators need a one-line status of each run; nothing is published.",
            "users": [{"name": "operator", "description": "Starts runs and reads their status."}],
            "success_conditions": ["status prints one line per run"], "constraints": ["read-only"],
            "decisions": [{"id": "DEC-1", "decision": "Status reads the ledger only",
                           "rationale": "The ledger is the single record of runs."}],
            "glossary": {"entries": [{"term": "run", "definition": "One started project."}]}}


def spec():
    return {"title": "Run status", "problem": "An operator cannot see where each run stands.",
            "solution": "A status command prints one line per run from the ledger.",
            "actors": [{"name": "operator", "description": "Reads the status."},
                       {"name": "system", "description": "Keeps the ledger consistent."}],
            "non_goals": ["publishing status anywhere"],
            "decisions": [{"id": "SDEC-1", "kind": "interface", "decision": "One line per run",
                           "rationale": "Fits a terminal.", "source": {"type": "brief", "ref": "DEC-1"}}],
            "test_seams": [{"seam": "cli status", "test_ids": ["unit"], "prior_art": ["tests for cli doctor"]}],
            "testing_notes": ["Assert the printed text, not the ledger query."], "further_notes": [],
            "requirements": [
                {"id": "REQ-1", "actor": "operator", "text": "see each run's stage",
                 "benefit": "know which run needs an answer",
                 "acceptance": [{"id": "AC-1", "text": "a waiting run prints its stage", "test_ids": ["unit"]}]},
                {"id": "REQ-2", "actor": "system", "text": "read the ledger without writing",
                 "benefit": "status can never change a run",
                 "acceptance": [{"id": "AC-2", "text": "the ledger bytes are unchanged", "test_ids": ["integration"]}]},
            ]}


class Contracts(unittest.TestCase):
    def test_every_example_shown_to_a_worker_passes_its_stage_schema(self):
        # Before REQ-PC-013 nothing checked this, and the review examples were already invalid
        # against their own verdict enum.
        self.assertEqual(set(CONTRACTS), set(SCHEMAS))
        for stage, example in CONTRACTS.items():
            with self.subTest(stage=stage):
                validate_output(stage, copy.deepcopy(example))

    def test_a_worker_that_copies_the_example_is_refused_as_a_document(self):
        with self.assertRaisesRegex(GateError, "placeholder"):
            validate_brief(copy.deepcopy(CONTRACTS["grill-with-docs"]["brief"]))
        with self.assertRaisesRegex(GateError, "placeholder"):
            validate_spec(copy.deepcopy(CONTRACTS["to-spec"]["spec"]), test_ids={"approved-test-id"},
                          brief=None, answers=[])

    def test_a_schema_refusal_names_where_and_never_echoes_what_the_worker_sent(self):
        older = {"spec": {"title": "t", "non_goals": [], "requirements": [], "WORKER_CHOSEN_KEY": 1},
                 "questions": []}
        with self.assertRaises(GateError) as caught:
            validate_output("to-spec", older)
        message = str(caught.exception)
        self.assertIn("at to-spec.spec", message)
        self.assertIn("missing actors", message)
        self.assertIn("1 unexpected field", message)
        self.assertNotIn("WORKER_CHOSEN_KEY", message)
        bad_kind = {"spec": spec(), "questions": []}
        bad_kind["spec"]["decisions"][0]["kind"] = "vibes"
        with self.assertRaisesRegex(GateError, r"at to-spec\.spec\.decisions\[0\]\.kind"):
            validate_output("to-spec", bad_kind)

    def test_a_complete_brief_and_spec_are_admitted(self):
        validate_brief(brief())
        validate_spec(spec(), test_ids=TESTS, brief=brief(), answers=[])
        # With no brief, actors are not restricted, and a decision must cite something other than a brief.
        standalone = spec() | {"actors": [{"name": "auditor", "description": "Reads status."}]}
        standalone["decisions"][0]["source"] = {"type": "evidence", "ref": "project_creator/cli.py"}
        for requirement in standalone["requirements"]:
            requirement["actor"] = "auditor"
        validate_spec(standalone, test_ids=TESTS, brief=None, answers=[])

    def test_each_brief_rule_refuses_on_its_own(self):
        rows = [
            ("summary is a placeholder", {"summary": "..."}, "summary is empty or a placeholder"),
            ("no intended user", {"users": []}, "no intended user"),
            ("no success condition", {"success_conditions": []}, "no success condition"),
            ("duplicate user", {"users": [{"name": "operator", "description": "a"},
                                          {"name": "Operator", "description": "b"}]}, "duplicate brief user"),
            ("duplicate glossary term", {"glossary": {"entries": [{"term": "run", "definition": "a"},
                                                                  {"term": "RUN", "definition": "b"}]}},
             "duplicate glossary term"),
            ("duplicate decision id", {"decisions": brief()["decisions"] * 2}, "duplicate brief decision id"),
        ]
        for label, change, expected in rows:
            with self.subTest(label):
                with self.assertRaisesRegex(GateError, expected):
                    validate_brief(brief() | change)

    def test_each_spec_rule_refuses_on_its_own(self):
        def with_requirement(**change):
            value = spec()
            value["requirements"][0] = value["requirements"][0] | change
            return value

        def with_decision(**change):
            value = spec()
            value["decisions"][0] = value["decisions"][0] | change
            return value

        three_same = spec()
        three_same["requirements"].append(copy.deepcopy(three_same["requirements"][0]) | {
            "id": "REQ-3", "text": "see each run's age",
            "acceptance": [{"id": "AC-3", "text": "age is shown", "test_ids": ["unit"]}]})
        for requirement in three_same["requirements"]:
            requirement["benefit"] = "know which run needs an answer"
        rows = [
            ("problem placeholder", spec() | {"problem": "..."}, brief(), [], "problem is empty or a placeholder"),
            ("solution restates the problem", spec() | {"solution": spec()["problem"]}, brief(), [], "restates the problem"),
            ("no actor", spec() | {"actors": []}, brief(), [], "names no actor"),
            ("actor the brief never named", spec() | {"actors": [{"name": "auditor", "description": "x"}]},
             brief(), [], "not an intended user named by the brief"),
            ("requirement actor not declared", with_requirement(actor="auditor"), brief(), [],
             "requirement actor is not a declared spec actor"),
            ("benefit restates the text", with_requirement(benefit="see each run's stage"), brief(), [],
             "benefit restates the requirement"),
            ("most requirements share a benefit", three_same, brief(), [], "share one benefit"),
            ("decision reuses a brief id", with_decision(id="DEC-1"), brief(), [], "reuses a brief decision id"),
            ("decision cites a missing brief decision", with_decision(source={"type": "brief", "ref": "DEC-9"}),
             brief(), [], "cites a brief decision that does not exist"),
            ("decision cites a missing answer", with_decision(source={"type": "answer", "ref": "2"}),
             brief(), [{"questions": ["q"], "answer": "a"}], "operator answer that does not exist"),
            ("evidence with no location", with_decision(source={"type": "evidence", "ref": " "}), brief(), [],
             "evidence reference is empty"),
            ("duplicate decision id", spec() | {"decisions": spec()["decisions"] * 2}, brief(), [],
             "duplicate spec decision id"),
            ("seam tied to an unapproved test", spec() | {"test_seams": [{"seam": "cli", "test_ids": ["nightly"],
                                                                          "prior_art": []}]},
             brief(), [], "not tied to approved verification"),
            ("placeholder note", spec() | {"further_notes": ["TBD"]}, brief(), [], "note or non-goal"),
        ]
        # Every row passes a valid brief, so each is refused by its own rule and no earlier one.
        for label, value, given_brief, answers, expected in rows:
            with self.subTest(label):
                with self.assertRaisesRegex(GateError, expected):
                    validate_spec(value, test_ids=TESTS, brief=given_brief, answers=answers)

    def test_citing_an_answer_that_exists_is_admitted(self):
        value = spec()
        value["decisions"][0]["source"] = {"type": "answer", "ref": "1"}
        validate_spec(value, test_ids=TESTS, brief=brief(), answers=[{"questions": ["q"], "answer": "a"}])

    def test_every_assumption_becomes_one_question_naming_its_decision(self):
        value = spec()
        value["decisions"].append({"id": "SDEC-2", "kind": "schema", "decision": "Store the age in seconds.",
                                   "rationale": "Seems simplest.", "source": {"type": "assumption", "ref": ""}})
        validate_spec(value, test_ids=TESTS, brief=brief(), answers=[])
        questions = assumption_questions(value)
        self.assertEqual(len(questions), 1)
        self.assertIn("SDEC-2", questions[0])
        self.assertEqual(assumption_questions(spec()), [])


def review(**changes):
    return {"verdict": "pass", "checked_criteria": ["AC-1"], "findings": [], "limitations": []} | changes


class ReviewContract(unittest.TestCase):
    """REQ-PC-014. What a review is admitted on, and what it may not claim."""

    def test_an_inherent_limitation_does_not_refuse_an_otherwise_clean_review(self):
        # The D12 pilot regression: both reviewers returned pass with zero findings and full
        # coverage, and the delivery was refused because they had said what they cannot do.
        stated = review(limitations=[{"kind": "inherent", "text": "this role cannot execute the tests it is shown"},
                                     {"kind": "inherent", "text": "only the frozen packet was visible"}])
        self.assertIsNone(review_refusal(stated, {"AC-1"}, spec_axis=True))
        self.assertTrue(validate_review(stated, {"AC-1"}, spec_axis=True))

    def test_an_encountered_limitation_refuses_and_says_so_without_raising(self):
        stopped = review(limitations=[{"kind": "encountered", "text": "the packet lacks the file AC-1 names"}])
        self.assertIn("stopped it checking", review_refusal(stopped, {"AC-1"}, spec_axis=True))
        self.assertFalse(validate_review(stopped, {"AC-1"}, spec_axis=True))

    def test_the_verdict_and_the_findings_still_decide(self):
        for report, reason in ((review(verdict="needs_context"), "pass verdict"),
                               (review(verdict="fail", findings=[{"id": "F-1", "priority": 3, "message": "style",
                                                                  "disposition": "advisory"}]), "pass verdict"),
                               (review(findings=[{"id": "F-1", "priority": 3, "message": "style", "disposition": "advisory"},
                                                 {"id": "F-2", "priority": 2, "message": "wrong row"}]), "above P3")):
            self.assertIn(reason, review_refusal(report, {"AC-1"}, spec_axis=True))
        clean = review(findings=[{"id": "F-1", "priority": 3, "message": "style", "disposition": "advisory"}])
        self.assertIsNone(review_refusal(clean, {"AC-1"}, spec_axis=True))
        with self.assertRaises(GateError):
            review_refusal(review(findings=[{"id": "F-1", "priority": 3, "message": "style"}]), {"AC-1"}, spec_axis=True)

    def test_a_limitation_must_say_its_kind_and_say_something(self):
        for bad in ("", "   ", "...", "tbd", "TODO"):
            with self.assertRaises(GateError):
                review_refusal(review(limitations=[{"kind": "inherent", "text": bad}]), {"AC-1"}, spec_axis=True)
        for kind in ("", "advisory", None):
            with self.assertRaises(GateError):
                review_refusal(review(limitations=[{"kind": kind, "text": "real text"}]), {"AC-1"}, spec_axis=True)
        with self.assertRaises(GateError):
            review_refusal(review(limitations=["a bare string"]), {"AC-1"}, spec_axis=True)

    def test_no_axis_may_claim_criteria_it_was_not_given(self):
        # The spec axis must cover its scope exactly; the defect axis may cover less, never more.
        with self.assertRaises(GateError):
            review_refusal(review(checked_criteria=[]), {"AC-1"}, spec_axis=True)
        with self.assertRaises(GateError):
            review_refusal(review(checked_criteria=["AC-1", "AC-2"]), {"AC-1"}, spec_axis=True)
        with self.assertRaises(GateError):
            review_refusal(review(checked_criteria=["AC-1", "AC-9"]), {"AC-1"}, spec_axis=False)
        self.assertIsNone(review_refusal(review(checked_criteria=[]), {"AC-1"}, spec_axis=False))


if __name__ == "__main__":
    unittest.main()
