"""Provider edges. Fresh invocations, explicit models, no implicit fallback."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .contracts import GateError, digest, safe_text
from .processes import execute


def validate_capability(config: dict, purpose: str):
    proof = config.get("proof", {})
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
    for case in required:
        value = cases[case]["evidence_sha256"]
        path = evidence.get(value)
        if not path or not Path(path).is_absolute() or digest(Path(path).read_bytes()) != value:
            raise GateError(f"{purpose} retained test evidence absent or changed")
    executable = Path(config["argv"][0])
    if not executable.is_absolute() or digest(executable.read_bytes()) != proof.get("executable_sha256"):
        raise GateError(f"{purpose} executable changed after proof")
    dependencies = proof.get("runtime_files", {})
    for arg in config["argv"][1:]:
        if Path(arg).suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".exe"} and arg not in dependencies:
            raise GateError(f"{purpose} script or binary missing from proof")
    for name, expected_hash in dependencies.items():
        if not Path(name).is_absolute() or digest(Path(name).read_bytes()) != expected_hash:
            raise GateError(f"{purpose} runtime dependency changed after proof")


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
        validate_capability(self.config, "provider")
        if not self.config.get("attestation"):
            raise GateError("adapter lacks an actual-model attestation parser")
        argv = [arg.replace("{model}", model) for arg in self.config["argv"]]
        if not any(model in arg for arg in argv):
            raise GateError("provider does not explicitly select the pinned model")
        prompt = json.dumps(packet, ensure_ascii=False)
        safe_text(prompt)
        self.audit("provider_start", {"vendor": self.name, "model_requested": model, "stage": stage,
                   "packet_hash": digest(packet), "adapter_hash": digest(self.config), "cost_class": "ruby", "billing": "existing-cli-auth; exact marginal spend unknown"})
        result = execute(argv, cwd=directory, stdin=prompt, timeout=self.config.get("timeout_seconds", 900), cancelled=self.cancelled)
        if result.returncode:
            self.audit("provider_failure", {"vendor": self.name, "exit_code": result.returncode})
            raise GateError("provider refused or failed; inspect authentication/model availability outside the AI transcript")
        safe_text(result.stdout)
        try:
            envelope = json.loads(result.stdout)
            actual = attest_model(envelope, self.config["attestation"], model)
            mode = self.config.get("output", "json")
            if mode == "claude-json":
                if envelope.get("is_error"):
                    raise GateError("provider reported an error")
                response = envelope.get("structured_output")
                if response is None:
                    response = json.loads(envelope["result"])
            elif mode == "gemini-json":
                response = json.loads(envelope["response"])
            else:
                response = envelope
            if not isinstance(response, dict):
                raise ValueError()
        except (ValueError, KeyError, TypeError) as exc:
            raise GateError("provider did not return the required JSON object") from exc
        safe_text(json.dumps(response, ensure_ascii=False))
        self.audit("provider_end", {"vendor": self.name, "model_requested": model,
                   "model_attested": actual, "elapsed_seconds": result.elapsed, "output_hash": digest(response)})
        return response


class VerificationRunner:
    def __init__(self, sandbox: dict, commands: dict, *, cancelled=lambda: False):
        self.sandbox, self.commands, self.cancelled = sandbox, commands, cancelled

    def run(self, ids: list[str], worktree: Path) -> list[dict]:
        validate_capability(self.sandbox, "verification")
        results = []
        for test_id in sorted(set(ids)):
            if test_id not in self.commands:
                raise GateError("unapproved test identifier")
            command = self.commands[test_id]
            if not isinstance(command, list) or not command:
                raise GateError("invalid approved verification command")
            argv = [x.replace("{worktree}", str(worktree)) for x in self.sandbox["argv"]] + command
            result = execute(argv, cwd=worktree, timeout=self.sandbox.get("timeout_seconds", 600), cancelled=self.cancelled)
            # The runner is confined; diagnostic text remains untrusted data.
            # Credential-shaped output is refused rather than copied to a model.
            diagnostic = safe_text((result.stdout + "\n" + result.stderr)[:8000])
            results.append({"test_id": test_id, "exit_code": result.returncode,
                            "diagnostic": diagnostic,
                            "stdout_sha256": digest(result.stdout), "stderr_sha256": digest(result.stderr),
                            "elapsed_seconds": result.elapsed})
        return results
