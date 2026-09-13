"""Durable run ledger. SQLite is the only owner of mutable lifecycle state."""
from __future__ import annotations

import contextlib
import ctypes
import json
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

from .contracts import GateError, digest
from .processes import _same_windows_path, _windows_final_path, stable_directory


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root: Path, *, create=False, read_only=False):
        self.root = root.resolve()
        db = self.root / "ledger.sqlite3"
        if not create and not db.is_file():
            raise GateError("run ledger does not exist")
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db) if create else db.as_uri() + ("?mode=ro" if read_only else "?mode=rw"), uri=not create, timeout=10)
        self.db.row_factory = sqlite3.Row
        if not read_only:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
        if create:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, run_id TEXT,
              at TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL,
              previous TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (key TEXT PRIMARY KEY, run_id TEXT NOT NULL,
              body TEXT NOT NULL, title TEXT NOT NULL, issue INTEGER, synced_body TEXT,
              status TEXT NOT NULL DEFAULT 'pending', error TEXT);
            """)

    def close(self):
        self.db.close()

    def get(self, run_id):
        row = self.db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise GateError("unknown run")
        return json.loads(row[0])

    def all(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM runs ORDER BY id")]

    def event(self, run_id, kind, data):
        prev = self.db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        previous = prev[0] if prev else "0" * 64
        stamp, payload = now(), json.dumps(data, sort_keys=True, ensure_ascii=False)
        value = digest([run_id, stamp, kind, payload, previous])
        self.db.execute("INSERT INTO events(run_id,at,kind,data,previous,hash) VALUES(?,?,?,?,?,?)",
                        (run_id, stamp, kind, payload, previous, value))

    def save(self, run, kind="checkpoint", *, expected_revision=None):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            old = self.db.execute("SELECT data FROM runs WHERE id=?", (run["id"],)).fetchone()
            if old:
                old = json.loads(old[0])
                if expected_revision is not None and old["revision"] != expected_revision:
                    raise GateError("concurrent state change; reload required")
            run["revision"] = (old or {}).get("revision", 0) + 1
            run["updated_at"] = now()
            self.db.execute("INSERT OR REPLACE INTO runs VALUES(?,?)", (run["id"], json.dumps(run, ensure_ascii=False)))
            self.event(run["id"], kind, {"revision": run["revision"], "status": run["status"], "stage": run["stage"], "state_hash": digest(run)})

    def audit(self, run_id, kind, data):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.event(run_id, kind, data)

    def enqueue(self, run_id, key, title, body):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("""INSERT INTO outbox(key,run_id,title,body) VALUES(?,?,?,?)
              ON CONFLICT(key) DO UPDATE SET title=excluded.title, body=excluded.body,
              status=CASE WHEN outbox.body=excluded.body THEN outbox.status ELSE 'pending' END""",
                            (key, run_id, title, body))
            self.event(run_id, "outbox_payload", {"key": key, "payload_hash": digest([run_id, key, title, body])})

    def verify(self):
        previous = "0" * 64
        states = {}
        payloads = {}
        for row in self.db.execute("SELECT * FROM events ORDER BY seq"):
            expected = digest([row["run_id"], row["at"], row["kind"], row["data"], previous])
            if row["previous"] != previous or row["hash"] != expected:
                raise GateError("event ledger integrity failure")
            previous = row["hash"]
            data = json.loads(row["data"])
            if "state_hash" in data:
                states[row["run_id"]] = data["state_hash"]
            if row["kind"] == "outbox_payload":
                payloads[data["key"]] = data["payload_hash"]
        for run in self.all():
            if states.get(run["id"]) != digest(run):
                raise GateError("run state integrity failure")
        for row in self.db.execute("SELECT * FROM outbox"):
            if payloads.get(row["key"]) != digest([row["run_id"], row["key"], row["title"], row["body"]]):
                raise GateError("outbox payload integrity failure")
        return True


@contextlib.contextmanager
def exclusive(path: Path):
    """OS-held lock; process death releases it on Windows and POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise GateError("another worker owns this project") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def atomic_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with stable_directory(path.parent.resolve()):
        raw = text.encode("utf-8")
        if os.name == "nt":
            import msvcrt
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                           ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                           wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            invalid = wintypes.HANDLE(-1).value
            handle = kernel.CreateFileW(str(path), 0x80000000 | 0x40000000,
                                        0x00000001, None, 4, 0x80 | 0x00200000, None)
            if handle == invalid:
                raise GateError("could not open stable state file")
            class AttributeTag(ctypes.Structure):
                _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]
            tag = AttributeTag()
            if (not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag))
                    or tag.FileAttributes & 0x400 or tag.FileAttributes & 0x10
                    or not _same_windows_path(_windows_final_path(handle), path)):
                kernel.CloseHandle(handle)
                raise GateError("state target is not a regular non-reparse file")
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        else:
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                raise GateError("state target is not a regular file")
        with os.fdopen(descriptor, "r+b", closefd=True) as stream:
            stream.seek(0)
            stream.write(raw)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            if stream.read() != raw:
                raise GateError("stable state write could not be verified")
