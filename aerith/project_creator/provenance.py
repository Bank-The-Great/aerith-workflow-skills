"""Strict readers for reviewed, isolated CLI builds, not requested-model echoes."""
import json
import re

from .contracts import GateError


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", value) is not None


def object_response(text):
    value = json.loads(text)
    if not isinstance(value, dict):
        raise GateError("model output must be a JSON object")
    return value


def parse_gemini_provenance(text, requested):
    envelope = json.loads(text)
    if not isinstance(envelope, dict) or "error" in envelope or not identifier(envelope.get("session_id")):
        raise GateError("invalid Gemini worker result")
    proof = envelope.get("aerith_model_provenance")
    if (not isinstance(proof, dict) or type(proof.get("schema")) is not int or proof["schema"] != 1
            or proof.get("runtime") != "aerith-gemini-provenance-v1" or proof.get("overflow") is not False):
        raise GateError("Gemini actual-response provenance absent or invalid")
    records = proof.get("responses")
    if not isinstance(records, list) or len(records) != 1:
        raise GateError("data-only Gemini worker must complete exactly one response")
    record = records[0]
    if (not isinstance(record, dict) or record.get("source") != "provider-response.modelVersion"
            or record.get("requested_model") != requested or record.get("observed_models") != [requested]
            or record.get("completed") is not True or type(record.get("chunks")) is not int
            or record["chunks"] < 1 or type(record.get("missing_model_chunks")) is not int
            or record["missing_model_chunks"] != 0
            or type(record.get("tool_call_chunks")) is not int or record["tool_call_chunks"] != 0):
        raise GateError("Gemini response is incomplete, effect-bearing, or uses another model")
    response_ids = record.get("response_ids")
    if not isinstance(response_ids, list) or len(response_ids) != 1 or not identifier(response_ids[0]):
        raise GateError("Gemini response identity absent or mixed")
    return object_response(envelope["response"]), {
        "model": requested, "session_id": envelope["session_id"],
        "response_ids": response_ids, "source": record["source"],
    }


def parse_codex_provenance(text, requested):
    events = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not events or not all(isinstance(event, dict) for event in events):
        raise GateError("invalid Codex worker stream")
    thread = turn = None
    started = completed = False
    responses, messages = [], []
    completed_items = set()
    for index, event in enumerate(events):
        kind = event.get("type")
        if kind == "thread.started":
            if index != 0 or not identifier(event.get("thread_id")):
                raise GateError("Codex stream mixed thread contexts")
            thread = event["thread_id"]
        elif kind == "turn.started":
            if thread is None or started or index != 1:
                raise GateError("Codex stream must start one fresh turn")
            started = True
        elif not started or completed:
            raise GateError("Codex event outside the current turn")
        elif kind == "aerith.response.completed":
            if (event.get("thread_id") != thread or not identifier(event.get("turn_id"))
                    or not identifier(event.get("response_id")) or event.get("server_models") != [requested]):
                raise GateError("Codex actual model is absent, mixed, invalid or differs from the pin")
            if turn is not None and event["turn_id"] != turn:
                raise GateError("Codex response belongs to another turn")
            turn = event["turn_id"]
            if event["response_id"] in responses or len(responses) >= 128:
                raise GateError("duplicate or excessive Codex response evidence")
            responses.append(event["response_id"])
        elif kind in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            if (not isinstance(item, dict) or item.get("type") not in {"agent_message", "reasoning"}
                    or not identifier(item.get("id")) or not isinstance(item.get("text"), str)):
                raise GateError("Codex emitted an effect-bearing or unknown item")
            if item["id"] in completed_items:
                raise GateError("Codex updated an already completed item")
            if kind == "item.completed":
                completed_items.add(item["id"])
                if item["type"] == "agent_message":
                    messages.append(item["text"])
        elif kind == "turn.completed":
            if not responses or not messages or index != len(events) - 1:
                raise GateError("Codex turn lacks current response proof or final output")
            completed = True
        else:
            raise GateError("Codex worker failed or emitted an unknown event")
    if not completed:
        raise GateError("Codex worker stream did not complete")
    return object_response(messages[-1]), {
        "model": requested, "session_id": thread, "turn_id": turn,
        "response_ids": responses, "source": "provider-response.headers",
    }


def parse_codex_data_only(text, requested, request_id):
    """Read the two-record stream from the lifecycle-free Codex worker."""
    events = [json.loads(line) for line in text.splitlines() if line.strip()]
    if len(events) != 2 or not all(isinstance(event, dict) for event in events):
        raise GateError("Codex data-only worker needs exactly two records")
    metadata, result = events
    if set(metadata) != {"type", "request_id", "response_id", "requested_model", "provider_model"}:
        raise GateError("Codex data-only metadata is malformed")
    if set(result) != set(metadata) | {"output"}:
        raise GateError("Codex data-only result is malformed")
    if metadata.get("type") != "response_metadata" or result.get("type") != "result":
        raise GateError("Codex data-only record order is invalid")
    for record in events:
        if (record.get("request_id") != request_id or record.get("requested_model") != requested
                or record.get("provider_model") != requested
                or not identifier(record.get("response_id"))):
            raise GateError("Codex data-only identity or model evidence differs from the request")
    if metadata["response_id"] != result["response_id"] or not isinstance(result.get("output"), dict):
        raise GateError("Codex data-only response identity or output is invalid")
    return result["output"], {
        "model": requested,
        "request_id": request_id,
        "response_ids": [result["response_id"]],
        "source": "provider-response.headers",
        "runtime": "aerith-data-only",
    }
