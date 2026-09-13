"""Fixed-argv UTF-8 transport with bounded output and owned process lifetime.

Unlike the incumbent host wrapper, public workers cannot import private host
code. This transport adds cancellation and byte limits and never logs stderr.
"""
from __future__ import annotations

import os
import hashlib
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .contracts import GateError


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str
    elapsed: float


def minimal_environment():
    """No preload/config/proxy/credential injection; normal native auth home only."""
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "TMPDIR",
               "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
               "PROGRAMFILES(X86)", "PROGRAMW6432", "USERNAME", "USER", "LOGNAME", "LANG", "LC_ALL"}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def _windows_job(proc, cancelled=lambda: False):
    """Assign a suspended child before it can spawn descendants, then resume."""
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(x, ctypes.c_ulonglong) for x in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    job = k.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    if not job or not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) or not k.AssignProcessToJobObject(job, int(proc._handle)):
        proc.kill()
        if job:
            k.CloseHandle(job)
        raise GateError("could not establish process-tree ownership")
    nt = ctypes.WinDLL("ntdll")
    nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
    nt.NtResumeProcess.restype = ctypes.c_long
    if cancelled():
        k.CloseHandle(job)
        proc.wait(timeout=10)
        raise GateError("cancelled before child resume")
    if nt.NtResumeProcess(int(proc._handle)) != 0:
        k.CloseHandle(job)
        raise GateError("could not resume owned child")
    return lambda: k.CloseHandle(job)


def _reparse_free(path: Path) -> bool:
    """Reject Windows reparse points in the executable's existing path chain."""
    if os.name != "nt":
        return not path.is_symlink()
    import ctypes
    invalid = 0xFFFFFFFF
    reparse = 0x400
    current = path
    while True:
        attributes = ctypes.windll.kernel32.GetFileAttributesW(str(current))
        if attributes == invalid or attributes & reparse:
            return False
        if current.parent == current:
            return True
        current = current.parent


def _locked_windows_executable(path: Path, expected_sha256: str):
    """Open and hash an executable while denying concurrent write/delete."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x00000001, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise GateError("could not lock reviewed executable")
    descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    stream = os.fdopen(descriptor, "rb", closefd=True)
    observed = hashlib.sha256(stream.read()).hexdigest()
    stream.seek(0)
    if observed != expected_sha256:
        stream.close()
        raise GateError("reviewed executable changed before launch")
    return stream


def execute(argv: list[str], *, cwd: Path, stdin="", timeout=600, cancelled=lambda: False,
            max_bytes=2_000_000, env=None, expected_executable_sha256=None) -> Result:
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and "\x00" not in x for x in argv):
        raise GateError("expected fixed argv")
    if Path(argv[0]).suffix.lower() in {".cmd", ".bat", ".ps1"}:
        raise GateError("resolve CLI to its native executable or node script; no shell wrapper")
    executable_lock = None
    if expected_executable_sha256 is not None:
        executable = Path(argv[0])
        if (not executable.is_absolute() or len(expected_executable_sha256) != 64
                or any(character not in "0123456789abcdef" for character in expected_executable_sha256)
                or not _reparse_free(executable)):
            raise GateError("reviewed executable path is not immutable")
        if os.name == "nt":
            executable_lock = _locked_windows_executable(executable, expected_executable_sha256)
        elif hashlib.sha256(executable.read_bytes()).hexdigest() != expected_executable_sha256:
            raise GateError("reviewed executable changed before launch")
    kwargs = {"cwd": str(cwd), "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000004 | 0x08000000  # SUSPENDED, NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    start = time.monotonic()
    if cancelled():
        raise GateError("cancelled before process launch")
    try:
        proc = subprocess.Popen(argv, **kwargs)
    except BaseException:
        if executable_lock:
            executable_lock.close()
        raise
    try:
        close_job = _windows_job(proc, cancelled) if os.name == "nt" else None
    except BaseException:
        proc.kill()
        proc.wait(timeout=10)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()
        if executable_lock:
            executable_lock.close()
        raise
    if executable_lock:
        executable_lock.close()
    chunks = [bytearray(), bytearray()]
    oversized = threading.Event()
    def reader(stream, dest):
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            if len(dest) + len(chunk) > max_bytes:
                oversized.set()
                return
            dest.extend(chunk)
    threads = [threading.Thread(target=reader, args=(stream, chunks[i]), daemon=True) for i, stream in enumerate((proc.stdout, proc.stderr))]
    for t in threads:
        t.start()
    def writer():
        try:
            proc.stdin.write(stdin.encode("utf-8"))
            proc.stdin.close()
        except (OSError, BrokenPipeError, ValueError):
            pass
    writer_thread = threading.Thread(target=writer, daemon=True)
    writer_thread.start()
    error = None
    try:
        while proc.poll() is None:
            if cancelled():
                error = "cancelled"
                break
            if oversized.is_set():
                error = "output byte limit"
                break
            if time.monotonic() - start > timeout:
                error = "process timeout"
                break
            time.sleep(0.1)
    finally:
        # Close ownership even after the original child exits: descendants must
        # not outlive a call or retain pipe handles across a worker checkpoint.
        if close_job:
            close_job()
        elif os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait(timeout=10)
        writer_thread.join(timeout=5)
        for t in threads:
            t.join(timeout=5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()
    if error or oversized.is_set():
        raise GateError(error or "output byte limit")
    try:
        return Result(proc.returncode, chunks[0].decode("utf-8"), chunks[1].decode("utf-8"), time.monotonic() - start)
    except UnicodeError as exc:
        raise GateError("non-UTF-8 process output") from exc
