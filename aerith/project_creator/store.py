"""Durable run ledger. SQLite is the only owner of mutable lifecycle state."""
from __future__ import annotations

import contextlib
import ctypes
import json
import os
import re
import sqlite3
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .contracts import GateError, digest
from .processes import _same_windows_path, _windows_final_path, stable_directory


_ATOMIC_TEMPORARY = re.compile(r"^\.aerith-atomic-[0-9a-f]{32}\.tmp$")


def now():
    return datetime.now(timezone.utc).isoformat()


def _before_atomic_replace():
    """Test seam for proving that a pre-replace process death preserves old state."""


class Store:
    def __init__(self, root: Path, *, create=False, read_only=False):
        self.root = root.resolve()
        db = self.root / "ledger.sqlite3"
        if not create and not db.is_file():
            raise GateError("run ledger does not exist")
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        if not read_only:
            recover_atomic_temporaries(self.root)
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


def _delete_atomic_temporary_windows(path: Path, expected_parent_identity: str):
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    invalid = wintypes.HANDLE(-1).value
    handle = kernel.CreateFileW(
        str(path), 0x00010000 | 0x00000080, 0, None, 3, 0x00200000, None)
    if handle == invalid:
        if ctypes.get_last_error() == 32:
            return False
        raise GateError("atomic temporary could not be opened for recovery")
    try:
        if not _same_windows_path(_windows_final_path(handle), path):
            raise GateError("atomic temporary identity changed during recovery")
        observed_parent = os.stat(path.parent, follow_symlinks=False)
        if f"{observed_parent.st_dev}:{observed_parent.st_ino}" != expected_parent_identity:
            raise GateError("atomic temporary parent changed during recovery")
        attributes = kernel.GetFileAttributesW(str(path))
        if attributes == 0xFFFFFFFF or attributes & 0x400:
            raise GateError("atomic temporary is a reparse point")
        disposition = wintypes.BOOL(1)
        if not kernel.SetFileInformationByHandle(
                handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            raise GateError("atomic temporary recovery failed")
    finally:
        kernel.CloseHandle(handle)
    return True


def recover_atomic_temporaries(root: Path):
    """Remove only this controller's abandoned private atomic temporaries."""
    root = Path(root)
    if not root.is_dir():
        return 0
    recovered = 0
    for current, directories, files in os.walk(root, followlinks=False):
        parent = Path(current)
        directories[:] = [
            name for name in directories
            if not (parent / name).is_symlink()
            and not (hasattr(parent / name, "is_junction")
                     and (parent / name).is_junction())
        ]
        observed_parent = os.stat(parent, follow_symlinks=False)
        parent_identity = f"{observed_parent.st_dev}:{observed_parent.st_ino}"
        for name in files:
            if not _ATOMIC_TEMPORARY.fullmatch(name):
                continue
            candidate = parent / name
            if os.name == "nt":
                recovered += int(_delete_atomic_temporary_windows(
                    candidate, parent_identity))
                continue
            parent_descriptor = os.open(
                parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                current_parent = os.fstat(parent_descriptor)
                if f"{current_parent.st_dev}:{current_parent.st_ino}" != parent_identity:
                    raise GateError("atomic temporary parent changed during recovery")
                descriptor = os.open(
                    name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_descriptor)
                try:
                    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                        raise GateError("atomic temporary is not a regular file")
                    os.unlink(name, dir_fd=parent_descriptor)
                    recovered += 1
                finally:
                    os.close(descriptor)
            finally:
                os.close(parent_descriptor)
    return recovered


def _atomic_text_windows(path: Path, raw: bytes, temporary: Path,
                         expected_parent_identity: str | None):
    """Replace atomically while holding and rechecking one opened parent identity."""
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    invalid = wintypes.HANDLE(-1).value
    directory_flags = 0x02000000 | 0x00200000
    parent_handle, handle, descriptor = None, None, None
    renamed = False
    try:
        # Create an empty temporary before reading private bytes. Then bind it
        # to the opened parent identity. If the pathname was swapped before the
        # parent was opened, the two final paths differ and no private data has
        # been written. After this check, both the source handle and the rename
        # destination are handle-relative to the same directory object.
        handle = kernel.CreateFileW(
            str(temporary), 0x80000000 | 0x40000000 | 0x00010000,
            0x00000001 | 0x00000002 | 0x00000004,
            None, 1, 0x80 | 0x00200000, None)
        if handle == invalid:
            handle = None
            raise GateError("could not create atomic state temporary")
        if not _same_windows_path(_windows_final_path(handle), temporary):
            raise GateError("atomic state temporary identity changed")
        parent_handle = kernel.CreateFileW(
            str(path.parent), 0x80000000,
            0x00000001 | 0x00000002 | 0x00000004,
            None, 3, directory_flags, None)
        if parent_handle == invalid:
            parent_handle = None
            raise GateError("could not open atomic state parent")
        attributes = kernel.GetFileAttributesW(str(path.parent))
        if (attributes == 0xFFFFFFFF or attributes & 0x400
                or not _same_windows_path(_windows_final_path(parent_handle), path.parent)
                or not _same_windows_path(_windows_final_path(handle), temporary)):
            raise GateError("atomic state parent identity changed")
        observed_parent = os.stat(path.parent, follow_symlinks=False)
        observed_identity = f"{observed_parent.st_dev}:{observed_parent.st_ino}"
        if (expected_parent_identity is not None
                and observed_identity != expected_parent_identity):
            raise GateError("atomic state parent identity changed")
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        handle = None
        with os.fdopen(descriptor, "r+b", closefd=True) as stream:
            descriptor = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            if stream.read() != raw:
                raise GateError("atomic state temporary could not be verified")

            class FileRenameInfo(ctypes.Structure):
                _fields_ = [("ReplaceIfExists", wintypes.BOOL),
                            ("RootDirectory", wintypes.HANDLE),
                            ("FileNameLength", wintypes.DWORD),
                            ("FileName", wintypes.WCHAR * 1)]

            destination = str(path).encode("utf-16-le")
            size = FileRenameInfo.FileName.offset + len(destination) + 2
            buffer = ctypes.create_string_buffer(size)
            info = FileRenameInfo.from_buffer(buffer)
            info.ReplaceIfExists = 1
            info.RootDirectory = None
            info.FileNameLength = len(destination)
            ctypes.memmove(ctypes.addressof(buffer) + FileRenameInfo.FileName.offset,
                           destination, len(destination))
            native = msvcrt.get_osfhandle(stream.fileno())
            _before_atomic_replace()
            if not kernel.SetFileInformationByHandle(native, 3, buffer, size):
                raise GateError("atomic state replacement failed")
            renamed = True
            if (not _same_windows_path(_windows_final_path(parent_handle), path.parent)
                    or not _same_windows_path(_windows_final_path(native), path)):
                raise GateError("atomic state replacement identity changed")
            stream.seek(0)
            if stream.read() != raw:
                raise GateError("atomic state replacement could not be verified")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        elif handle is not None:
            kernel.CloseHandle(handle)
        if parent_handle is not None:
            kernel.CloseHandle(parent_handle)
        if not renamed:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def atomic_text(path: Path, text: str, *, expected_parent_identity: str | None = None):
    """Durably replace one state file without exposing a truncated canonical file."""
    path = Path(os.path.abspath(str(path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = text.encode("utf-8")
    temporary = path.parent / (".aerith-atomic-" + uuid.uuid4().hex + ".tmp")
    if os.name == "nt":
        _atomic_text_windows(path, raw, temporary, expected_parent_identity)
        return
    renamed = False
    with stable_directory(path.parent):
        observed_parent = os.stat(path.parent, follow_symlinks=False)
        observed_identity = f"{observed_parent.st_dev}:{observed_parent.st_ino}"
        if (expected_parent_identity is not None
                and observed_identity != expected_parent_identity):
            raise GateError("atomic state parent identity changed")
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR |
                             getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise GateError("atomic state temporary is not a regular file")
            with os.fdopen(descriptor, "r+b", closefd=True) as stream:
                descriptor = None
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
                stream.seek(0)
                if stream.read() != raw:
                    raise GateError("atomic state temporary could not be verified")
            _before_atomic_replace()
            os.replace(temporary, path)
            renamed = True
            parent = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if not renamed:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
