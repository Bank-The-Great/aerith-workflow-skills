"""Frozen source packets and journalled, exactly-scoped worktree edits."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .contracts import GateError, digest, safe_relative, safe_text, scoped_path
from .store import atomic_text


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-c", "core.hooksPath=", "-c", "commit.gpgsign=false", "-C", str(root), *args],
                            capture_output=True, timeout=60, encoding="utf-8", errors="strict")
    if result.returncode:
        raise GateError("git operation failed: " + args[0])
    return result.stdout.strip()


def make_worktree(project: Path, target: Path, branch: str, base: str):
    if target.exists():
        if git(target, "rev-parse", "--abbrev-ref", "HEAD") != branch:
            raise GateError("existing workspace does not match run branch")
        return
    git(project, "worktree", "add", "-b", branch, str(target), base)


def snapshot(root: Path, read_set: list[str]) -> dict:
    files = {}
    for name in sorted(set(read_set)):
        path = scoped_path(root, name, read_set)
        if path.exists():
            if not path.is_file() or path.stat().st_size > 500_000:
                raise GateError("source file is not bounded UTF-8 text")
            try:
                files[name] = safe_text(path.read_bytes().decode("utf-8"))
            except UnicodeError as exc:
                raise GateError("source file is not UTF-8 text") from exc
        else:
            files[name] = None
    if sum(len((x or "").encode("utf-8")) for x in files.values()) > 1_000_000:
        raise GateError("source packet exceeds bounded context; split the project scope")
    return {"files": files, "file_sha256": {k: digest(v) if v is not None else None for k, v in files.items()},
            "hash": digest(files), "head": git(root, "rev-parse", "HEAD")}


def project_lock(project: Path) -> Path:
    # Git's common directory is shared across linked worktrees and independent
    # state directories. Never let --state select a different writer lock.
    common = Path(git(project, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    identity = str(common.resolve())
    if __import__("os").name == "nt":
        identity = identity.casefold()
    return Path(tempfile.gettempdir()) / "project-creator-locks" / (digest(identity) + ".lock")


def audit_scope(root: Path, base: str, allowed: list[str]):
    changed = git(root, "diff", "--name-only", "--no-renames", base).splitlines()
    untracked = git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    for name in changed + untracked:
        safe_relative(name)
        if name not in allowed:
            raise GateError("realized worktree delta exceeds approved scope")


def propose_edits(root: Path, changes: list, allowed: list[str], journal: Path):
    if not isinstance(changes, list) or not changes:
        raise GateError("implementation contains no proposed edits")
    planned, seen = [], set()
    for change in changes:
        if not isinstance(change, dict):
            raise GateError("proposed edit must be an object")
        name = change.get("path")
        path = scoped_path(root, name, allowed)
        if name.casefold() in seen:
            raise GateError("duplicate or case-colliding edit")
        seen.add(name.casefold())
        content = safe_text(change.get("content"))
        before = digest(path.read_bytes()) if path.exists() else None
        if change.get("expected_sha256") != before:
            raise GateError("source changed before proposal application")
        planned.append({"path": name, "before": before, "after": digest(content), "content": content})
    # Intent is durable before the first effect. Recovery accepts each file only
    # at its recorded before/after revision, never silently overwrites divergence.
    atomic_text(journal, json.dumps({"edits": planned}, ensure_ascii=False))
    reconcile_edits(root, allowed, journal)


def reconcile_edits(root: Path, allowed: list[str], journal: Path):
    if not journal.exists():
        return
    plan = json.loads(journal.read_text(encoding="utf-8"))
    for change in plan["edits"]:
        path = scoped_path(root, change["path"], allowed)
        current = digest(path.read_bytes()) if path.exists() else None
        if current not in {change["before"], change["after"]}:
            raise GateError("partial edit recovery conflicts with external changes")
    for change in plan["edits"]:
        path = scoped_path(root, change["path"], allowed)
        if (digest(path.read_bytes()) if path.exists() else None) != change["after"]:
            atomic_text(path, change["content"])
    # Keep the journal as evidence; caller checkpoints its consumption.


def commit_delivery(root: Path, base: str, allowed: list[str], run_id: str, receipt_hash: str):
    audit_scope(root, base, allowed)
    token = f"Project-Creator-Receipt: {run_id}/{receipt_hash}"
    history = git(root, "log", "-20", "--format=%H%n%B")
    if token in history and not git(root, "status", "--porcelain"):
        return git(root, "rev-parse", "HEAD")
    existing = [x for x in allowed if (root / x).is_file()]
    if existing:
        git(root, "add", "--", *existing)
    if git(root, "diff", "--cached", "--name-only"):
        git(root, "commit", "-m", "Implement verified project slice\n\n" + token)
    return git(root, "rev-parse", "HEAD")
