"""Verified catalog snapshots, role routing, and immutable per-run pins."""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

from .contracts import GateError, TIERS, digest

OFFICIAL = {"claude": {"platform.claude.com", "www.anthropic.com"},
            "codex": {"developers.openai.com", "platform.openai.com"},
            "gemini": {"ai.google.dev", "cloud.google.com"}}


def load_config(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
        try:
            result = json.loads(text)
        except ValueError:
            import yaml
            result = yaml.safe_load(text)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (OSError, ValueError, ImportError) as exc:
        raise GateError("configuration unavailable or malformed") from exc


def catalog_pin(catalog: dict, vendor: str, *, at=None) -> dict:
    at = at or datetime.now(timezone.utc)
    row = catalog.get("project_creator", {}).get("vendors", {}).get(vendor)
    if not row:
        raise GateError("vendor has no verified model profile")
    try:
        verified = datetime.fromisoformat(row["verified_at"])
        if verified.tzinfo is None or verified > at or at - verified > timedelta(hours=24):
            raise GateError("model catalog needs official-source refresh")
        source = urlparse(row["source"])
        if source.scheme != "https" or source.hostname not in OFFICIAL.get(vendor, set()):
            raise GateError("model catalog source is not an approved official host")
        ids = [row["highest"], row["second-highest"]]
        if len(set(ids)) != 2 or not all(re.fullmatch(r"[a-zA-Z0-9._-]{1,100}", m) for m in ids):
            raise GateError("invalid two-tier model profile")
        return {"vendor": vendor, "highest": ids[0], "second-highest": ids[1],
                "verified_at": row["verified_at"], "source": row["source"],
                "catalog_hash": digest(catalog), "effort": row.get("effort", "provider-default")}
    except (KeyError, TypeError, ValueError) as exc:
        raise GateError("incomplete model catalog") from exc


def model_for(pins: dict, author: str, stage: str, review_vendor=None):
    vendor = review_vendor if stage == "spec-review" and review_vendor else author
    if vendor not in pins:
        raise GateError("requested vendor has no run-pinned model profile")
    return vendor, pins[vendor][TIERS[stage]]


def refresh_catalog(catalog: dict, vendor: str, fetch=None) -> dict:
    """Refresh known family roles only. Unknown family rankings require a ruling.

    Discovery never installs code. Patterns are maintained in trusted catalog
    policy; a page containing an instruction cannot change the role ordering.
    """
    import copy
    result = copy.deepcopy(catalog)
    row = result.get("project_creator", {}).get("vendors", {}).get(vendor)
    if not row:
        raise GateError("unknown catalog vendor")
    url = urlparse(row.get("source", ""))
    if url.scheme != "https" or url.hostname not in OFFICIAL.get(vendor, set()):
        raise GateError("unapproved metadata source")
    if fetch is None:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise GateError("model metadata redirect refused")
        def fetch(source):
            with urllib.request.build_opener(NoRedirect).open(source, timeout=15) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise GateError("metadata response too large")
            return raw.decode("utf-8")
    try:
        body = fetch(row["source"])
        for tier in ("highest", "second-highest"):
            pattern = row.get("discovery", {}).get(tier)
            if not pattern:
                raise GateError("missing explicit family discovery rule")
            candidates = set(re.findall(pattern, body))
            if not candidates or not all(isinstance(x, str) for x in candidates):
                raise GateError("official metadata did not identify model family")
            # Family is policy; numeric version ordering applies ONLY inside it.
            row[tier] = max(candidates, key=lambda x: tuple(map(int, re.findall(r"\d+", x))))
        row["verified_at"] = datetime.now(timezone.utc).isoformat()
        row["source_hash"] = digest(body)
        catalog_pin(result, vendor)
        return result
    except (OSError, ValueError, re.error) as exc:
        raise GateError("official model refresh unavailable") from exc
