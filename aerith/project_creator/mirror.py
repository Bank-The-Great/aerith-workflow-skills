"""Private GitHub mirror with read-back and no blind uncertain-write retries."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .contracts import GateError, digest, safe_text
from .processes import execute


class GitHub:
    def __init__(self, executable: str, repo: str, cwd: Path):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise GateError("invalid exact GitHub repository")
        self.executable, self.repo, self.cwd = executable, repo, cwd

    def api(self, endpoint, method="GET", data=None):
        argv = [self.executable, "api", "--method", method, f"repos/{self.repo}{endpoint}"]
        if data is not None:
            argv += ["--input", "-"]
        result = execute(argv, cwd=self.cwd, stdin=json.dumps(data) if data is not None else "", timeout=30)
        if result.returncode:
            raise GateError("GitHub request failed or outcome uncertain")
        try:
            value = json.loads(result.stdout)
        except ValueError as exc:
            raise GateError("GitHub response unavailable; outcome uncertain") from exc
        return value

    def private(self):
        meta = self.api("")
        if meta.get("full_name", "").casefold() != self.repo.casefold() or meta.get("private") is not True or not meta.get("has_issues"):
            raise GateError("mirror requires exact private repository with Issues enabled")

    def pages(self, endpoint):
        for page in range(1, 1001):
            rows = self.api(f"{endpoint}{'&' if '?' in endpoint else '?'}per_page=100&page={page}")
            if not isinstance(rows, list):
                raise GateError("invalid GitHub listing")
            yield from rows
            if len(rows) < 100:
                return
        raise GateError("GitHub pagination bound exceeded")


def marker(key):
    return f"<!-- project-creator:{key} -->"


def sync(store, github: GitHub, run_id: str):
    store.verify()
    # On real runs, revalidate source artifacts before any outbound request.
    for run in store.all():
        if run["id"] == run_id:
            for name, expected in run.get("artifacts", {}).items():
                path = store.root / run_id / (name + ".md")
                if not path.is_file() or digest(path.read_bytes()) != expected:
                    raise GateError("canonical artifact changed before mirror sync")
    github.private()
    rows = store.db.execute("SELECT * FROM outbox WHERE run_id=? ORDER BY key", (run_id,)).fetchall()
    for row in rows:
        if row["status"] in {"synced", "conflict"}:
            continue
        key, body, title = row["key"], safe_text(row["body"]), safe_text(row["title"])
        tag = marker(key)
        wanted = tag + "\n\n" + body
        try:
            if row["issue"] is None:
                matches = [x for x in github.pages("/issues?state=all") if not x.get("pull_request") and tag in (x.get("body") or "")]
                if len(matches) > 1:
                    raise GateError("duplicate mirror identity; reconciliation required")
                if not matches:
                    if row["status"] in {"sending", "unknown"}:
                        raise GateError("uncertain create not found; operator reconciliation required")
                    with store.db:
                        store.db.execute("UPDATE outbox SET status='sending' WHERE key=?", (key,))
                    issue = github.api("/issues", "POST", {"title": title, "body": wanted})
                else:
                    issue = matches[0]
                number = issue["number"]
                current = github.api(f"/issues/{number}")
                if current.get("body") != wanted:
                    with store.db:
                        store.db.execute("UPDATE outbox SET issue=?,status='conflict',error='managed body differs' WHERE key=?", (number, key))
                    continue
                with store.db:
                    store.db.execute("UPDATE outbox SET issue=?,synced_body=?,status='synced',error=NULL WHERE key=?", (number, wanted, key))
            else:
                number = row["issue"]
                current = github.api(f"/issues/{number}")
                # Initial issue body is immutable to us after creation. Later
                # revisions are comments, so remote concurrent edits cannot be
                # destroyed by a PATCH without a supported atomic CAS.
                if current.get("body") != row["synced_body"]:
                    with store.db:
                        store.db.execute("UPDATE outbox SET status='conflict',error='issue edited externally' WHERE key=?", (key,))
                    continue
                rev = marker(key + ":" + digest(body))
                updated = rev + "\n\n" + body
                comments = [x for x in github.pages(f"/issues/{number}/comments") if rev in (x.get("body") or "")]
                if len(comments) > 1 or (comments and comments[0].get("body") != updated):
                    raise GateError("mirror revision conflict")
                if not comments:
                    if row["status"] in {"sending", "unknown"}:
                        raise GateError("uncertain revision write needs reconciliation")
                    with store.db:
                        store.db.execute("UPDATE outbox SET status='sending' WHERE key=?", (key,))
                    comment = github.api(f"/issues/{number}/comments", "POST", {"body": updated})
                    if github.api(f"/issues/comments/{comment['id']}").get("body") != updated:
                        raise GateError("revision read-back differs")
                with store.db:
                    store.db.execute("UPDATE outbox SET status='synced',error=NULL WHERE key=?", (key,))
            store.audit(run_id, "mirror_synced", {"key": key, "body_hash": digest(body), "issue": number})
        except (GateError, KeyError) as exc:
            with store.db:
                store.db.execute("UPDATE outbox SET status='unknown',error=? WHERE key=?", (str(exc) if isinstance(exc, GateError) else "invalid GitHub response", key))
            store.audit(run_id, "mirror_pending", {"key": key})
    return [dict(x) for x in store.db.execute("SELECT key,issue,status,error FROM outbox WHERE run_id=? ORDER BY key", (run_id,))]
