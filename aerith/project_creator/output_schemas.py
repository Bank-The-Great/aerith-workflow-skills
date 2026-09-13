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
            "expected_sha256": {"anyOf": [STRING, {"type": "null"}]},
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
