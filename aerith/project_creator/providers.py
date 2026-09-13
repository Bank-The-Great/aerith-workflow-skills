"""Provider edges. Fresh invocations, explicit models, no implicit fallback."""
from __future__ import annotations

import json
import os
import tempfile
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .contracts import GateError, digest, safe_text
from .processes import execute, minimal_environment
from .output_schemas import schema_for, validate_output
from .provenance import parse_codex_data_only, parse_codex_provenance, parse_gemini_provenance

_HOST_CAPABILITIES = {}


def configure_host_capabilities(approved):
    """Host-bootstrap-only authority, never supplied by model/run configuration.

    A checksum is not approval. The trusted host pins the entire reviewed
    adapter (including its evidence) before any run configuration is consumed.
    Direct development entry points have no admission authority by default.
    """
    if not isinstance(approved, dict) or any(v not in {"provider", "verification"} for v in approved.values()):
        raise GateError("invalid host capability registry")
    _HOST_CAPABILITIES.clear()
    _HOST_CAPABILITIES.update(approved)


def json_object(text):
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise GateError("provider did not return a JSON object")
    return value


def provider_environment(config):
    # Native CLI login may use the normal user's account/keychain through its
    # HOME/APPDATA location. Runtime injection, alternate config directories,
    # proxies/custom CAs and script-preload variables are never inherited.
    env = minimal_environment()
    if config.get("auth_mode") == "claude-subscription":
        for key in list(env):
            if key.startswith("ANTHROPIC_") or key.startswith("CLAUDE_CODE_USE_"):
                env.pop(key)
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN",
                    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
            env.pop(key, None)
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def parse_claude_stream(text, requested):
    events = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not all(isinstance(x, dict) for x in events):
        raise GateError("invalid provider stream")
    # Treat every JSONL record as protocol, never as ignorable diagnostics.
    # In particular, a top-level `user`/`tool_result` record proves that the
    # supposedly data-only context performed or replayed an effect.
    if (not events or events[0].get("type") != "system"
            or events[-1].get("type") != "result"
            or any(x.get("type") not in {"system", "assistant", "result"} for x in events)
            or any(x.get("subtype") != "init" for x in events if x.get("type") == "system")
            or any(x.get("type") == "result" for x in events[:-1])):
        raise GateError("provider emitted an effect-bearing, unknown or out-of-order event")
    inits = [x for x in events if x.get("type") == "system" and x.get("subtype") == "init"]
    finals = [x for x in events if x.get("type") == "result"]
    if len(inits) != 1 or len(finals) != 1:
        raise GateError("provider stream needs one init and one result")
    init, final = inits[0], finals[0]
    assistant_events = [x for x in events if x.get("type") == "assistant"]
    messages = [x.get("message", {}) for x in assistant_events]
    if (not isinstance(final.get("modelUsage"), dict) or not all(isinstance(x, dict) for x in messages)
            or not all(isinstance(x.get("model"), str) and isinstance(x.get("content"), list)
                       and all(isinstance(block, dict) for block in x["content"]) for x in messages)):
        raise GateError("malformed provider message or model usage")
    models = sorted({x.get("model", "") for x in messages})
    if init.get("tools") != [] or init.get("mcp_servers") != [] or not init.get("session_id"):
        raise GateError("provider did not attest a tool-less fresh context")
    session = init.get("session_id")
    if not isinstance(session, str) or any(x.get("session_id") != session for x in [final, *assistant_events]):
        raise GateError("provider stream mixed session identities")
    if (init.get("model") != requested or models != [requested] or final.get("is_error") is not False
            or sorted(final.get("modelUsage", {})) != [requested]):
        raise GateError("actual provider model is absent or differs from the pinned model")
    if any(block.get("type") not in {"text", "thinking", "redacted_thinking"} for msg in messages for block in msg.get("content", [])):
        raise GateError("provider emitted an effect-bearing or unknown content block")
    return json_object(final["result"]), {"model": requested, "session_id": init["session_id"],
                                         "metered_models": sorted(final.get("modelUsage", {}))}


def validate_capability(config: dict, purpose: str, *, environment=None):
    if _HOST_CAPABILITIES.get(digest(config)) != purpose:
        raise GateError("capability is not pinned by the trusted host admission registry")
    proof = config.get("proof", {})
    if purpose == "provider" and proof.get("environment_hash") != digest(provider_environment(config) if environment is None else environment):
        raise GateError("provider auth-home/runtime environment changed after host review")
    expected = digest({k: v for k, v in config.items() if k != "proof"})
    if proof.get("configuration_hash") != expected:
        raise GateError(f"{purpose} configuration has no matching containment proof")
    try:
        checked = datetime.fromisoformat(proof["checked_at"])
        age = datetime.now(timezone.utc) - checked
        if checked.tzinfo is None or age < timedelta(0) or age > timedelta(days=30):
            raise ValueError()
    except (ValueError, KeyError, TypeError) as exc:
        raise GateError(f"{purpose} containment proof expired or absent") from exc
    required = {"outside_read_denied", "outside_write_denied", "network_denied", "child_cleanup"} if purpose == "verification" else {"fresh_context", "tools_disabled", "ambient_disabled", "child_cleanup", "model_attestation", "subscription_auth_only"}
    cases = proof.get("cases", {})
    if not all(isinstance(cases.get(key), dict) and cases[key].get("passed") is True
               and cases[key].get("expected") == cases[key].get("observed")
               and cases[key].get("evidence_sha256") for key in required):
        raise GateError(f"{purpose} containment cases incomplete")
    evidence = proof.get("evidence_files", {})
    if proof.get("schema_version") != 1:
        raise GateError("unsupported capability evidence schema")
    for case in required:
        value = cases[case]["evidence_sha256"]
        path = evidence.get(value)
        if not path or not Path(path).is_absolute():
            raise GateError(f"{purpose} retained test evidence absent or changed")
        raw = Path(path).read_bytes()
        if digest(raw) != value:
            raise GateError(f"{purpose} retained test evidence absent or changed")
        try:
            measured = json.loads(raw)
            measured_at = measured.get("checked_at", measured.get("at")) if isinstance(measured, dict) else None
            valid = (isinstance(measured, dict) and measured.get("schema_version") == 1
                     and measured.get("purpose") == purpose and measured.get("configuration_hash") == expected
                     and isinstance(measured.get("cases"), dict) and measured["cases"].get(case) is True
                     and measured.get("probe_harness_sha256") == proof.get("probe_harness_sha256")
                     and bool(measured.get("probe_harness_sha256"))
                     and measured_at == proof.get("checked_at")
                     and measured.get("executable_sha256") == proof.get("executable_sha256")
                     and measured.get("runtime_files") == proof.get("runtime_files", {}))
        except ValueError:
            valid = False
        if not valid:
            raise GateError("retained evidence does not substantiate the claimed capability")
    executable = Path(config["argv"][0])
    if not executable.is_absolute() or digest(executable.read_bytes()) != proof.get("executable_sha256"):
        raise GateError(f"{purpose} executable changed after proof")
    dependencies = proof.get("runtime_files", {})
    if config.get("kind") == "docker":
        runtime = Path(__file__).resolve().with_name("docker_sandbox.py")
        if dependencies.get(str(runtime)) != digest(runtime.read_bytes()):
            raise GateError("Docker runtime file missing from proof")
        harness = runtime.parents[1] / "probe_docker.py"
        if proof.get("probe_harness_sha256") != digest(harness.read_bytes()):
            raise GateError("Docker probe harness changed after host review")
    for arg in config["argv"][1:]:
        if Path(arg).suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".exe"} and arg not in dependencies:
            raise GateError(f"{purpose} script or binary missing from proof")
    runtime_bytes = {}
    for name, expected_hash in dependencies.items():
        if not Path(name).is_absolute():
            raise GateError(f"{purpose} runtime dependency changed after proof")
        raw = Path(name).read_bytes()
        if digest(raw) != expected_hash:
            raise GateError(f"{purpose} runtime dependency changed after proof")
        runtime_bytes[name] = raw
    return runtime_bytes


def _verified_docker_class(runtime_bytes):
    """Compile the exact Docker adapter bytes validated in this invocation."""
    path = Path(__file__).resolve().with_name("docker_sandbox.py")
    raw = runtime_bytes.get(str(path)) if isinstance(runtime_bytes, dict) else None
    if not isinstance(raw, bytes):
        raise GateError("validated Docker runtime bytes are unavailable")
    module = types.ModuleType("project_creator._validated_docker_sandbox")
    module.__file__ = str(path)
    module.__package__ = "project_creator"
    exec(compile(raw, str(path), "exec"), module.__dict__)
    cls = module.__dict__.get("DockerSandbox")
    if not isinstance(cls, type):
        raise GateError("validated Docker runtime has no sandbox class")
    return cls


def attest_model(envelope: dict, policy: dict, requested: str):
    """No adapter may report a requested model as an observed model."""
    value = envelope
    for key in policy.get("path", []):
        if not isinstance(value, dict) or key not in value:
            raise GateError("provider omitted actual model attestation")
        value = value[key]
    mode = policy.get("mode")
    observed = list(value) if mode == "keys" and isinstance(value, dict) else [value] if mode == "field" and isinstance(value, str) else []
    if observed != [requested]:
        raise GateError("actual provider model is absent or differs from the pinned model")
    return requested


class CLIProvider:
    def __init__(self, name: str, config: dict, *, audit, cancelled=lambda: False):
        self.name, self.config, self.audit, self.cancelled = name, config, audit, cancelled

    def invoke(self, stage: str, model: str, packet: dict, directory: Path) -> dict:
        required_auth = {"codex-provenance": "codex-subscription", "codex-data-only": "codex-subscription",
                         "gemini-provenance": "gemini-subscription"}
        if self.config.get("output") in required_auth and self.config.get("auth_mode") != required_auth[self.config["output"]]:
            raise GateError("isolated provenance adapters require subscription-only authentication")
        if self.config.get("output") == "claude-stream" and self.config.get("auth_mode") != "claude-subscription":
            raise GateError("Claude stream adapter requires subscription-only authentication")
        env = provider_environment(self.config)
        validate_capability(self.config, "provider", environment=env)
        if not self.config.get("attestation"):
            raise GateError("adapter lacks an actual-model attestation parser")
        argv = [arg.replace("{model}", model) for arg in self.config["argv"]]
        if not any(model in arg for arg in argv):
            raise GateError("provider does not explicitly select the pinned model")
        request_id = digest(packet)
        worker_packet = packet
        if self.config.get("output") == "codex-data-only":
            worker_packet = {
                "request_id": request_id,
                "instructions": packet.get("instructions", ""),
                "input": {key: value for key, value in packet.items() if key != "instructions"},
                "output_schema": schema_for(stage),
            }
        prompt = json.dumps(worker_packet, ensure_ascii=False)
        safe_text(prompt)
        self.audit("provider_start", {"vendor": self.name, "model_requested": model, "stage": stage,
                   "packet_hash": digest(packet), "adapter_hash": digest(self.config), "cost_class": "ruby", "billing": "existing-cli-auth; exact marginal spend unknown"})
        # Never make a subscription-authenticated provider process discover a
        # project's local instructions, plugins, hooks or vendor configuration.
        # The bounded packet is the only project data crossing this boundary.
        if self.config.get("auth_mode") == "claude-subscription":
            with tempfile.TemporaryDirectory(prefix="project-creator-provider-auth-") as auth_directory:
                auth_cwd = Path(auth_directory)
                if any(auth_cwd.iterdir()):
                    raise GateError("provider auth working directory is not empty")
                auth = execute([argv[0], "auth", "status", "--json"], cwd=auth_cwd, timeout=20, env=env,
                               cancelled=self.cancelled,
                               expected_executable_sha256=self.config.get("proof", {}).get("executable_sha256"),
                               expected_runtime_sha256=self.config.get("proof", {}).get("runtime_files", {}),
                               require_empty_cwd=True)
                try:
                    status = json.loads(auth.stdout)
                except ValueError as exc:
                    raise GateError("subscription authentication could not be verified") from exc
                if auth.returncode or status.get("loggedIn") is not True or status.get("authMethod") != "claude.ai":
                    raise GateError("existing Claude subscription authentication required")
        # Auth and inference never reuse a directory. Even a correctly pinned
        # auth process cannot leave local instructions for the model process.
        with tempfile.TemporaryDirectory(prefix="project-creator-provider-inference-") as inference_directory:
            inference_cwd = Path(inference_directory)
            if any(inference_cwd.iterdir()):
                raise GateError("provider inference working directory is not empty")
            result = execute(argv, cwd=inference_cwd, stdin=prompt, timeout=self.config.get("timeout_seconds", 900),
                             cancelled=self.cancelled, env=env,
                             expected_executable_sha256=self.config.get("proof", {}).get("executable_sha256"),
                             expected_runtime_sha256=self.config.get("proof", {}).get("runtime_files", {}),
                             require_empty_cwd=True)
        if result.returncode:
            self.audit("provider_failure", {"vendor": self.name, "exit_code": result.returncode})
            raise GateError("provider refused or failed; inspect authentication/model availability outside the AI transcript")
        safe_text(result.stdout)
        try:
            mode = self.config.get("output", "json")
            metadata = {}
            stream_parsers = {"claude-stream": parse_claude_stream, "codex-provenance": parse_codex_provenance,
                              "gemini-provenance": parse_gemini_provenance}
            if mode == "codex-data-only":
                response, metadata = parse_codex_data_only(result.stdout, model, request_id)
                actual = metadata["model"]
            elif mode in stream_parsers:
                response, metadata = stream_parsers[mode](result.stdout, model)
                actual = metadata["model"]
            else:
                envelope = json.loads(result.stdout)
                actual = attest_model(envelope, self.config["attestation"], model)
            if mode == "claude-json":
                if envelope.get("is_error"):
                    raise GateError("provider reported an error")
                response = envelope.get("structured_output")
                if response is None:
                    response = json_object(envelope["result"])
            elif mode == "gemini-json":
                response = json.loads(envelope["response"])
            elif mode not in stream_parsers and mode != "codex-data-only":
                response = envelope
            if not isinstance(response, dict):
                raise ValueError()
        except (ValueError, KeyError, TypeError) as exc:
            raise GateError("provider did not return the required JSON object") from exc
        response = validate_output(stage, response)
        safe_text(json.dumps(response, ensure_ascii=False))
        self.audit("provider_end", {"vendor": self.name, "model_requested": model,
                   "model_attested": actual, "metadata": metadata, "elapsed_seconds": result.elapsed, "output_hash": digest(response)})
        return response


class VerificationRunner:
    def __init__(self, sandbox: dict, commands: dict, *, read_set=None, cancelled=lambda: False):
        self.sandbox, self.commands, self.cancelled = sandbox, commands, cancelled
        self.read_set = read_set

    def run(self, ids: list[str], worktree: Path, *, expected_files=None) -> list[dict]:
        runtime_bytes = validate_capability(self.sandbox, "verification")
        results = []
        for test_id in sorted(set(ids)):
            if test_id not in self.commands:
                raise GateError("unapproved test identifier")
            command = self.commands[test_id]
            if not isinstance(command, list) or not command:
                raise GateError("invalid approved verification command")
            if self.sandbox.get("kind") == "docker":
                if not self.read_set:
                    raise GateError("Docker verification requires an exact source packet")
                DockerSandbox = _verified_docker_class(runtime_bytes)
                result = DockerSandbox(self.sandbox, self.read_set, cancelled=self.cancelled).run(
                    command, worktree, test_id, expected_files=expected_files)
            else:
                argv = [x.replace("{worktree}", str(worktree)) for x in self.sandbox["argv"]] + command
                result = execute(
                    argv, cwd=worktree, timeout=self.sandbox.get("timeout_seconds", 600),
                    cancelled=self.cancelled,
                    expected_executable_sha256=self.sandbox.get("proof", {}).get("executable_sha256"),
                    expected_runtime_sha256=self.sandbox.get("proof", {}).get("runtime_files", {}),
                )
            # The runner is confined; diagnostic text remains untrusted data.
            # Credential-shaped output is refused rather than copied to a model.
            diagnostic = safe_text((result.stdout + "\n" + result.stderr)[:8000])
            results.append({"test_id": test_id, "exit_code": result.returncode,
                            "diagnostic": diagnostic,
                            "stdout_sha256": digest(result.stdout), "stderr_sha256": digest(result.stderr),
                            "elapsed_seconds": result.elapsed})
        return results
