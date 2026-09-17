"""Strict provider-output schemas owned by the deterministic controller."""
from __future__ import annotations

import copy

from .contracts import GateError


def _array(items):
    return {"type": "array", "items": items}


def _object(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


STRING = {"type": "string"}
STRINGS = _array(STRING)
QUESTION = {"questions": STRINGS}
FINDING = _object({
    "id": STRING,
    "priority": {"type": "integer", "enum": [0, 1, 2, 3]},
    "path": {"anyOf": [STRING, {"type": "null"}]},
    "line": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    "message": STRING,
    "criterion": {"anyOf": [STRING, {"type": "null"}]},
    "disposition": {"anyOf": [STRING, {"type": "null"}]},
})
REVIEW = _object({
    "verdict": {"type": "string", "enum": ["pass", "fail", "needs_context"]},
    "checked_criteria": STRINGS,
    "findings": _array(FINDING),
    "limitations": STRINGS,
})

NAMED = _object({"name": STRING, "description": STRING})
DECISION_KINDS = ["module", "interface", "schema", "api", "architecture", "process", "other"]
DECISION_SOURCES = ["brief", "answer", "evidence", "assumption"]

SCHEMAS = {
    "grill-with-docs": _object({
        "brief": _object({
            "summary": STRING,
            # The skill has always asked for intended users, success conditions and constraints.
            # Until 2026-09-17 the schema had no field for them, so a worker that obeyed the skill
            # either dropped them or had the whole stage refused (REQ-PC-013).
            "users": _array(NAMED),
            "success_conditions": STRINGS,
            "constraints": STRINGS,
            "decisions": _array(_object({"id": STRING, "decision": STRING, "rationale": STRING})),
            "glossary": _object({
                "entries": _array(_object({"term": STRING, "definition": STRING})),
            }),
        }),
        **QUESTION,
    }),
    "to-spec": _object({
        "spec": _object({
            "title": STRING,
            "problem": STRING,
            "solution": STRING,
            "actors": _array(NAMED),
            "non_goals": STRINGS,
            # A decision says where it came from. An `assumption` is the worker's own guess, and
            # the controller turns every one into a question before the spec can bind anything.
            "decisions": _array(_object({
                "id": STRING,
                "kind": {"type": "string", "enum": DECISION_KINDS},
                "decision": STRING,
                "rationale": STRING,
                "source": _object({"type": {"type": "string", "enum": DECISION_SOURCES}, "ref": STRING}),
            })),
            "test_seams": _array(_object({"seam": STRING, "test_ids": STRINGS, "prior_art": STRINGS})),
            "testing_notes": STRINGS,
            "further_notes": STRINGS,
            "requirements": _array(_object({
                "id": STRING,
                "actor": STRING,
                "text": STRING,
                "benefit": STRING,
                "acceptance": _array(_object({"id": STRING, "text": STRING, "test_ids": STRINGS})),
            })),
        }),
        **QUESTION,
    }),
    "to-tickets": _object({
        "tickets": _array(_object({
            "id": STRING,
            "title": STRING,
            "criteria": STRINGS,
            "blocked_by": STRINGS,
            "write_set": STRINGS,
        })),
        **QUESTION,
    }),
    "implement": _object({
        "changes": _array(_object({
            "path": STRING,
            "expected_sha256": STRING,
            "content": STRING,
        })),
        "summary": STRING,
        **QUESTION,
    }),
    "spec-review": REVIEW,
    "defect-review": REVIEW,
}


def schema_for(stage: str) -> dict:
    try:
        return copy.deepcopy(SCHEMAS[stage])
    except KeyError as exc:
        raise GateError("provider output schema is unavailable") from exc


def validate_output(stage: str, value: object) -> dict:
    """Validate provider data again at the trusted controller boundary.

    Provider-side structured output is useful but is not an integrity boundary:
    a malformed provider, replaced worker, or future transport regression must
    not be able to bypass the canonical stage contract.
    """
    _validate(value, schema_for(stage), stage)
    return value


def _mismatch(path: str, detail: str) -> GateError:
    # The path is built from schema keys and list positions only, never from a key or value the
    # worker supplied, so the message is safe to show and still says WHERE the output is wrong.
    # Without it a refused stage names no field, the output is not saved, and every resume pays
    # for the same call again (INVERTER F2, 2026-09-17).
    return GateError(f"provider output does not match the stage schema at {path}: {detail}")


def _validate(value: object, schema: dict, path: str = "output") -> None:
    alternatives = schema.get("anyOf")
    if alternatives is not None:
        if not isinstance(alternatives, list) or not alternatives:
            raise GateError("provider output schema is invalid")
        for alternative in alternatives:
            try:
                _validate(value, alternative, path)
                return
            except GateError:
                pass
        raise _mismatch(path, "no allowed type matches")

    expected = schema.get("type")
    matches = {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected, False)
    if not matches:
        raise _mismatch(path, f"expected {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise _mismatch(path, "value is not one of the allowed values")
    if expected == "array":
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            raise GateError("provider output schema is invalid")
        for index, item in enumerate(value):
            _validate(item, item_schema, f"{path}[{index}]")
    elif expected == "object":
        properties = schema.get("properties")
        required = schema.get("required")
        if (not isinstance(properties, dict) or not isinstance(required, list)
                or schema.get("additionalProperties") is not False or set(required) != set(properties)):
            raise GateError("provider output schema is invalid")
        missing = sorted(set(required) - set(value))
        unexpected = len(set(value) - set(required))
        if missing or unexpected:
            parts = ([f"missing {', '.join(missing)}"] if missing else []) + (
                [f"{unexpected} unexpected field(s)"] if unexpected else [])
            raise _mismatch(path, "; ".join(parts))
        for key, child_schema in properties.items():
            _validate(value[key], child_schema, f"{path}.{key}")


# --- Document contracts: what a brief and a spec must MEAN, beyond their shape. ---------------
# Shape alone admits "As a user ... so that it works" and the literal "..." copied from the
# contract example. These rules are the checks a machine can actually make; anything they
# cannot judge stays with the operator and the reviewers (REQ-PC-013, INVERTER F1/F3).

_PLACEHOLDERS = {"", "...", "…", "tbd", "todo", "n/a"}


def _real(value: object, label: str) -> str:
    if not isinstance(value, str) or value.strip().casefold().strip(". …") in {"", *_PLACEHOLDERS}:
        raise GateError(f"{label} is empty or a placeholder")
    return value


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _unique_ids(items, label):
    seen = set()
    for item in items:
        key = _real(item["id"], f"{label} id").strip().casefold()
        if key in seen:
            raise GateError(f"duplicate {label} id")
        seen.add(key)
    return seen


def validate_brief(brief: dict) -> dict:
    _validate(brief, SCHEMAS["grill-with-docs"]["properties"]["brief"], "brief")
    _real(brief["summary"], "brief summary")
    if not brief["users"]:
        raise GateError("brief names no intended user")
    names = set()
    for user in brief["users"]:
        name = _norm(_real(user["name"], "brief user name"))
        _real(user["description"], "brief user description")
        if name in names:
            raise GateError("duplicate brief user")
        names.add(name)
    if not brief["success_conditions"]:
        raise GateError("brief states no success condition")
    for text in brief["success_conditions"] + brief["constraints"]:
        _real(text, "brief condition or constraint")
    _unique_ids(brief["decisions"], "brief decision")
    for decision in brief["decisions"]:
        _real(decision["decision"], "brief decision")
        _real(decision["rationale"], "brief decision rationale")
    terms = set()
    for entry in brief["glossary"]["entries"]:
        term = _norm(_real(entry["term"], "glossary term"))
        _real(entry["definition"], "glossary definition")
        if term in terms:
            raise GateError("duplicate glossary term")
        terms.add(term)
    return brief


SYSTEM_ACTOR = "system"


def validate_spec(spec: dict, *, test_ids: set[str], brief: dict | None, answers: list) -> dict:
    """The spec's meaning, checked where it is written and before any stage that reads it pays.

    The brief is authoritative and never rewritten here: a spec decision either cites where it came
    from or declares itself an assumption, and its id may not reuse a brief decision id.
    """
    _validate(spec, SCHEMAS["to-spec"]["properties"]["spec"], "spec")
    for label in ("title", "problem", "solution"):
        _real(spec[label], f"spec {label}")
    if _norm(spec["problem"]) == _norm(spec["solution"]):
        raise GateError("spec solution restates the problem")

    if not spec["actors"]:
        raise GateError("spec names no actor")
    brief_users = {_norm(user["name"]) for user in (brief or {}).get("users", [])}
    actors = set()
    for actor in spec["actors"]:
        name = _norm(_real(actor["name"], "spec actor name"))
        _real(actor["description"], "spec actor description")
        if name in actors:
            raise GateError("duplicate spec actor")
        # Only an actor the brief named, or the explicit system actor for controller invariants,
        # so an actor cannot be invented at spec time to make a user story read well.
        if brief is not None and name != SYSTEM_ACTOR and name not in brief_users:
            raise GateError("spec actor is not an intended user named by the brief")
        actors.add(name)

    for text in spec["non_goals"] + spec["testing_notes"] + spec["further_notes"]:
        _real(text, "spec note or non-goal")

    brief_ids = {item["id"].strip().casefold() for item in (brief or {}).get("decisions", [])}
    spec_ids = _unique_ids(spec["decisions"], "spec decision")
    if spec_ids & brief_ids:
        raise GateError("spec decision id reuses a brief decision id")
    for decision in spec["decisions"]:
        _real(decision["decision"], "spec decision")
        _real(decision["rationale"], "spec decision rationale")
        source, ref = decision["source"]["type"], decision["source"]["ref"]
        if source == "brief" and ref.strip().casefold() not in brief_ids:
            raise GateError("spec decision cites a brief decision that does not exist")
        if source == "answer" and not (ref.strip().isdigit() and 1 <= int(ref) <= len(answers)):
            raise GateError("spec decision cites an operator answer that does not exist")
        if source == "evidence":
            _real(ref, "spec decision evidence reference")

    for seam in spec["test_seams"]:
        _real(seam["seam"], "test seam")
        if not seam["test_ids"] or not set(seam["test_ids"]) <= test_ids:
            raise GateError("test seam is not tied to approved verification")
        for item in seam["prior_art"]:
            _real(item, "test seam prior art")

    benefits = []
    for requirement in spec["requirements"]:
        if _norm(_real(requirement["actor"], "requirement actor")) not in actors:
            raise GateError("requirement actor is not a declared spec actor")
        text = _norm(_real(requirement["text"], "requirement text"))
        benefit = _norm(_real(requirement["benefit"], "requirement benefit"))
        if benefit == text:
            raise GateError("requirement benefit restates the requirement")
        benefits.append(benefit)
    if len(benefits) >= 3 and max(benefits.count(b) for b in benefits) * 2 > len(benefits):
        raise GateError("most requirements share one benefit; state what each requirement is for")
    return spec


def assumption_questions(spec: dict) -> list[str]:
    """Every decision the worker marked as its own guess, as a question for the operator."""
    return [f"Spec decision {item['id']} rests on an assumption, not on the brief, an answer or "
            f"evidence: {item['decision']} Confirm it, correct it, or say what it should be."
            for item in spec["decisions"] if item["source"]["type"] == "assumption"]
