"""Small, strict boundaries for model-produced data, not a prompt trust policy."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

STAGES = ("grill-with-docs", "to-spec", "to-tickets", "implement", "spec-review", "defect-review")
TIERS = {stage: "highest" if i < 3 else "second-highest" for i, stage in enumerate(STAGES)}
PROTECTED = {".git", ".aerith", ".claude", ".codex", ".gemini", ".agents", ".github", "node_modules", ".venv", "venv"}
SECRET_NAME = re.compile(r"(^\.env($|\.)|secret|credential|^auth\.json$|^id_(rsa|ed25519)|\.(pem|p12|pfx|key)$)", re.I)
SECRET_TEXT = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:ghp_|github_pat_|sk-proj-|sk-ant-api)[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._-]{24,}")
MAX_BYTES = 2_000_000


class GateError(Exception):
    """A deterministic refusal. Messages must not contain raw provider output."""


def digest(value: bytes | str | dict | list) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    elif not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise GateError(f"missing {label}")
    return value


def safe_relative(raw: str) -> str:
    nonempty(raw, "relative path")
    p = PurePosixPath(raw)
    if ("\\" in raw or ":" in raw or p.is_absolute() or str(p) != raw
            or any(ord(character) < 32 or ord(character) == 127 for character in raw)
            or any(x in {"", ".", ".."} or x.casefold() in PROTECTED or SECRET_NAME.search(x) for x in p.parts)
            or any(x.endswith((".", " ")) for x in p.parts)
            or any(re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", x, re.I) for x in p.parts)):
        raise GateError("unsafe or protected relative path")
    return raw


def scoped_path(root: Path, raw: str, allowed: list[str]) -> Path:
    raw = safe_relative(raw)
    # Scope is an exact file set, not a model-controlled wildcard policy.
    if raw not in allowed:
        raise GateError("path outside approved write set")
    p = root / raw
    for q in (p, *p.parents):
        if q == root:
            break
        if q.is_symlink() or (hasattr(q, "is_junction") and q.is_junction()):
            raise GateError("links are not writable")
    if not p.resolve().is_relative_to(root.resolve()):
        raise GateError("path escapes project")
    return p


def safe_text(value: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_BYTES:
        raise GateError("invalid or oversized text")
    if "\x00" in value or SECRET_TEXT.search(value):
        raise GateError("binary or credential-shaped content refused")
    return value


def artifact(title: str, payload: dict) -> str:
    return f"# {title}\n\nCanonical structured content follows.\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n"


def parse_artifact(text: str) -> dict:
    matches = re.findall(r"^```json\n(.*?)\n```$", text, flags=re.M | re.S)
    if len(matches) != 1:
        raise GateError("artifact needs exactly one canonical JSON block")
    try:
        value = json.loads(matches[0])
    except ValueError as exc:
        raise GateError("invalid artifact JSON") from exc
    if not isinstance(value, dict):
        raise GateError("artifact must be an object")
    return value


def criteria(spec: dict, test_ids: set[str]) -> dict:
    if not isinstance(spec, dict):
        raise GateError("spec must be an object")
    reqs = spec.get("requirements")
    if not isinstance(reqs, list) or not reqs:
        raise GateError("spec has no requirements")
    out, req_seen = {}, set()
    for req in reqs:
        if not isinstance(req, dict):
            raise GateError("requirement must be an object")
        rid = nonempty(req.get("id"), "requirement id")
        if rid in req_seen:
            raise GateError("duplicate requirement id")
        req_seen.add(rid)
        nonempty(req.get("text"), "requirement text")
        acs = req.get("acceptance")
        if not isinstance(acs, list) or not acs:
            raise GateError("requirement has no acceptance criteria")
        for ac in acs:
            if not isinstance(ac, dict):
                raise GateError("criterion must be an object")
            cid = nonempty(ac.get("id"), "criterion id")
            if cid in out:
                raise GateError("duplicate criterion id")
            nonempty(ac.get("text"), "criterion text")
            proof = ac.get("test_ids")
            if not isinstance(proof, list) or not proof or not all(isinstance(x, str) for x in proof) or not set(proof) <= test_ids:
                raise GateError("criterion lacks approved verification")
            out[cid] = ac
    return out


def ticket_order(tickets: list[dict], acs: dict, allowed: list[str]) -> list[str]:
    if not isinstance(tickets, list) or not tickets:
        raise GateError("no tickets")
    by_id, owners = {}, {}
    for ticket in tickets:
        if not isinstance(ticket, dict):
            raise GateError("ticket must be an object")
        tid = nonempty(ticket.get("id"), "ticket id")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tid) or tid in by_id:
            raise GateError("invalid or duplicate ticket id")
        nonempty(ticket.get("title"), "ticket title")
        cs = ticket.get("criteria")
        ws = ticket.get("write_set")
        if not isinstance(cs, list) or not cs or not isinstance(ws, list) or not ws:
            raise GateError("ticket lacks criteria or write set")
        if not all(isinstance(x, str) for x in cs + ws):
            raise GateError("ticket scope identifiers must be strings")
        if len(set(cs)) != len(cs):
            raise GateError("duplicate ticket criterion")
        for cid in cs:
            if cid not in acs or cid in owners:
                raise GateError("missing or multiply owned criterion")
            owners[cid] = tid
        if not set(ws) <= set(allowed):
            raise GateError("ticket expands approved write set")
        for name in ws:
            safe_relative(name)
        deps = ticket.get("blocked_by")
        if not isinstance(deps, list) or not all(isinstance(x, str) for x in deps) or len(set(deps)) != len(deps):
            raise GateError("invalid ticket dependencies")
        by_id[tid] = ticket
    if set(owners) != set(acs):
        raise GateError("ticket coverage incomplete")
    ordered, pending = [], set(by_id)
    while pending:
        ready = sorted(x for x in pending if set(by_id[x]["blocked_by"]) <= set(ordered))
        if not ready:
            raise GateError("cyclic or unknown dependency")
        ordered.extend(ready)
        pending.difference_update(ready)
    return ordered


def validate_review(report: dict, expected: set[str], *, spec_axis: bool) -> bool:
    if not isinstance(report, dict):
        raise GateError("review must be an object")
    if report.get("verdict") not in {"pass", "fail", "needs_context"}:
        raise GateError("invalid review verdict")
    findings = report.get("findings")
    if not isinstance(findings, list) or not isinstance(report.get("limitations"), list):
        raise GateError("incomplete review report")
    checked = report.get("checked_criteria", [])
    if not isinstance(checked, list) or not all(isinstance(x, str) for x in checked):
        raise GateError("invalid reviewed criterion list")
    if spec_axis and set(checked) != expected:
        raise GateError("review did not cover every criterion")
    for finding in findings:
        if not isinstance(finding, dict):
            raise GateError("finding must be an object")
        if type(finding.get("priority")) is not int or finding["priority"] not in range(4):
            raise GateError("invalid finding priority")
        nonempty(finding.get("message"), "finding evidence")
        nonempty(finding.get("id"), "finding id")
        if finding["priority"] == 3:
            nonempty(finding.get("disposition"), "P3 disposition")
    return report["verdict"] == "pass" and not report["limitations"] and all(f["priority"] == 3 for f in findings)
