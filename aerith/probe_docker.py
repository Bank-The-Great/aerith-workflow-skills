"""Explicit synthetic containment test; writes evidence only to the chosen host directory."""
import argparse
import json
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from project_creator.contracts import GateError, digest
from project_creator.docker_sandbox import DockerSandbox
from project_creator.store import atomic_text
from project_creator.processes import execute


def probe(executable, image, evidence, daemon_host, daemon_id):
    cfg = {"kind": "docker", "argv": [str(executable.resolve())], "image": image, "timeout_seconds": 30,
           "daemon_host": daemon_host, "daemon_id": daemon_id}
    with tempfile.TemporaryDirectory(prefix="project-creator-proof-") as tmp:
        root = Path(tmp)
        outside = root / "outside.txt"
        outside.write_text("synthetic outside canary", encoding="utf-8")
        worktree = root / "worktree"
        worktree.mkdir()
        program = '''import json, pathlib, socket
out = {}
outside = pathlib.Path('/excluded-host/outside.txt')
try:
    outside.read_bytes()
    out['unmounted_read_unavailable'] = False
except OSError:
    out['unmounted_read_unavailable'] = True
try:
    outside.write_text('unexpected')
    out['unmounted_write_denied'] = False
except OSError:
    out['unmounted_write_denied'] = True
try:
    pathlib.Path('/workspace/source.py').write_text('unexpected')
    out['source_write_denied'] = False
except OSError:
    out['source_write_denied'] = True
try:
    socket.create_connection(('1.1.1.1',443), timeout=2).close()
    out['network_denied'] = False
except OSError:
    out['network_denied'] = True
out['docker_socket_absent'] = not pathlib.Path('/var/run/docker.sock').exists()
out['private_git_absent'] = not pathlib.Path('/workspace/.git').exists()
print(json.dumps(out))
'''
        (worktree / "source.py").write_text(program, encoding="utf-8")
        runner = DockerSandbox(cfg, ["source.py"])
        result = runner.run(["python", "source.py"], worktree, "containment")
        if result.returncode:
            raise GateError("synthetic containment program failed")
        cases = json.loads(result.stdout)
        if not cases or not all(x is True for x in cases.values()):
            raise GateError("synthetic containment denied-case failed")
        # Host paths are not Linux paths. Host exclusion is proved from the
        # daemon's inspected exact mount set, not by passing a Windows pathname
        # to Linux pathlib and mistaking the resulting missing path for proof.
        cases["outside_read_denied"] = runner.last_inspection["mount_set_exact"] and cases["unmounted_read_unavailable"]
        cases["outside_write_denied"] = runner.last_inspection["mount_set_exact"] and runner.last_inspection["root_read_only"] and cases["source_write_denied"]
        timeout_cfg = cfg | {"timeout_seconds": 3}
        timeout_runner = DockerSandbox(timeout_cfg, ["source.py"])
        name, owner = timeout_runner.identity(worktree, "cleanup")
        observed = {"child_started": False}
        finished = threading.Event()
        def watch_child():
            while not finished.wait(0.2):
                process_list = timeout_runner.command(["top", name, "-eo", "pid,ppid,comm"], worktree, timeout=2)
                rows = [line.split() for line in process_list.stdout.splitlines()[1:]]
                processes = [x for x in rows if len(x) == 3 and x[0].isdigit() and x[1].isdigit() and x[2].startswith("python")]
                pids = {x[0] for x in processes}
                if not process_list.returncode and any(x[1] in pids for x in processes):
                    observed["child_started"] = True
                    return
        watcher = threading.Thread(target=watch_child)
        watcher.start()
        timeout_fired = False
        try:
            timeout_runner.run(["python", "-c", "import subprocess,time; subprocess.Popen(['python','-c',\"import time; print('descendant-ready',flush=True); time.sleep(60)\"]); time.sleep(60)"], worktree, "cleanup")
        except GateError as exc:
            if str(exc) != "process timeout":
                raise
            timeout_fired = True
        finally:
            finished.set()
            watcher.join(timeout=5)
        listing = timeout_runner.command(["ps", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.Names}}"], worktree, timeout=10)
        if not timeout_fired or not observed["child_started"] or listing.returncode or listing.stdout.strip():
            raise GateError("deadline/descendant cleanup proof incomplete")
        cases["child_cleanup"] = True
        if outside.read_text(encoding="utf-8") != "synthetic outside canary":
            raise GateError("outside canary changed")
        harness_hash = digest(Path(__file__).read_bytes())
        executable_sha256 = digest(executable.read_bytes())
        runtime = Path(__file__).resolve().parent / "project_creator" / "docker_sandbox.py"
        runtime_files = {str(runtime): digest(runtime.read_bytes())}
        payload = {"schema_version": 1, "purpose": "verification", "probe_harness_sha256": harness_hash,
                   "checked_at": datetime.now(timezone.utc).isoformat(), "image": image,
                   "configuration_hash": digest(cfg), "cases": cases,
                   "executable_sha256": executable_sha256, "runtime_files": runtime_files,
                   "daemon_inspection": runner.last_inspection,
                   "daemon_id": daemon_id, "container_id": runner.last_container_id,
                   "mounts_sha256": runner.last_mounts_hash, "source_packet_sha256": digest(program),
                   "host_exclusion_basis": "exact individually locked read-only file mounts, no containing worktree/other bind/volume, read-only root, isolated network; host canary is never mounted",
                   "test_kind": "real Docker process, synthetic data, no model"}
        evidence.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(payload, indent=2) + "\n"
        report = evidence / "docker-containment.json"
        atomic_text(report, raw)
        report_hash = digest(raw)
        cfg["proof"] = {"schema_version": 1, "probe_harness_sha256": harness_hash,
                        "checked_at": payload["checked_at"], "configuration_hash": payload["configuration_hash"],
                        "executable_sha256": executable_sha256,
                        "runtime_files": runtime_files,
                        "evidence_files": {report_hash: str(report.resolve())},
                        "cases": {key: {"passed": True, "expected": True, "observed": True,
                                       "evidence_sha256": report_hash} for key in cases}}
        output = evidence / "docker-adapter.json"
        atomic_text(output, json.dumps(cfg, indent=2) + "\n")
        return {"status": "passed", "cases": cases, "adapter": str(output.resolve()), "evidence_sha256": report_hash}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--docker", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--daemon-host", required=True)
    parser.add_argument("--daemon-id", required=True)
    args = parser.parse_args()
    print(json.dumps(probe(args.docker, args.image, args.evidence, args.daemon_host, args.daemon_id)))
