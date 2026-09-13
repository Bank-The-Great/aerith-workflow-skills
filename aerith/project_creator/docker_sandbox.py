"""Networkless, non-root verification over individually locked source files.

Only exact reviewed files are mounted, read-only. Neither the Git metadata,
private run state, host home, Docker socket nor host environment is exposed.
Container identity is deterministic so recovery can reconcile a dead client's
container before rerunning a side-effect-free verification step.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .contracts import GateError, digest, scoped_path
from .processes import execute, minimal_environment, reviewed_files


class DockerSandbox:
    def __init__(self, config, read_set, *, cancelled=lambda: False):
        self.config, self.read_set, self.cancelled = config, read_set, cancelled
        argv = config.get("argv")
        if not isinstance(argv, list) or len(argv) != 1 or not Path(argv[0]).is_absolute():
            raise GateError("Docker requires one absolute native executable")
        self.exe = argv[0]
        self.image = config.get("image", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[a-f0-9]{64}", self.image):
            raise GateError("verification image must be digest-pinned")
        self.host = config.get("daemon_host", "")
        if not (re.fullmatch(r"npipe:////\./pipe/[A-Za-z0-9_-]+", self.host)
                or re.fullmatch(r"unix:///[^\s,]+", self.host)) or not config.get("daemon_id"):
            raise GateError("verification requires an explicitly pinned local Docker daemon")

    def command(self, args, cwd, *, timeout=15, cancelled=lambda: False):
        # No inherited remote context, TLS settings, registry credentials or
        # proxy configuration. This is a new empty client-config directory.
        env = minimal_environment()
        for key in list(env):
            if key.startswith("DOCKER_"):
                env.pop(key)
        with tempfile.TemporaryDirectory(prefix="project-creator-docker-client-") as config_dir:
            env["DOCKER_CONFIG"] = config_dir
            return execute([self.exe, "--host", self.host, *args], cwd=cwd,
                           timeout=timeout, cancelled=cancelled, env=env,
                           expected_executable_sha256=self.config.get("proof", {}).get("executable_sha256"),
                           expected_runtime_sha256=self.config.get("proof", {}).get("runtime_files", {}))

    def verify_daemon(self, cwd):
        result = self.command(["info", "--format", '{{json .ID}} {{json .OSType}}'], cwd)
        if result.returncode or result.stdout.strip() != json.dumps(self.config["daemon_id"]) + ' "linux"':
            raise GateError("Docker daemon identity changed or is unavailable")

    @staticmethod
    def _host_source_candidates(source):
        expected = str(source.resolve()).replace("\\", "/")
        candidates = {expected}
        if re.match(r"^[A-Za-z]:/", expected):
            drive_path = expected[0].lower() + expected[2:]
            candidates |= {"/run/desktop/mnt/host/" + drive_path, "/host_mnt/" + drive_path}
        return {value.casefold() for value in candidates}

    def verify_container(self, name, sources, command, cwd):
        result = self.command(["inspect", "--format", "{{json .}}", name], cwd)
        if result.returncode:
            raise GateError("created test container cannot be inspected")
        value = json.loads(result.stdout)
        host, cfg = value.get("HostConfig", {}), value.get("Config", {})
        mounts = value.get("Mounts", [])
        bind = [x for x in mounts if x.get("Type") == "bind"]
        others = [x for x in mounts if x.get("Type") != "bind"]
        realized = {(x.get("Destination"), x.get("Source", "").replace("\\", "/").casefold()): x
                    for x in bind}
        expected_mounts = {("/workspace/" + relative,
                            frozenset(self._host_source_candidates(source)))
                           for relative, source in sources.items()}
        mount_set_exact = len(bind) == len(expected_mounts) and not others
        for destination, candidates in expected_mounts:
            matches = [entry for (actual_destination, actual_source), entry in realized.items()
                       if actual_destination == destination and actual_source in candidates]
            mount_set_exact = mount_set_exact and len(matches) == 1 and matches[0].get("RW") is False
        checks = {
            "container_identity": value.get("Id") == name and value.get("State", {}).get("Running") is False,
            "mount_set_exact": mount_set_exact,
            "root_read_only": host.get("ReadonlyRootfs") is True,
            "network_none": host.get("NetworkMode") == "none",
            "non_root": cfg.get("User") == "65534:65534",
            "not_privileged": host.get("Privileged") is False and not host.get("Devices") and not host.get("CapAdd"),
            "capabilities_dropped": host.get("CapDrop") == ["ALL"],
            "no_new_privileges": "no-new-privileges:true" in host.get("SecurityOpt", []),
            "healthcheck_disabled": cfg.get("Healthcheck", {}).get("Test") == ["NONE"],
            "daemon_logging_disabled": host.get("LogConfig", {}).get("Type") == "none" and not host.get("LogConfig", {}).get("Config"),
            "bounded": host.get("Memory") == 512 * 1024 * 1024 and host.get("NanoCpus") == 1_000_000_000 and host.get("PidsLimit") == 128,
            "tmpfs_exact": host.get("Tmpfs") == {"/tmp": "rw,noexec,nosuid,size=64m"},
            "approved_command": cfg.get("Entrypoint") == [command[0]] and (cfg.get("Cmd") or []) == command[1:]
                and cfg.get("WorkingDir") == "/workspace" and cfg.get("Image") == self.image,
        }
        if not all(checks.values()):
            raise GateError("created test container differs from isolation policy: " + ",".join(k for k, v in checks.items() if not v))
        self.last_inspection = checks
        self.last_container_id = name
        self.last_mounts_hash = digest(mounts)

    def identity(self, worktree, test_id):
        owner = digest(str(worktree.resolve()))
        return "project-creator-" + owner[:16] + "-" + digest(test_id)[:12], owner

    def remove_owned(self, name, owner, cwd):
        self.verify_daemon(cwd)
        result = self.command(["inspect", "--format", '{"id":{{json .Id}},"labels":{{json .Config.Labels}}}', name], cwd)
        if result.returncode:
            # Absence is established separately from daemon failure.
            is_id = bool(re.fullmatch(r"[a-f0-9]{64}", name))
            selector = "id=" + name if is_id else "name=^/" + name + "$"
            listing = self.command(["ps", "-a", "--no-trunc", "--filter", selector, "--format", "{{.ID}}"], cwd)
            if listing.returncode or listing.stdout.strip():
                raise GateError("container ownership cannot be established")
            return
        identity = json.loads(result.stdout)
        labels = identity.get("labels")
        container_id = identity.get("id", "")
        if not isinstance(labels, dict) or labels.get("project-creator.owner") != owner:
            raise GateError("existing container has a different owner")
        if not re.fullmatch(r"[a-f0-9]{64}", container_id) or (re.fullmatch(r"[a-f0-9]{64}", name) and name != container_id):
            raise GateError("container identity cannot be established")
        removed = self.command(["rm", "--force", "--volumes", container_id], cwd, timeout=30)
        if removed.returncode:
            raise GateError("owned container cleanup failed; recovery required")

    def run(self, command, worktree, test_id, *, expected_files=None):
        if self.cancelled():
            raise GateError("cancelled before container launch")
        if not isinstance(command, list) or not command or not command[0] or not all(isinstance(x, str) and "\x00" not in x for x in command):
            raise GateError("invalid verification argv")
        name, owner = self.identity(worktree, test_id)
        self.remove_owned(name, owner, worktree)
        image = self.command(["image", "inspect", "--format", "{{json .RepoDigests}}", self.image], worktree)
        if image.returncode:
            raise GateError("pinned verification image is not local; automatic pull forbidden")
        sources = {}
        hashes = {}
        if expected_files is not None and (not isinstance(expected_files, dict)
                or set(expected_files) != set(self.read_set)):
            raise GateError("verification source hashes do not match the exact read set")
        for relative in sorted(set(self.read_set)):
            original = scoped_path(worktree, relative, self.read_set)
            expected = expected_files.get(relative) if expected_files is not None else None
            if original.exists():
                if not original.is_file() or original.stat().st_size > 500_000:
                    raise GateError("verification input is not bounded regular text")
                observed = digest(original.read_bytes())
                if expected_files is not None and observed != expected:
                    raise GateError("verification source changed after controller snapshot")
                if "," in str(original) or "," in relative:
                    raise GateError("source path cannot be represented safely")
                sources[relative] = original
                hashes[str(original)] = observed
            elif expected_files is not None and expected is not None:
                raise GateError("verification source disappeared after controller snapshot")
        if not sources:
            raise GateError("verification requires at least one present source file")
        with reviewed_files(hashes):
            args = ["create", "--pull=never", "--rm", "--name", name,
                    "--label", "project-creator.owner=" + owner,
                    "--network", "none", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true", "--pids-limit", "128",
                    "--no-healthcheck", "--log-driver", "none",
                    "--memory", "512m", "--cpus", "1", "--user", "65534:65534",
                    "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--workdir", "/workspace",
                    "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONUNBUFFERED=1",
            ]
            for relative, source in sources.items():
                args.extend(["--mount", "type=bind,source=" + str(source)
                             + ",target=/workspace/" + relative + ",readonly"])
            args.extend(["--entrypoint", command[0], self.image, *command[1:]])
            container_id = name
            try:
                created = self.command(args, worktree, timeout=30, cancelled=self.cancelled)
                if created.returncode:
                    raise GateError("test container creation failed")
                if not re.fullmatch(r"[a-f0-9]{64}", created.stdout.strip()):
                    raise GateError("test container omitted its immutable identity")
                container_id = created.stdout.strip()
                self.verify_container(container_id, sources, command, worktree)
                return self.command(["start", "--attach", container_id], worktree,
                                    timeout=self.config.get("timeout_seconds", 600), cancelled=self.cancelled)
            finally:
                self.remove_owned(container_id, owner, worktree)
