"""Networkless, non-root verification over a copied exact source set.

Only a temporary source packet is mounted, read-only. Neither the Git metadata,
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
from .processes import execute, minimal_environment


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
                           timeout=timeout, cancelled=cancelled, env=env)

    def verify_daemon(self, cwd):
        result = self.command(["info", "--format", '{{json .ID}} {{json .OSType}}'], cwd)
        if result.returncode or result.stdout.strip() != json.dumps(self.config["daemon_id"]) + ' "linux"':
            raise GateError("Docker daemon identity changed or is unavailable")

    def verify_container(self, name, source, command, cwd):
        result = self.command(["inspect", "--format", "{{json .}}", name], cwd)
        if result.returncode:
            raise GateError("created test container cannot be inspected")
        value = json.loads(result.stdout)
        host, cfg = value.get("HostConfig", {}), value.get("Config", {})
        mounts = value.get("Mounts", [])
        expected = str(source.resolve()).replace("\\", "/")
        candidates = {expected}
        if re.match(r"^[A-Za-z]:/", expected):
            drive_path = expected[0].lower() + expected[2:]
            candidates |= {"/run/desktop/mnt/host/" + drive_path, "/host_mnt/" + drive_path}
        bind = [x for x in mounts if x.get("Type") == "bind"]
        others = [x for x in mounts if x.get("Type") != "bind"]
        checks = {
            "container_identity": value.get("Id") == name and value.get("State", {}).get("Running") is False,
            "mount_set_exact": len(bind) == 1 and not others and bind[0].get("Destination") == "/workspace"
                and bind[0].get("Source", "").replace("\\", "/").casefold() in {x.casefold() for x in candidates}
                and bind[0].get("RW") is False,
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

    def run(self, command, worktree, test_id):
        if self.cancelled():
            raise GateError("cancelled before container launch")
        if not isinstance(command, list) or not command or not command[0] or not all(isinstance(x, str) and "\x00" not in x for x in command):
            raise GateError("invalid verification argv")
        name, owner = self.identity(worktree, test_id)
        self.remove_owned(name, owner, worktree)
        image = self.command(["image", "inspect", "--format", "{{json .RepoDigests}}", self.image], worktree)
        if image.returncode:
            raise GateError("pinned verification image is not local; automatic pull forbidden")
        with tempfile.TemporaryDirectory(prefix="project-creator-packet-") as temporary:
            source = Path(temporary)
            for relative in self.read_set:
                original = scoped_path(worktree, relative, self.read_set)
                if original.exists():
                    if not original.is_file() or original.stat().st_size > 500_000:
                        raise GateError("verification input is not bounded regular text")
                    target = source / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(original.read_bytes())
                    target.chmod(0o644)
            args = ["create", "--pull=never", "--rm", "--name", name,
                    "--label", "project-creator.owner=" + owner,
                    "--network", "none", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges:true", "--pids-limit", "128",
                    "--no-healthcheck", "--log-driver", "none",
                    "--memory", "512m", "--cpus", "1", "--user", "65534:65534",
                    "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--workdir", "/workspace",
                    "--mount", "type=bind,source=" + str(source) + ",target=/workspace,readonly",
                    "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "PYTHONUNBUFFERED=1",
                    "--entrypoint", command[0], self.image, *command[1:]]
            # Source paths are host-owned; commas are Docker mount separators.
            if "," in str(source):
                raise GateError("temporary source path cannot be represented safely")
            container_id = name
            try:
                created = self.command(args, worktree, timeout=30, cancelled=self.cancelled)
                if created.returncode:
                    raise GateError("test container creation failed")
                if not re.fullmatch(r"[a-f0-9]{64}", created.stdout.strip()):
                    raise GateError("test container omitted its immutable identity")
                container_id = created.stdout.strip()
                self.verify_container(container_id, source, command, worktree)
                return self.command(["start", "--attach", container_id], worktree,
                                    timeout=self.config.get("timeout_seconds", 600), cancelled=self.cancelled)
            finally:
                self.remove_owned(container_id, owner, worktree)
