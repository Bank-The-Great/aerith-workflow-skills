"""Provider edges. Fresh invocations, explicit models, no implicit fallback."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .contracts import GateError, digest, safe_text
from .processes import assert_non_augmentable_directory, execute, minimal_environment
from .output_schemas import schema_for, validate_output
from .provenance import parse_codex_data_only

_HOST_CAPABILITIES = {}
_VALIDATED_RUNTIME_LOCK = threading.RLock()
_ADMITTED_PROVIDER_OUTPUTS = {"codex-data-only"}
# The evidence tiers each data-only attestation mode accepts AT RUNTIME. Since D10
# (2026-09-16) the mode is the operator's floor for what a single call may rest on, never
# a claim about what was proven: `recorded-response-model` accepts a response that only
# records the requested model, and header evidence is stronger and meets that floor too,
# while the header mode never accepts recorded evidence. Nothing certifies this label
# against the proof any more (REQ-LC-020); `attestation_facts` shows the operator what the
# proof actually measured (REQ-LC-022).
_ADMITTED_EVIDENCE = {
    "provider-response-header": {"provider_response_header"},
    "recorded-response-model": {"provider_response_header", "recorded_response_model"},
}
# One owner for the proof lifetime: the gate refuses past it and the summary counts down to
# it, and a second literal is how the two would come to disagree (R23-SEC-09).
_PROOF_LIFETIME = timedelta(days=30)


def _attestation_mode(config: dict):
    """The data-only attestation mode, or None unless the block is exactly one known mode."""
    attestation = config.get("attestation")
    if (isinstance(attestation, dict) and set(attestation) == {"mode"}
            and isinstance(attestation["mode"], str) and attestation["mode"] in _ADMITTED_EVIDENCE):
        return attestation["mode"]
    return None


def _launch_refusal(config, name):
    """Why `invoke` would refuse to launch this record under this key, or None.

    R23-SPEC-04, completed at R24-SPEC-01 / R24-SEC-01. The first version took only the record,
    so it could not see the one precondition that is not a field of the record: the vendor KEY
    the record is filed under. A correctly shaped adapter copied under a second key hashed
    identically, was pinned identically, and was reported `proof-current` by both readiness
    surfaces while every launch of it was refused. The key is therefore an argument, it is
    required, and a caller that has no key to give (None) is refused rather than excused.

    The key has its own message, because an operator told that a correctly shaped record is
    "not in a launchable shape" is told something untrue about the record. Both messages are
    fixed text: neither names the key or any other caller-supplied value.

    None of these asserts anything about the world; each says only that the record and its key
    are in the shape a launch can use. One owner, called by the gate and by `invoke`, so the two
    cannot drift apart. `output`, `filesystem_scope` and `loader_policy` are not repeated here:
    the data-only worker check owns them in both places, with its own message and its own rows.
    """
    if name != "codex":
        return "only the codex vendor key may launch a reviewed data-only worker"
    template = config.get("argv")
    if not (config.get("auth_mode") == "codex-subscription"
            # No length check: a list whose tail is exactly these two elements has exactly
            # three, so a separate `len(template) == 3` was redundant and unfalsifiable.
            and isinstance(template, list) and template[1:] == ["--model", "{model}"]
            and isinstance(template[0], str) and Path(template[0]).suffix.lower() == ".exe"
            and _attestation_mode(config) is not None):
        return "provider capability is not in a launchable data-only shape"
    return None


def _model_attestation_measured(measurement: dict) -> bool:
    """The one measured invariant left in the model-attestation case (REQ-LC-020, D10).

    No observation on this backend proves which model generated the output (D4), so a gate
    that checked the record's attestation mode and limits was checking a label rather than
    a fact, and two review rounds were spent on it. What survives is measured, not claimed:
    the recorded tier was not withdrawn, and at least one positive call carried model
    evidence at all. Which tier a call may rest on is the operator's runtime floor in
    `invoke`; what the proof measured is reported by `attestation_facts`.
    """
    calls = measurement.get("evidenced_positive_calls")
    return (measurement.get("recorded_tier_withdrawn") is False
            and type(calls) is int and calls > 0)


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


def validate_capability(config: dict, purpose: str, *, name=None, environment=None, observed=None):
    """Admit a reviewed capability, and optionally hand back what it measured.

    `name` is the vendor key the record is filed under. For the provider purpose it is part of
    the launchable shape, and leaving it out refuses (R24-SPEC-01): a readiness answer that
    does not know which key it is answering for cannot be the answer `invoke` will give.

    `observed`, when given a dict, receives the model-attestation measurement and its check
    time only when this function is about to return, after every refusal it can make. It exists
    so the operator's summary has one reader of the evidence rather than two: the second reader
    was weaker than this one on six counts and disagreed with it on a seventh (R23-SEC-02,
    R23-SPEC-03, R23-SPEC-05). It was first written mid-loop, before the executable, runtime-file,
    Docker and dependency refusals, so the invariant was held by the caller's early return rather
    than by this function (R24-SEC-02); the measurement is now staged and handed back last.
    """
    if _HOST_CAPABILITIES.get(digest(config)) != purpose:
        raise GateError("capability is not pinned by the trusted host admission registry")
    if purpose == "provider" and (config.get("output") not in _ADMITTED_PROVIDER_OUTPUTS
                                  or config.get("filesystem_scope") != "codex-home-auth-only"
                                  or config.get("loader_policy") != "pe-dependent-load-system32"):
        raise GateError("provider is not an admitted data-only worker")
    proof = config.get("proof", {})
    # D10 (2026-09-16): this gate no longer CERTIFIES `attestation` or `proof.limits` against
    # the evidence. It answers one question, whether this is the reviewed executable with
    # intact evidence that has not expired; what the proof measured about the model is
    # reported to the operator by `attestation_facts` rather than certified here.
    # The record must still be in a LAUNCHABLE shape, under the key it is filed with (R22-SEC-02,
    # R23-SPEC-04, R24-SPEC-01): none of those clauses asserts anything about the world, they only
    # say the record is usable, and without them a readiness surface reports a record ready when
    # every launch of it is refused. What that owner checks is inventoried by the coverage ledger,
    # not restated here.
    if purpose == "provider":
        refusal = _launch_refusal(config, name)
        if refusal:
            raise GateError(refusal)
    if purpose == "provider" and proof.get("environment_hash") != digest(provider_environment(config) if environment is None else environment):
        raise GateError("provider auth-home/runtime environment changed after host review")
    expected = digest({k: v for k, v in config.items() if k != "proof"})
    if proof.get("configuration_hash") != expected:
        raise GateError(f"{purpose} configuration has no matching containment proof")
    try:
        checked = datetime.fromisoformat(proof["checked_at"])
        age = datetime.now(timezone.utc) - checked
        if checked.tzinfo is None or age < timedelta(0) or age > _PROOF_LIFETIME:
            raise ValueError()
    except (ValueError, KeyError, TypeError) as exc:
        raise GateError(f"{purpose} containment proof expired or absent") from exc
    required = ({"exact_source_set", "outside_read_denied", "outside_write_denied",
                 "network_denied", "child_cleanup"} if purpose == "verification" else
                {"fresh_context", "tools_disabled", "ambient_not_observed_in_output", "child_cleanup",
                 "model_attestation", "subscription_auth_only",
                 "dependent_load_flags_system32", "delay_imports_absent",
                 "imports_allowlisted"})
    cases = proof.get("cases", {})
    staged = {}
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
            # One spelling, because two readers of one field is how the panel and the gate came
            # to disagree about whether a record was refused (R23-SPEC-03). Live run 3's evidence
            # uses `checked_at`; the `at` fallback this line used to carry was dead and is gone.
            measured_at = measured.get("checked_at") if isinstance(measured, dict) else None
            measured_case = measured.get("cases", {}).get(case) if isinstance(measured, dict) else None
            valid = (isinstance(measured, dict) and measured.get("schema_version") == 1
                     and measured.get("purpose") == purpose and measured.get("configuration_hash") == expected
                     and isinstance(measured_case, dict) and measured_case.get("passed") is True
                     and isinstance(measured_case.get("measurement"), dict)
                     and bool(measured_case["measurement"])
                     and measured.get("probe_harness_sha256") == proof.get("probe_harness_sha256")
                     and bool(measured.get("probe_harness_sha256"))
                     and measured_at == proof.get("checked_at")
                     and measured.get("executable_sha256") == proof.get("executable_sha256")
                     and measured.get("runtime_files") == proof.get("runtime_files", {}))
            if valid and purpose == "provider" and case == "model_attestation":
                valid = _model_attestation_measured(measured_case["measurement"])
                if valid:
                    # The gate is the ONLY reader of this file. What the operator is shown comes
                    # from here, already validated, instead of from a second weaker read. It is
                    # staged, and reaches `observed` only after the last refusal below.
                    staged = {"measurement": measured_case["measurement"], "checked_at": measured_at}
        except ValueError:
            valid = False
        if not valid:
            raise GateError("retained evidence does not substantiate the claimed capability")
    executable = Path(config["argv"][0])
    if not executable.is_absolute() or digest(executable.read_bytes()) != proof.get("executable_sha256"):
        raise GateError(f"{purpose} executable changed after proof")
    dependencies = proof.get("runtime_files", {})
    if purpose == "provider" and dependencies:
        raise GateError("data-only provider must be one reviewed native executable")
    if config.get("kind") == "docker":
        assert_non_augmentable_directory(executable.parent)
        package = Path(__file__).resolve().parent
        required_runtime = {package / name for name in ("contracts.py", "processes.py", "docker_sandbox.py")}
        for runtime in required_runtime:
            if dependencies.get(str(runtime)) != digest(runtime.read_bytes()):
                raise GateError("Docker runtime closure file missing from proof")
        harness = package.parent / "probe_docker.py"
        if proof.get("probe_harness_sha256") != digest(harness.read_bytes()):
            raise GateError("Docker probe harness changed after host review")
    for arg in config["argv"][1:]:
        if Path(arg).suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".exe"} and arg not in dependencies:
            raise GateError(f"{purpose} script or binary missing from proof")
    runtime_bytes = {}
    for dependency, expected_hash in dependencies.items():
        if not Path(dependency).is_absolute():
            raise GateError(f"{purpose} runtime dependency changed after proof")
        raw = Path(dependency).read_bytes()
        if digest(raw) != expected_hash:
            raise GateError(f"{purpose} runtime dependency changed after proof")
        runtime_bytes[dependency] = raw
    if observed is not None:
        observed.update(staged)
    return runtime_bytes


def attestation_facts(config: dict, *, name=None) -> dict:
    """What the GATE measured about the model, for the operator to read before the first call.

    REQ-LC-022, rebuilt after round 23 (R23-SEC-01/02/03, R23-SPEC-03/05/06). The first two
    builds of this function were a second reader of the retained evidence, weaker than
    `validate_capability` on six checks and disagreeing with it on a seventh, which is how a
    display meant to report measurements came to report a rewrapped proof as fresh and, later,
    to announce a refusal that had not happened. There is now ONE reader: this function runs the
    gate, and reports the measurement the gate handed back after every binding passed.

    Consequences worth stating, because they are the point rather than side effects. When the
    gate refuses, there are no numbers to show and none are shown: the refusal IS the finding,
    and it is reported verbatim. When the gate admits, the evidence is already bound to this
    configuration, this executable, this harness and this proof time, so `checked_at` needs no
    second opinion and the expiry warnings that used to restate the gate's own refusals are
    unreachable and gone. The panel opens no file of its own, so it needs no path, size or
    reparse bounds, and it is no longer an oracle over caller-named paths.

    `name` is the vendor key the record is filed under, forwarded to the gate so the verdict is
    the one `invoke` would reach for that key (R24-SPEC-01). There is no `at` and no
    `environment` parameter any more: each let a caller print numbers from a clock or an
    environment other than the one the gate decided with (R24-SEC-04), and neither had a caller.

    `answering_model_proven` stays False by construction, never by measurement, because no
    observation on this backend can establish it (D4). Never raises, for any input: the
    arithmetic over the handed-back check time sits inside the same guard as the gate, so the
    contract holds at this function rather than two functions away (R24-SPEC-06, R24-SEC-03).
    """
    facts = {"verified": False, "available": False, "answering_model_proven": False,
             "declared_evidence_floor": _attestation_mode(config) if isinstance(config, dict) else None,
             "statement": "The model that generated the output is not proven on this backend: the "
                          "worker requests the declared model and the response records it. The "
                          "unserved-model echo is untested whenever the proof could not observe it (D8).",
             "reason": None, "warnings": []}
    measured = {}
    try:
        validate_capability(config, "provider", name=name, observed=measured)
    except GateError as exc:
        facts["reason"] = str(exc)
        return facts
    except (OSError, KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        # A pinned-but-malformed record can reach the gate in shapes no GateError covers. The
        # summary reports that rather than ending the command with a traceback. `IndexError` is
        # not reachable through today's launchable shape (R24-SPEC-07, R24-SEC-07); it stays,
        # because this contract is "never raises" and must not depend on that shape staying put.
        facts["reason"] = "provider record is unreadable: " + type(exc).__name__
        return facts
    measurement = measured.get("measurement", {})
    numbers = {key: measurement.get(key) for key in (
        "evidenced_positive_calls", "provider_response_header_calls",
        "recorded_response_model_calls", "recorded_tier_withdrawn", "unserved_echo_observable")}
    try:
        numbers["checked_at"] = measured["checked_at"]
        age = datetime.now(timezone.utc) - datetime.fromisoformat(numbers["checked_at"])
        numbers["age_days"] = round(age.total_seconds() / 86400, 2)
        numbers["expires_in_days"] = round((_PROOF_LIFETIME - age).total_seconds() / 86400, 2)
    except (KeyError, TypeError, ValueError) as exc:
        # Unreachable while the gate binds the check time before admitting; if that binding is
        # ever lost, the operator is told the GATE handed back nothing usable, not that the
        # record is at fault, and no partial numbers are shown.
        facts["reason"] = "the gate admitted without handing back a check time: " + type(exc).__name__
        return facts
    facts |= numbers
    # Only reachable warnings live here. Expiry, a future date and a record/evidence time
    # disagreement are all refusals the gate has already made, so a warning for them could never
    # fire and would only teach a reader that this function still judges those things.
    if facts["expires_in_days"] <= 3:
        facts["warnings"].append("the proof expires within three days; a fresh live run is due")
    floor = facts["declared_evidence_floor"]
    if ("recorded_response_model" not in _ADMITTED_EVIDENCE[floor]
            and facts.get("recorded_response_model_calls")):
        # Refused after the call is spent, so say it before the 5-call live budget pays for it.
        facts["warnings"].append("this floor accepts only header evidence and the proof measured "
                                 "recorded-tier calls, which are refused after the call is spent")
    if facts.get("unserved_echo_observable") is True and facts.get("recorded_tier_withdrawn") is False:
        facts["warnings"].append("the evidence reports an observable echo without withdrawing the "
                                 "recorded tier, a combination no run of this harness produces")
    facts["verified"] = True
    facts["available"] = True
    return facts


def _verified_docker_class(runtime_bytes):
    """Compile the exact validated adapter and its local import closure."""
    package_path = Path(__file__).resolve().parent
    paths = {leaf: package_path / (leaf + ".py")
             for leaf in ("contracts", "processes", "docker_sandbox")}
    raw = {leaf: runtime_bytes.get(str(path)) if isinstance(runtime_bytes, dict) else None
           for leaf, path in paths.items()}
    if not all(isinstance(value, bytes) for value in raw.values()):
        raise GateError("validated Docker runtime closure bytes are unavailable")
    package_name = "project_creator._validated_docker_" + digest(
        {leaf: digest(value) for leaf, value in raw.items()})[:16]

    def namespace(leaf):
        return {"__name__": package_name + "." + leaf,
                "__file__": str(paths[leaf]), "__package__": package_name}

    contracts = namespace("contracts")
    exec(compile(raw["contracts"], str(paths["contracts"]), "exec"), contracts)
    required_contracts = {name: contracts.get(name) for name in ("GateError", "digest", "scoped_path")}
    if not all(required_contracts.values()):
        raise GateError("validated Docker contract helpers are unavailable")

    processes_source = raw["processes"].decode("utf-8")
    process_import = "from .contracts import GateError"
    if processes_source.count(process_import) != 1:
        raise GateError("validated process helper import is not exact")
    process_name = package_name + ".processes"
    process_module = types.ModuleType(process_name)
    process_module.__dict__.update(namespace("processes") | {"GateError": required_contracts["GateError"]})
    with _VALIDATED_RUNTIME_LOCK:
        sys.modules[process_name] = process_module
        try:
            exec(compile(processes_source.replace(process_import, "# GateError injected from validated bytes"),
                         str(paths["processes"]), "exec"), process_module.__dict__)
        finally:
            sys.modules.pop(process_name, None)
    processes = process_module.__dict__
    required_processes = {name: processes.get(name)
                          for name in ("execute", "minimal_environment", "reviewed_files")}
    if not all(required_processes.values()):
        raise GateError("validated Docker process helpers are unavailable")

    docker_source = raw["docker_sandbox"].decode("utf-8")
    imports = ("from .contracts import GateError, digest, scoped_path",
               "from .processes import execute, minimal_environment, reviewed_files")
    if any(docker_source.count(statement) != 1 for statement in imports):
        raise GateError("validated Docker helper imports are not exact")
    for statement in imports:
        docker_source = docker_source.replace(statement, "# helper injected from validated bytes")
    module = namespace("docker_sandbox") | required_contracts | required_processes
    exec(compile(docker_source, str(paths["docker_sandbox"]), "exec"), module)
    cls = module.get("DockerSandbox")
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
        if (self.config.get("output") not in _ADMITTED_PROVIDER_OUTPUTS
                or self.config.get("filesystem_scope") != "codex-home-auth-only"
                or self.config.get("loader_policy") != "pe-dependent-load-system32"):
            raise GateError("only a reviewed data-only provider worker may be launched")
        # One owner for the launchable shape, shared with the gate (R23-SPEC-04), and since
        # R24-SPEC-01 that owner also holds the vendor key, which this line used to check alone.
        refusal = _launch_refusal(self.config, self.name)
        if refusal:
            raise GateError(refusal)
        env = provider_environment(self.config)
        validate_capability(self.config, "provider", name=self.name, environment=env)
        argv = [arg.replace("{model}", model) for arg in self.config["argv"]]
        if not any(model in arg for arg in argv):
            raise GateError("provider does not explicitly select the pinned model")
        request_id = digest(packet)
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
        # The exact reviewed worker disables project/ancestor config discovery.
        # The transport also replaces this disposable caller path with a locked,
        # non-writable system CWD to keep DLL search away from the project.
        with tempfile.TemporaryDirectory(prefix="project-creator-provider-inference-") as inference_directory:
            inference_cwd = Path(inference_directory)
            if any(inference_cwd.iterdir()):
                raise GateError("provider inference working directory is not empty")
            result = execute(argv, cwd=inference_cwd, stdin=prompt, timeout=self.config.get("timeout_seconds", 900),
                             cancelled=self.cancelled, env=env,
                             expected_executable_sha256=self.config.get("proof", {}).get("executable_sha256"),
                             expected_runtime_sha256=self.config.get("proof", {}).get("runtime_files", {}),
                             system_cwd=True)
        if result.returncode:
            self.audit("provider_failure", {"vendor": self.name, "exit_code": result.returncode})
            raise GateError("provider refused or failed; inspect authentication/model availability outside the AI transcript")
        safe_text(result.stdout)
        try:
            response, metadata = parse_codex_data_only(result.stdout, model, request_id)
            actual = metadata["model"]
            if not isinstance(response, dict):
                raise ValueError()
        except (ValueError, KeyError, TypeError) as exc:
            raise GateError("provider did not return the required JSON object") from exc
        # The mode is the operator's evidence floor for a single call, enforced here and no
        # longer certified at admission (D10, 2026-09-16). The header floor accepts only
        # header evidence. Recorded evidence does not name the answering model, so a run
        # resting on it must have declared the recorded floor, and its audit never calls the
        # model attested.
        mode = _attestation_mode(self.config)
        if metadata.get("model_evidence") not in _ADMITTED_EVIDENCE[mode]:
            self.audit("provider_failure", {"vendor": self.name, "reason": "model_evidence_not_admitted",
                       "model_evidence": metadata.get("model_evidence")})
            raise GateError("provider result rests on recorded model evidence, which the header attestation mode does not admit")
        response = validate_output(stage, response)
        safe_text(json.dumps(response, ensure_ascii=False))
        claim = ({"model_attested": actual} if mode == "provider-response-header"
                 else {"model_recorded": actual, "attestation_mode": mode})
        self.audit("provider_end", {"vendor": self.name, "model_requested": model, **claim,
                   "metadata": metadata, "elapsed_seconds": result.elapsed, "output_hash": digest(response)})
        return response


class VerificationRunner:
    def __init__(self, sandbox: dict, commands: dict, *, read_set=None, cancelled=lambda: False):
        self.sandbox, self.commands, self.cancelled = sandbox, commands, cancelled
        self.read_set = read_set

    def run(self, ids: list[str], worktree: Path, *, expected_files=None) -> list[dict]:
        runtime_bytes = validate_capability(self.sandbox, "verification")
        if self.sandbox.get("kind") != "docker":
            raise GateError("only exact-packet Docker verification is admitted")
        results = []
        for test_id in sorted(set(ids)):
            if test_id not in self.commands:
                raise GateError("unapproved test identifier")
            command = self.commands[test_id]
            if not isinstance(command, list) or not command:
                raise GateError("invalid approved verification command")
            if not self.read_set:
                raise GateError("Docker verification requires an exact source packet")
            DockerSandbox = _verified_docker_class(runtime_bytes)
            result = DockerSandbox(self.sandbox, self.read_set, cancelled=self.cancelled).run(
                command, worktree, test_id, expected_files=expected_files)
            # The runner is confined; diagnostic text remains untrusted data.
            # Credential-shaped output is refused rather than copied to a model.
            diagnostic = safe_text((result.stdout + "\n" + result.stderr)[:8000])
            results.append({"test_id": test_id, "exit_code": result.returncode,
                            "diagnostic": diagnostic,
                            "stdout_sha256": digest(result.stdout), "stderr_sha256": digest(result.stderr),
                            "elapsed_seconds": result.elapsed})
        return results
