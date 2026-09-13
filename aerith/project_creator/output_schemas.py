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

SCHEMAS = {
    "grill-with-docs": _object({
        "brief": _object({
            "summary": STRING,
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
            "non_goals": STRINGS,
            "requirements": _array(_object({
                "id": STRING,
                "text": STRING,
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
    _validate(value, schema_for(stage))
    return value


def _validate(value: object, schema: dict) -> None:
    alternatives = schema.get("anyOf")
    if alternatives is not None:
        if not isinstance(alternatives, list) or not alternatives:
            raise GateError("provider output schema is invalid")
        for alternative in alternatives:
            try:
                _validate(value, alternative)
                return
            except GateError:
                pass
        raise GateError("provider output does not match the stage schema")

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
        raise GateError("provider output does not match the stage schema")
    if "enum" in schema and value not in schema["enum"]:
        raise GateError("provider output does not match the stage schema")
    if expected == "array":
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            raise GateError("provider output schema is invalid")
        for item in value:
            _validate(item, item_schema)
    elif expected == "object":
        properties = schema.get("properties")
        required = schema.get("required")
        if (not isinstance(properties, dict) or not isinstance(required, list)
                or schema.get("additionalProperties") is not False
                or set(value) != set(required) or set(required) != set(properties)):
            raise GateError("provider output does not match the stage schema")
        for key, child_schema in properties.items():
            _validate(value[key], child_schema)
