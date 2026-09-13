"""Frozen source packets and journalled, exactly-scoped worktree edits."""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from .contracts import GateError, digest, safe_relative, safe_text, scoped_path
from .processes import (assert_non_augmentable_directory, execute, minimal_environment,
                        reviewed_files, stable_directory, _same_windows_path,
                        _windows_final_path)
from .store import atomic_text


_GIT = None


def configure_git(config: dict):
    """Bind Git to one host-admitted executable; model/run input cannot set it."""
    global _GIT
    if not isinstance(config, dict) or set(config) != {"executable", "sha256"}:
        raise GateError("host Git admission requires executable and sha256")
    executable = Path(config["executable"])
    expected = config["sha256"]
    if (not executable.is_absolute() or executable.suffix.lower() != ".exe"
            or not isinstance(expected, str) or len(expected) != 64
            or digest(executable.read_bytes()) != expected.lower()):
        raise GateError("host Git executable is absent or changed")
    assert_non_augmentable_directory(executable.parent)
    normalized = {"executable": str(executable), "sha256": expected.lower()}
    if _GIT is not None and _GIT != normalized:
        raise GateError("host Git admission cannot change after bootstrap")
    _GIT = normalized


def _git_environment(extra=None):
    env = minimal_environment()
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "",
        "GIT_EDITOR": "",
    })
    if extra:
        env.update(extra)
    return env


def git(root: Path, *args: str, stdin="", strip=True, extra_env=None) -> str:
    if _GIT is None:
        raise GateError("host Git executable is not admitted")
    root = Path(root)
    if not root.is_absolute() or not root.exists():
        raise GateError("Git root must be an existing absolute path")
    argv = [_GIT["executable"],
            "-c", "core.hooksPath=NUL",
            "-c", "commit.gpgsign=false",
            "-c", "core.fsmonitor=false",
            "-c", "maintenance.auto=false",
            "-c", "gc.auto=0",
            "-C", str(root), *args]
    result = execute(argv, cwd=root, stdin=stdin, timeout=60,
                     env=_git_environment(extra_env), max_bytes=4_000_000,
                     expected_executable_sha256=_GIT["sha256"], system_cwd=True)
    if result.returncode:
        operation = args[0] if args else "unknown"
        raise GateError("Git operation failed: " + operation)
    return result.stdout.strip() if strip else result.stdout


def _revision(value: str) -> str:
    if not isinstance(value, str) or len(value) not in {40, 64} or any(x not in "0123456789abcdef" for x in value):
        raise GateError("Git revision must be an exact object id")
    return value


def _tree_blob(root: Path, revision: str, name: str):
    revision, name = _revision(revision), safe_relative(name)
    row = git(root, "ls-tree", revision, "--", name)
    if not row or "\n" in row or "\t" not in row:
        raise GateError("approved source must be one existing tracked file")
    meta, observed_name = row.split("\t", 1)
    fields = meta.split()
    if len(fields) != 3 or observed_name != name or fields[1] != "blob" or fields[0] not in {"100644", "100755"}:
        raise GateError("approved source is not a regular tracked file")
    content = safe_text(git(root, "cat-file", "blob", fields[2], strip=False))
    return fields[0], fields[2], content


def source_scope_clean(project: Path, base: str, names: list[str]):
    """Require exact existing UTF-8 blob matches; no filter/helper is invoked."""
    with stable_directory(project):
        for name in sorted(set(names)):
            path = scoped_path(project, name, names)
            if not path.is_file():
                raise GateError("first safe release accepts existing tracked files only")
            _, _, content = _tree_blob(project, base, name)
            try:
                current = safe_text(path.read_bytes().decode("utf-8"))
            except UnicodeError as exc:
                raise GateError("source file is not UTF-8 text") from exc
            if digest(current) != digest(content):
                raise GateError("selected source scope differs from exact committed blobs")


def make_worktree(project: Path, target: Path, branch: str, base: str, read_set: list[str]):
    base = _revision(base)
    if target.exists():
        if git(target, "rev-parse", "--abbrev-ref", "HEAD") != branch:
            raise GateError("existing workspace does not match run branch")
        return
    git(project, "worktree", "add", "--no-checkout", "-b", branch, str(target), base)
    with stable_directory(target.resolve()):
        for name in sorted(set(read_set)):
            _, _, content = _tree_blob(project, base, name)
            path = scoped_path(target, name, read_set)
            atomic_text(path, content)


def snapshot(root: Path, read_set: list[str]) -> dict:
    files = {}
    with stable_directory(root.resolve()):
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
        head = git(root, "rev-parse", "HEAD")
    return {"files": files, "file_sha256": {k: digest(v) if v is not None else None for k, v in files.items()},
            "hash": digest(files), "head": head}


def project_lock(project: Path) -> Path:
    common = Path(git(project, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    identity = str(common.resolve())
    if os.name == "nt":
        identity = identity.casefold()
    return Path(tempfile.gettempdir()) / "project-creator-locks" / (digest(identity) + ".lock")


def _visible_files(root: Path):
    files = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative == ".git":
            continue
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise GateError("worktree contains a link or junction")
        if path.is_file():
            files.add(relative)
        elif not path.is_dir():
            raise GateError("worktree contains a non-regular entry")
    return files


def audit_scope(root: Path, base: str, allowed: list[str], read_set: list[str]):
    with stable_directory(root.resolve()):
        visible = _visible_files(root)
        if visible != set(read_set):
            raise GateError("realized worktree file set differs from the exact source scope")
        for name in set(read_set) - set(allowed):
            _, _, original = _tree_blob(root, base, name)
            if digest((root / name).read_bytes()) != digest(original):
                raise GateError("read-only source changed")


def _overwrite_existing(path: Path, expected: str, content: str):
    raw = content.encode("utf-8")
    if os.name == "nt":
        import ctypes
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
                                    0x00000001, None, 3, 0x80 | 0x00200000, None)
        if handle == invalid:
            raise GateError("could not lock existing scoped file for update")
        class AttributeTag(ctypes.Structure):
            _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]
        tag = AttributeTag()
        if (not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag))
                or tag.FileAttributes & 0x400
                or not _same_windows_path(_windows_final_path(handle), path)):
            kernel.CloseHandle(handle)
            raise GateError("scoped file identity is not the approved regular path")
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    else:
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise GateError("scoped file is not regular")
    with os.fdopen(descriptor, "r+b", closefd=True) as stream:
        before = stream.read()
        if digest(before) != expected:
            raise GateError("source identity changed before handle-bound update")
        stream.seek(0)
        stream.write(raw)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
        stream.seek(0)
        if digest(stream.read()) != digest(raw):
            raise GateError("handle-bound source update could not be verified")


def propose_edits(root: Path, changes: list, allowed: list[str], journal: Path):
    if not isinstance(changes, list) or not changes:
        raise GateError("implementation contains no proposed edits")
    planned, seen = [], set()
    with stable_directory(root.resolve()):
        for change in changes:
            if not isinstance(change, dict):
                raise GateError("proposed edit must be an object")
            name = change.get("path")
            path = scoped_path(root, name, allowed)
            if name.casefold() in seen:
                raise GateError("duplicate or case-colliding edit")
            seen.add(name.casefold())
            if not path.is_file():
                raise GateError("first safe release may update existing files only")
            content = safe_text(change.get("content"))
            before_content = safe_text(path.read_bytes().decode("utf-8"))
            before = digest(before_content)
            if change.get("expected_sha256") != before:
                raise GateError("source changed before proposal application")
            planned.append({"path": name, "before": before, "before_content": before_content,
                            "after": digest(content), "content": content})
        atomic_text(journal, json.dumps({"edits": planned}, ensure_ascii=False))
        reconcile_edits(root, allowed, journal)


def reconcile_edits(root: Path, allowed: list[str], journal: Path):
    if not journal.exists():
        return
    plan = json.loads(journal.read_text(encoding="utf-8"))
    with stable_directory(root.resolve()):
        for change in plan["edits"]:
            path = scoped_path(root, change["path"], allowed)
            if not path.is_file():
                raise GateError("scoped existing file disappeared during recovery")
            current = digest(path.read_bytes())
            if current not in {change["before"], change["after"]}:
                raise GateError("partial edit recovery conflicts with external changes")
        for change in plan["edits"]:
            path = scoped_path(root, change["path"], allowed)
            if digest(path.read_bytes()) != change["after"]:
                _overwrite_existing(path, change["before"], change["content"])


def _verify_commit(root: Path, commit: str, allowed: list[str], expected_files: dict):
    changed = set(filter(None, git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()))
    if not changed or not changed <= set(allowed):
        raise GateError("delivery commit differs outside approved write scope")
    for name, expected in expected_files.items():
        _, _, content = _tree_blob(root, commit, name)
        if digest(content) != expected:
            raise GateError("delivery commit blob differs from verified source")


def commit_delivery(root: Path, base: str, allowed: list[str], read_set: list[str],
                    run_id: str, receipt_hash: str, expected_files: dict):
    audit_scope(root, base, allowed, read_set)
    token = f"Project-Creator-Receipt: {run_id}/{receipt_hash}"
    head = git(root, "rev-parse", "HEAD")
    if head != base:
        body = git(root, "cat-file", "commit", head, strip=False)
        if token not in body:
            raise GateError("delivery branch advanced without the expected receipt")
        _verify_commit(root, head, allowed, expected_files)
        return head
    locks = {str(root / name): expected_files[name] for name in read_set}
    with stable_directory(root.resolve()), reviewed_files(locks), tempfile.TemporaryDirectory(prefix="project-creator-index-") as temporary:
        index = str(Path(temporary) / "index")
        extra = {"GIT_INDEX_FILE": index,
                 "GIT_AUTHOR_NAME": "Aerith Project Creator",
                 "GIT_AUTHOR_EMAIL": "aerith-project-creator@localhost",
                 "GIT_COMMITTER_NAME": "Aerith Project Creator",
                 "GIT_COMMITTER_EMAIL": "aerith-project-creator@localhost"}
        git(root, "read-tree", base, extra_env=extra)
        for name in sorted(allowed):
            mode, _, _ = _tree_blob(root, base, name)
            content = safe_text((root / name).read_bytes().decode("utf-8"))
            if digest(content) != expected_files.get(name):
                raise GateError("verified source changed before object creation")
            blob = git(root, "hash-object", "-w", "--stdin", stdin=content, extra_env=extra)
            git(root, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{name}", extra_env=extra)
        tree = git(root, "write-tree", extra_env=extra)
        message = "Implement verified project slice\n\n" + token + "\n"
        commit = git(root, "commit-tree", tree, "-p", base, stdin=message, extra_env=extra)
        git(root, "update-ref", "HEAD", commit, base)
        git(root, "read-tree", commit)
    _verify_commit(root, commit, allowed, expected_files)
    return commit
