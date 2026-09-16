"""Parser/adapter unit fixtures, not substitutes for live containment probes."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate_inventory
from project_creator import providers as providers_module
from project_creator.contracts import GateError, digest
from project_creator.providers import (parse_claude_stream, provider_environment, VerificationRunner,
                                       CLIProvider, validate_capability, _verified_docker_class,
                                       _launch_refusal)
from project_creator.docker_sandbox import DockerSandbox
from project_creator.processes import Result
from project_creator.output_schemas import SCHEMAS, validate_output


def docker_config():
    return {"argv": [sys.executable], "image": "python@sha256:" + "a" * 64,
            "daemon_host": "npipe:////./pipe/fixture", "daemon_id": "fixture"}


def stream(*, model="chosen", tools=None, usage=None, result='{"ok":true}'):
    return [
        {"type": "system", "subtype": "init", "model": "chosen", "tools": tools or [], "mcp_servers": [], "session_id": "fresh"},
        {"type": "assistant", "session_id": "fresh", "message": {"model": model, "content": [{"type": "text", "text": result}]}},
        {"type": "result", "session_id": "fresh", "is_error": False, "modelUsage": usage or {"chosen": {}}, "result": result},
    ]


UNSET = object()  # "this row did not name an argv", which None and "" cannot say


class ProviderEdges(unittest.TestCase):
    def parse(self, events):
        return parse_claude_stream("\n".join(json.dumps(x) for x in events), "chosen")

    def test_stream_requires_observed_message_model(self):
        response, metadata = self.parse(stream())
        self.assertEqual(response, {"ok": True})
        self.assertEqual(metadata["session_id"], "fresh")
        with self.assertRaises(GateError):
            self.parse(stream(model="fallback"))

    def test_tools_and_mixed_models_refused(self):
        for data in (stream(tools=["Read"]), stream(usage={"chosen": {}, "helper": {}})):
            with self.assertRaises(GateError):
                self.parse(data)
        events = stream()
        events[1]["message"]["content"] = [{"type": "tool_use", "name": "Read"}]
        with self.assertRaises(GateError):
            self.parse(events)
        for kind in ("server_tool_use", "unknown", "tool_result"):
            events = stream()
            events[1]["message"]["content"] = [{"type": kind}]
            with self.assertRaises(GateError):
                self.parse(events)
        for position in (1, 2):
            events = stream()
            events[position]["session_id"] = "different"
            with self.assertRaises(GateError):
                self.parse(events)

    def test_claude_stream_rejects_effect_records_and_wrong_order(self):
        effect = {"type": "user", "session_id": "fresh",
                  "message": {"content": [{"type": "tool_result", "content": "untrusted"}]}}
        events = stream()
        with self.assertRaisesRegex(GateError, "effect-bearing"):
            self.parse(events[:2] + [effect] + events[2:])
        with self.assertRaisesRegex(GateError, "out-of-order"):
            self.parse([events[1], events[0], events[2]])
        status = {"type": "system", "subtype": "status", "session_id": "fresh"}
        with self.assertRaisesRegex(GateError, "unknown"):
            self.parse(events[:1] + [status] + events[1:])

    def test_error_and_missing_metadata_refused(self):
        for key in ("session_id", "tools", "mcp_servers"):
            events = stream()
            events[0].pop(key)
            with self.assertRaises(GateError):
                self.parse(events)
        events = stream()
        events[-1]["is_error"] = True
        with self.assertRaises(GateError):
            self.parse(events)

    def test_malformed_stream_nesting_and_duplicates_refused(self):
        for replacement in (None, [], "message"):
            events = stream()
            events[1]["message"] = replacement
            with self.assertRaises(GateError):
                self.parse(events)
        for content in (None, [None], "text"):
            events = stream()
            events[1]["message"]["content"] = content
            with self.assertRaises(GateError):
                self.parse(events)
        events = stream()
        for bad in (events + [events[0]], events + [events[-1]], events[:-1] + [events[-1] | {"modelUsage": []}]):
            with self.assertRaises(GateError):
                self.parse(bad)

    def test_general_vendor_cli_cannot_enter_production_provider_route(self):
        for output in ("claude-stream", "codex-provenance", "gemini-provenance"):
            with patch("project_creator.providers.execute") as execute:
                provider = CLIProvider("fixture", {"output": output}, audit=lambda *a: None)
                with self.assertRaisesRegex(GateError, "data-only"):
                    provider.invoke("implement", "chosen", {}, Path.cwd())
                execute.assert_not_called()

    def test_data_only_route_rejects_vendor_alias_and_malformed_native_command(self):
        base = {"output": "codex-data-only", "auth_mode": "codex-subscription",
                "filesystem_scope": "codex-home-auth-only",
                "loader_policy": "pe-dependent-load-system32",
                "attestation": {"mode": "provider-response-header"},
                "argv": [sys.executable, "--model", "{model}"]}
        variants = [
            ("gemini", base),
            ("codex", base | {"argv": None}),
            ("codex", base | {"argv": [sys.executable, "--model", "{model}", "extra"]}),
            ("codex", base | {"attestation": {"mode": "requested-only"}}),
        ]
        for vendor, config in variants:
            with self.subTest(vendor=vendor, config=config), patch("project_creator.providers.execute") as execute:
                with self.assertRaises(GateError):
                    CLIProvider(vendor, config, audit=lambda *a: None).invoke(
                        "implement", "chosen", {}, Path.cwd())
                execute.assert_not_called()

    def test_provider_uses_the_same_environment_snapshot_it_validated(self):
        config = {"output": "codex-data-only", "auth_mode": "codex-subscription",
                  "filesystem_scope": "codex-home-auth-only",
                  "loader_policy": "pe-dependent-load-system32",
                  "attestation": {"mode": "provider-response-header"},
                  "argv": [sys.executable, "--model", "{model}"]}
        original_path = os.environ.get("PATH")
        snapshots = []
        def validate(cfg, purpose, *, name, environment):
            # The gate is asked about the key `invoke` is launching under (R24-SPEC-01).
            self.assertEqual(name, "codex")
            snapshots.append(environment)
            os.environ["PATH"] = "synthetic-change-after-validation"
        packet = {}
        request_id = digest(packet)
        common = {"request_id": request_id, "response_id": "response-1",
                  "requested_model": "chosen", "evidenced_model": "chosen",
                  "model_evidence": "provider_response_header"}
        response = "\n".join(json.dumps(x) for x in (
            {"type": "response_metadata", **common},
            {"type": "result", **common,
             "output": {"changes": [], "summary": "none", "questions": []}},
        ))
        directories = []
        def launch(*args, **kwargs):
            cwd = kwargs["cwd"]
            self.assertEqual(list(cwd.iterdir()), [])
            directories.append(cwd)
            return Result(0, response, "", 0)
        with patch.dict(os.environ), patch("project_creator.providers.validate_capability", side_effect=validate), patch("project_creator.providers.execute", side_effect=launch) as execute:
            CLIProvider("codex", config, audit=lambda *a: None).invoke("implement", "chosen", packet, Path.cwd())
            for call in execute.call_args_list:
                self.assertIs(call.kwargs["env"], snapshots[0])
                self.assertEqual(call.kwargs["env"].get("PATH"), original_path)
                self.assertNotEqual(call.kwargs["cwd"], Path.cwd())
                self.assertTrue(call.kwargs["cwd"].name.startswith("project-creator-provider-"))
                self.assertFalse(call.kwargs["cwd"].exists())
                self.assertTrue(call.kwargs["system_cwd"])
            self.assertEqual(len(directories), 1)

    def test_data_only_codex_receives_bounded_packet_and_strict_stage_schema(self):
        packet = {"instructions": "controller-only instructions", "role": "untrusted role data",
                  "output_contract": {"changes": []}}
        request_id = digest(packet)
        common = {"request_id": request_id, "response_id": "response-1",
                  "requested_model": "chosen", "evidenced_model": "chosen",
                  "model_evidence": "provider_response_header"}
        stream = "\n".join(json.dumps(x) for x in (
            {"type": "response_metadata", **common},
            {"type": "result", **common,
             "output": {"changes": [], "summary": "nothing", "questions": []}},
        ))
        config = {"output": "codex-data-only", "auth_mode": "codex-subscription",
                  "filesystem_scope": "codex-home-auth-only",
                  "loader_policy": "pe-dependent-load-system32",
                  "attestation": {"mode": "provider-response-header"},
                  "argv": [sys.executable, "--model", "{model}"],
                  "proof": {"executable_sha256": "a" * 64}}
        with patch("project_creator.providers.validate_capability"), \
                patch("project_creator.providers.execute", return_value=Result(0, stream, "", 0.1)) as execute:
            result = CLIProvider("codex", config, audit=lambda *a: None).invoke(
                "implement", "chosen", packet, Path.cwd())
        self.assertEqual(result["summary"], "nothing")
        worker_request = json.loads(execute.call_args.kwargs["stdin"])
        self.assertEqual(worker_request["request_id"], request_id)
        self.assertEqual(worker_request["instructions"], packet["instructions"])
        self.assertNotIn("instructions", worker_request["input"])
        self.assertEqual(worker_request["input"]["role"], packet["role"])
        self.assertEqual(worker_request["output_schema"], SCHEMAS["implement"])
        self.assertEqual(execute.call_args.kwargs["expected_executable_sha256"], "a" * 64)
        self.assertEqual(execute.call_args.kwargs["expected_runtime_sha256"], {})

    def test_data_only_codex_admits_evidence_by_attestation_mode(self):
        packet = {"instructions": "controller-only instructions"}
        request_id = digest(packet)
        config = {"output": "codex-data-only", "auth_mode": "codex-subscription",
                  "filesystem_scope": "codex-home-auth-only",
                  "loader_policy": "pe-dependent-load-system32",
                  "attestation": {"mode": "provider-response-header"},
                  "argv": [sys.executable, "--model", "{model}"],
                  "proof": {"executable_sha256": "a" * 64}}
        recorded_config = config | {"attestation": {"mode": "recorded-response-model"}}
        cases = (("provider-response-header", "provider_response_header", True),
                 ("provider-response-header", "recorded_response_model", False),
                 ("recorded-response-model", "provider_response_header", True),
                 ("recorded-response-model", "recorded_response_model", True))
        for mode, tier, admitted in cases:
            common = {"request_id": request_id, "response_id": "response-1",
                      "requested_model": "chosen", "evidenced_model": "chosen", "model_evidence": tier}
            stream = "\n".join(json.dumps(x) for x in (
                {"type": "response_metadata", **common},
                {"type": "result", **common,
                 "output": {"changes": [], "summary": "nothing", "questions": []}},
            ))
            events = []
            chosen_config = config if mode == "provider-response-header" else recorded_config
            with self.subTest(mode=mode, tier=tier), patch("project_creator.providers.validate_capability"), \
                    patch("project_creator.providers.execute", return_value=Result(0, stream, "", 0.1)):
                provider = CLIProvider("codex", chosen_config, audit=lambda kind, payload: events.append((kind, payload)))
                if admitted:
                    self.assertEqual(provider.invoke("implement", "chosen", packet, Path.cwd())["summary"], "nothing")
                    end = [payload for kind, payload in events if kind == "provider_end"]
                    self.assertEqual(len(end), 1)
                    self.assertEqual(end[0]["metadata"]["model_evidence"], tier)
                    if mode == "provider-response-header":
                        self.assertEqual(end[0]["model_attested"], "chosen")
                        self.assertNotIn("model_recorded", end[0])
                    else:
                        # Under the recorded mode no event calls the model attested, even
                        # for a header-tier result.
                        self.assertEqual((end[0]["model_recorded"], end[0]["attestation_mode"]),
                                         ("chosen", "recorded-response-model"))
                        self.assertFalse(any("model_attested" in payload for _, payload in events))
                else:
                    with self.assertRaisesRegex(GateError, "recorded model evidence"):
                        provider.invoke("implement", "chosen", packet, Path.cwd())
                    # No event names a recorded-tier result as an attested model.
                    self.assertFalse([kind for kind, _ in events if kind == "provider_end"])
                    self.assertFalse(any("model_attested" in payload for _, payload in events))
                    self.assertIn(("provider_failure", {"vendor": "codex", "reason": "model_evidence_not_admitted",
                                                        "model_evidence": "recorded_response_model"}), events)
        # An attestation block that is not exactly one known mode is refused before launch.
        for attestation in ({"mode": "recorded-response-model", "echo_tested": False}, {"mode": "served-model"},
                            {"mode": ["recorded-response-model"]}, {}, None):
            launched = []
            with self.subTest(attestation=attestation), patch("project_creator.providers.validate_capability"), \
                    patch("project_creator.providers.execute", side_effect=lambda *a, **k: launched.append(a)):
                provider = CLIProvider("codex", config | {"attestation": attestation}, audit=lambda *a: None)
                with self.assertRaisesRegex(GateError, "not in a launchable data-only shape"):
                    provider.invoke("implement", "chosen", packet, Path.cwd())
                self.assertEqual(launched, [])

    def test_data_only_codex_rejects_wrong_stage_shape_locally(self):
        packet = {"instructions": "controller-only instructions"}
        request_id = digest(packet)
        common = {"request_id": request_id, "response_id": "response-1",
                  "requested_model": "chosen", "evidenced_model": "chosen",
                  "model_evidence": "provider_response_header"}
        stream = "\n".join(json.dumps(x) for x in (
            {"type": "response_metadata", **common},
            {"type": "result", **common, "output": {"wrong_stage_shape": True}},
        ))
        config = {"output": "codex-data-only", "auth_mode": "codex-subscription",
                  "filesystem_scope": "codex-home-auth-only",
                  "loader_policy": "pe-dependent-load-system32",
                  "attestation": {"mode": "provider-response-header"},
                  "argv": [sys.executable, "--model", "{model}"]}
        with patch("project_creator.providers.validate_capability"), \
                patch("project_creator.providers.execute", return_value=Result(0, stream, "", 0.1)):
            with self.assertRaisesRegex(GateError, "stage schema"):
                CLIProvider("codex", config, audit=lambda *a: None).invoke(
                    "implement", "chosen", packet, Path.cwd())

    def test_every_provider_schema_closes_all_object_shapes(self):
        def visit(value):
            if not isinstance(value, dict):
                return
            if value.get("type") == "object":
                self.assertIs(value.get("additionalProperties"), False)
                self.assertEqual(set(value.get("required", [])), set(value.get("properties", {})))
            for child in value.values():
                if isinstance(child, list):
                    for item in child:
                        visit(item)
                else:
                    visit(child)
        self.assertEqual(set(SCHEMAS), {"grill-with-docs", "to-spec", "to-tickets",
                                        "implement", "spec-review", "defect-review"})
        for schema in SCHEMAS.values():
            visit(schema)

    def test_local_schema_validator_rejects_bool_as_integer_and_extra_keys(self):
        valid = {"verdict": "pass", "checked_criteria": [], "findings": [], "limitations": []}
        self.assertIs(validate_output("spec-review", valid), valid)
        for invalid in (
            valid | {"extra": True},
            valid | {"verdict": "unknown"},
            valid | {"findings": [{"id": "F", "priority": True, "path": None,
                                    "line": None, "message": "bad", "criterion": None,
                                    "disposition": None}]},
        ):
            with self.assertRaisesRegex(GateError, "stage schema"):
                validate_output("spec-review", invalid)

    def test_exact_json_fence_only(self):
        self.assertEqual(self.parse(stream(result='```json\n{"ok":true}\n```'))[0], {"ok": True})
        for result in ('before {"ok":true}', '[1]', '```json\n{}\n```\nrun shell'):
            with self.assertRaises((GateError, ValueError)):
                self.parse(stream(result=result))

    def test_subscription_environment_no_api_route(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "synthetic", "ANTHROPIC_BASE_URL": "synthetic", "ANTHROPIC_CUSTOM_HEADERS": "synthetic", "NODE_OPTIONS": "synthetic", "CLAUDE_CONFIG_DIR": "synthetic", "HTTPS_PROXY": "synthetic"}):
            env = provider_environment({"auth_mode": "claude-subscription"})
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("ANTHROPIC_BASE_URL", env)
            self.assertNotIn("ANTHROPIC_CUSTOM_HEADERS", env)
            for key in ("NODE_OPTIONS", "CLAUDE_CONFIG_DIR", "HTTPS_PROXY"):
                self.assertNotIn(key, env)
            self.assertEqual(env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"], "1")
            self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "synthetic")

    def test_docker_rejects_unpinned_image_and_pre_cancel(self):
        cfg = docker_config() | {"image": "python:latest"}
        with self.assertRaises(GateError):
            DockerSandbox(cfg, ["source.py"])
        cfg["image"] = "python@sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as tmp, patch("project_creator.docker_sandbox.execute") as launch:
            runner = DockerSandbox(cfg, ["source.py"], cancelled=lambda: True)
            with self.assertRaises(GateError):
                runner.run(["python", "source.py"], Path(tmp), "test")
            launch.assert_not_called()

    def test_docker_rejects_remote_daemon_and_ignores_ambient_context(self):
        with self.assertRaises(GateError):
            DockerSandbox(docker_config() | {"daemon_host": "tcp://example.invalid:2375"}, ["source.py"])
        with patch.dict(os.environ, {"DOCKER_CONTEXT": "untrusted", "DOCKER_HOST": "tcp://example.invalid:2375"}), patch("project_creator.docker_sandbox.execute", return_value=Result(0, "", "", 0)) as execute:
            DockerSandbox(docker_config(), ["source.py"]).command(["version"], Path.cwd())
            call = execute.call_args
            self.assertEqual(call.args[0][1:3], ["--host", "npipe:////./pipe/fixture"])
            self.assertFalse("DOCKER_CONTEXT" in call.kwargs["env"])
            self.assertFalse("DOCKER_HOST" in call.kwargs["env"])
            self.assertTrue("project-creator-docker-client-" in call.kwargs["env"]["DOCKER_CONFIG"])
            self.assertTrue(call.kwargs["system_cwd"])

    def test_only_data_worker_and_docker_receive_reviewed_runtime_inputs(self):
        proof = {"executable_sha256": "a" * 64,
                 "runtime_files": {}}
        config = {"output": "codex-data-only", "auth_mode": "codex-subscription",
                  "filesystem_scope": "codex-home-auth-only",
                  "loader_policy": "pe-dependent-load-system32",
                  "attestation": {"mode": "provider-response-header"},
                  "argv": [sys.executable, "--model", "{model}"], "proof": proof}
        packet = {"instructions": "bounded"}
        request_id = digest(packet)
        common = {"request_id": request_id, "response_id": "response-1",
                  "requested_model": "chosen", "evidenced_model": "chosen",
                  "model_evidence": "provider_response_header"}
        response = "\n".join(json.dumps(x) for x in (
            {"type": "response_metadata", **common},
            {"type": "result", **common,
             "output": {"changes": [], "summary": "none", "questions": []}},
        ))
        with patch("project_creator.providers.validate_capability"), \
                patch("project_creator.providers.execute", return_value=Result(0, response, "", 0)) as launch:
            CLIProvider("codex", config, audit=lambda *a: None).invoke(
                "implement", "chosen", packet, Path.cwd())
            self.assertEqual(launch.call_args.kwargs["expected_runtime_sha256"], proof["runtime_files"])
            self.assertTrue(launch.call_args.kwargs["system_cwd"])

        native = {"argv": [sys.executable], "proof": proof}
        with patch("project_creator.providers.validate_capability"), \
                patch("project_creator.providers.execute", return_value=Result(0, "", "", 0)) as launch:
            with self.assertRaisesRegex(GateError, "Docker verification"):
                VerificationRunner(native, {"unit": ["-c", "pass"]}).run(["unit"], Path.cwd())
            launch.assert_not_called()

        docker = docker_config() | {"proof": proof}
        with patch("project_creator.docker_sandbox.execute", return_value=Result(0, "", "", 0)) as launch:
            DockerSandbox(docker, ["source.py"]).command(["version"], Path.cwd())
            self.assertEqual(launch.call_args.kwargs["expected_executable_sha256"], "a" * 64)
            self.assertEqual(launch.call_args.kwargs["expected_runtime_sha256"], proof["runtime_files"])

    def test_docker_creates_inspects_then_starts_exact_entrypoint_without_pull(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source.py").write_text("pass")
            runner = DockerSandbox(docker_config(), ["source.py"])
            seen = []
            def call(args, cwd, **kwargs):
                seen.append(args)
                return Result(0, "c" * 64 if args[0] == "create" else "", "", 0)
            with patch.object(runner, "remove_owned"), patch.object(runner, "command", side_effect=call), patch.object(runner, "verify_container") as inspect:
                runner.run(["python", "source.py"], root, "unit",
                           expected_files={"source.py": digest((root / "source.py").read_bytes())})
                create = next(x for x in seen if x[0] == "create")
                self.assertIn("--pull=never", create)
                self.assertIn("--no-healthcheck", create)
                self.assertEqual(create[create.index("--log-driver") + 1], "none")
                mounts = [create[index + 1] for index, value in enumerate(create) if value == "--mount"]
                self.assertEqual(len(mounts), 2)
                self.assertIn("type=tmpfs,target=/workspace", mounts[0])
                self.assertIn("readonly", mounts[0])
                self.assertIn("target=/workspace/source.py,readonly", mounts[1])
                i = create.index("--entrypoint")
                self.assertEqual(create[i+1], "python")
                self.assertEqual(create[i+2], docker_config()["image"])
                self.assertEqual(create[i+3:i+5], ["-I", "-c"])
                self.assertEqual(create[-2:], ["python", "source.py"])
                self.assertEqual(seen[-1][:2], ["start", "--attach"])
                self.assertEqual(seen[-1][-1], "c" * 64)
                inspect.assert_called_once()

    def test_cleanup_does_not_mistake_id_for_name(self):
        runner = DockerSandbox(docker_config(), ["source.py"])
        identity = "c" * 64
        with patch.object(runner, "verify_daemon"), patch.object(runner, "command", side_effect=[Result(1, "", "", 0), Result(0, identity, "", 0)]) as command:
            with self.assertRaisesRegex(GateError, "ownership cannot"):
                runner.remove_owned(identity, "owner", Path.cwd())
            self.assertIn("id=" + identity, command.call_args_list[-1].args[0])

    def test_realized_healthcheck_and_logging_must_be_disabled(self):
        runner = DockerSandbox(docker_config(), ["source.py"])
        root = Path.cwd()
        identity = "c" * 64
        source = root / "source.py"
        value = {"Id": identity, "State": {"Running": False},
                 "Mounts": [{"Type": "tmpfs", "Source": "", "Destination": "/workspace", "RW": False},
                            {"Type": "bind", "Source": str(source), "Destination": "/workspace/source.py", "RW": False}],
                 "HostConfig": {"ReadonlyRootfs": True, "NetworkMode": "none", "Privileged": False, "CapDrop": ["ALL"],
                                 "SecurityOpt": ["no-new-privileges:true"], "Memory": 512*1024*1024, "NanoCpus": 1_000_000_000,
                                 "PidsLimit": 128, "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"}, "LogConfig": {"Type": "none", "Config": {}},
                                 "Binds": None,
                                 "Mounts": [{"Type": "tmpfs", "Target": "/workspace", "ReadOnly": True,
                                             "TmpfsOptions": {"SizeBytes": 1048576, "Mode": 365}},
                                            {"Type": "bind", "Source": str(source), "Target": "/workspace/source.py",
                                             "ReadOnly": True}]},
                 "Config": {"User": "65534:65534", "Healthcheck": {"Test": ["NONE"]}, "Entrypoint": ["python"],
                            "Cmd": [], "WorkingDir": "/workspace", "Image": docker_config()["image"]}}
        guarded = runner.guarded_command(["python", "source.py"], {"source.py": source})
        value["Config"]["Cmd"] = guarded[1:]
        with patch.object(runner, "command", return_value=Result(0, json.dumps(value), "", 0)):
            runner.verify_container(identity, {"source.py": source}, guarded, root)
        value["HostConfig"]["LogConfig"]["Type"] = "syslog"
        with patch.object(runner, "command", return_value=Result(0, json.dumps(value), "", 0)), self.assertRaisesRegex(GateError, "daemon_logging"):
            runner.verify_container(identity, {"source.py": source}, ["python", "source.py"], root)
        value["HostConfig"]["LogConfig"]["Type"] = "none"
        value["Config"]["Healthcheck"]["Test"] = ["CMD", "unapproved"]
        with patch.object(runner, "command", return_value=Result(0, json.dumps(value), "", 0)), self.assertRaisesRegex(GateError, "healthcheck"):
            runner.verify_container(identity, {"source.py": source}, ["python", "source.py"], root)

    def test_docker_proof_requires_controller_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            cfg = docker_config() | {"kind": "docker"}
            checked_at = datetime.now(timezone.utc).isoformat()
            executable_sha256 = digest(Path(sys.executable).read_bytes())
            payload = {"schema_version": 1, "purpose": "verification", "configuration_hash": digest(cfg), "probe_harness_sha256": "fixture",
                       "checked_at": checked_at, "executable_sha256": executable_sha256,
                       "runtime_files": {},
                       "cases": {x: {"passed": True, "measurement": {"fixture": True}}
                                 for x in ("exact_source_set", "outside_read_denied", "outside_write_denied", "network_denied", "child_cleanup")}}
            evidence.write_text(json.dumps(payload))
            sha = digest(evidence.read_bytes())
            cfg["proof"] = {"schema_version": 1, "probe_harness_sha256": "fixture", "checked_at": checked_at, "configuration_hash": digest(cfg),
                            "executable_sha256": executable_sha256, "runtime_files": {}, "evidence_files": {sha: str(evidence)},
                            "cases": {x: {"expected": True, "observed": True, "passed": True, "evidence_sha256": sha}
                                      for x in ("exact_source_set", "outside_read_denied", "outside_write_denied", "network_denied", "child_cleanup")}}
            with patch.dict("project_creator.providers._HOST_CAPABILITIES", {digest(cfg): "verification"}), \
                    patch("project_creator.providers.assert_non_augmentable_directory"):
                with self.assertRaisesRegex(GateError, "runtime closure file missing"):
                    validate_capability(cfg, "verification")
            evidence.write_text("fabricated")
            fake_hash = digest(evidence.read_bytes())
            cfg["proof"]["evidence_files"] = {fake_hash: str(evidence)}
            for case in cfg["proof"]["cases"].values():
                case["evidence_sha256"] = fake_hash
            with patch.dict("project_creator.providers._HOST_CAPABILITIES", {digest(cfg): "verification"}), \
                    patch("project_creator.providers.assert_non_augmentable_directory"):
                with self.assertRaisesRegex(GateError, "does not substantiate"):
                    validate_capability(cfg, "verification")

    def test_capability_rejects_rewrapped_old_or_other_executable_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            cfg = {"argv": [sys.executable, "--model", "{model}"], "output": "codex-data-only",
                   "filesystem_scope": "codex-home-auth-only",
                   "loader_policy": "pe-dependent-load-system32",
                   "auth_mode": "codex-subscription",
                   "attestation": {"mode": "provider-response-header"}}
            current = datetime.now(timezone.utc).isoformat()
            cases = ("fresh_context", "tools_disabled", "ambient_not_observed_in_output", "child_cleanup",
                     "model_attestation", "subscription_auth_only",
                     "dependent_load_flags_system32", "delay_imports_absent",
                     "imports_allowlisted")
            base = {"schema_version": 1, "purpose": "provider", "configuration_hash": digest(cfg),
                    "probe_harness_sha256": "fixture", "checked_at": "2000-01-01T00:00:00+00:00",
                    "executable_sha256": "0" * 64, "runtime_files": {},
                    "cases": {case: {"passed": True, "measurement": {"fixture": True}}
                              for case in cases}}
            evidence.write_text(json.dumps(base))
            evidence_hash = digest(evidence.read_bytes())
            cfg["proof"] = {"schema_version": 1, "probe_harness_sha256": "fixture",
                            "checked_at": current, "configuration_hash": digest(cfg),
                            "executable_sha256": digest(Path(sys.executable).read_bytes()),
                            "environment_hash": digest(provider_environment(cfg)),
                            "runtime_files": {}, "evidence_files": {evidence_hash: str(evidence)},
                            "cases": {case: {"expected": True, "observed": True, "passed": True,
                                             "evidence_sha256": evidence_hash} for case in cases}}
            with patch.dict("project_creator.providers._HOST_CAPABILITIES", {digest(cfg): "provider"}):
                with self.assertRaisesRegex(GateError, "does not substantiate"):
                    validate_capability(cfg, "provider", name="codex", environment=provider_environment(cfg))


    PROOF_CASES = ("fresh_context", "tools_disabled", "ambient_not_observed_in_output", "child_cleanup",
                   "model_attestation", "subscription_auth_only",
                   "dependent_load_flags_system32", "delay_imports_absent", "imports_allowlisted")
    MEASURED = {"evidenced_positive_calls": 4, "provider_response_header_calls": 0,
                "recorded_response_model_calls": 4, "recorded_tier_withdrawn": False,
                "unserved_echo_observable": False}

    def pinned_provider(self, measurement=None, *, mode="recorded-response-model", checked_at=None,
                        evidence_changes=None, proof_changes=None, config_changes=None):
        """A provider record the gate ADMITS, so the panel has something it may report.

        Since round 24 the panel runs the gate, so a fixture the gate refuses can only ever
        exercise the refusal path. A test of what the panel SHOWS therefore needs a record that
        passes every binding, which is what this builds.
        """
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, True)
        evidence = directory / "evidence.json"
        cfg = {"argv": [sys.executable, "--model", "{model}"], "output": "codex-data-only",
               "filesystem_scope": "codex-home-auth-only",
               "loader_policy": "pe-dependent-load-system32",
               "auth_mode": "codex-subscription", "attestation": {"mode": mode}}
        cfg |= config_changes or {}
        current = checked_at or datetime.now(timezone.utc).isoformat()
        executable = digest(Path(sys.executable).read_bytes())
        measurement = self.MEASURED if measurement is None else measurement
        record = {"schema_version": 1, "purpose": "provider", "configuration_hash": digest(cfg),
                  "probe_harness_sha256": "fixture", "checked_at": current,
                  "executable_sha256": executable, "runtime_files": {},
                  "cases": {case: {"passed": True, "measurement": (
                      measurement if case == "model_attestation" else {"fixture": True})}
                      for case in self.PROOF_CASES}}
        record |= evidence_changes or {}
        evidence.write_text(json.dumps(record))
        evidence_hash = digest(evidence.read_bytes())
        cfg["proof"] = {"schema_version": 1, "probe_harness_sha256": "fixture", "checked_at": current,
                        "configuration_hash": digest(cfg), "executable_sha256": executable,
                        "environment_hash": digest(provider_environment(cfg)),
                        "runtime_files": {}, "evidence_files": {evidence_hash: str(evidence)},
                        "cases": {case: {"expected": True, "observed": True, "passed": True,
                                         "evidence_sha256": evidence_hash} for case in self.PROOF_CASES}}
        cfg["proof"] |= proof_changes or {}
        return cfg

    def panel(self, cfg, *, pin=True, name="codex"):
        from project_creator.providers import attestation_facts
        pins = {digest(cfg): "provider"} if pin else {}
        with patch.dict("project_creator.providers._HOST_CAPABILITIES", pins, clear=not pin):
            return attestation_facts(cfg, name=name)

    def test_panel_reports_what_the_gate_validated_and_nothing_else(self):
        facts = self.panel(self.pinned_provider())
        self.assertIs(facts["verified"], True)
        self.assertIs(facts["available"], True)
        self.assertIsNone(facts["reason"])
        self.assertIs(facts["answering_model_proven"], False)
        self.assertEqual(facts["evidenced_positive_calls"], 4)
        self.assertEqual(facts["recorded_response_model_calls"], 4)
        self.assertEqual(facts["provider_response_header_calls"], 0)
        self.assertIs(facts["recorded_tier_withdrawn"], False)
        self.assertIs(facts["unserved_echo_observable"], False)
        self.assertLess(facts["age_days"], 1)
        self.assertGreater(facts["expires_in_days"], 29)
        self.assertEqual(facts["declared_evidence_floor"], "recorded-response-model")
        self.assertIn("not proven", facts["statement"])
        self.assertIn("untested", facts["statement"])
        # Live run 3's own shape must produce a clean panel, or the warnings that carry
        # information are read as noise.
        self.assertEqual(facts["warnings"], [])

    def test_readiness_owner_reports_proof_current_for_a_record_the_gate_admits(self):
        # R23-SEC-08: no test produced `verified: true` through the readiness owner, so a mutant
        # hardcoding that limb to False would have survived the whole suite.
        from project_creator.cli import provider_panel
        cfg = self.pinned_provider()
        with patch.dict("project_creator.providers._HOST_CAPABILITIES", {digest(cfg): "provider"}):
            verdict, facts = provider_panel(cfg, "codex")
        self.assertEqual(verdict, "proof-current")
        self.assertIs(facts["verified"], True)
        self.assertEqual(facts["evidenced_positive_calls"], 4)

    def test_readiness_surfaces_answer_for_the_vendor_key_not_only_the_record(self):
        # R24-SPEC-01 / R24-SEC-01: the host pin is keyed on the record's digest and the record
        # does not contain its own key, so the one legitimately pinned adapter copied under a
        # second key is pinned too. `invoke` refuses every launch under that key; both readiness
        # surfaces must say so. The `codex` copy of the same bytes is the control.
        from project_creator.cli import announce_providers, main
        cfg = self.pinned_provider()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict("project_creator.providers._HOST_CAPABILITIES", {digest(cfg): "provider"}):
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"providers": {"codex": cfg, "second": cfg}}))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["doctor", "--config", str(config)])
            report = json.loads(out.getvalue())
            announced = io.StringIO()
            with contextlib.redirect_stdout(announced):
                announce_providers({"providers": {"codex": cfg, "second": cfg}})
            panel = json.loads(announced.getvalue())["provider_attestation"]
        with self.subTest("doctor"):
            self.assertEqual(report["checks"]["providers"]["codex"], "proof-current")
            self.assertIn("vendor key", report["checks"]["providers"]["second"])
            self.assertIs(report["provider_attestation"]["second"]["verified"], False)
            self.assertNotEqual(report["status"], "configured-gates-pass")
        with self.subTest("announcement"):
            self.assertIs(panel["codex"]["verified"], True)
            self.assertIs(panel["second"]["verified"], False)
            self.assertNotIn("evidenced_positive_calls", panel["second"])
        with self.subTest("invoke refuses the same key"):
            with patch("project_creator.providers.execute") as execute, \
                    patch.dict("project_creator.providers._HOST_CAPABILITIES", {digest(cfg): "provider"}):
                with self.assertRaisesRegex(GateError, "vendor key"):
                    CLIProvider("second", cfg, audit=lambda *a: None).invoke(
                        "implement", "chosen", {}, Path.cwd())
                execute.assert_not_called()

    def test_gate_hands_back_nothing_when_a_later_binding_refuses(self):
        # R24-SEC-02: the measurement used to be written mid-loop, so a record with valid
        # attestation evidence and a changed executable left numbers in `observed` after the
        # gate had refused. The caller's early return was the only thing keeping them off screen.
        cfg = self.pinned_provider(proof_changes={"executable_sha256": "0" * 64},
                                   evidence_changes={"executable_sha256": "0" * 64})
        observed = {}
        with patch.dict("project_creator.providers._HOST_CAPABILITIES", {digest(cfg): "provider"}):
            with self.assertRaisesRegex(GateError, "executable changed after proof"):
                validate_capability(cfg, "provider", name="codex", observed=observed)
        self.assertEqual(observed, {})

    def test_panel_shows_the_gate_refusal_and_no_numbers_at_all(self):
        # The whole point of the collapse: when the gate refuses there is nothing measured to
        # report, so nothing is reported. The refusal is the finding.
        for label, cfg, pin, fragment in (
                ("not pinned", self.pinned_provider(), False, "trusted host"),
                ("evidence from another executable",
                 self.pinned_provider(evidence_changes={"executable_sha256": "0" * 64}), True,
                 "does not substantiate"),
                ("evidence for another configuration",
                 self.pinned_provider(evidence_changes={"configuration_hash": "c" * 64}), True,
                 "does not substantiate"),
                ("evidence case not passed",
                 self.pinned_provider(evidence_changes={"cases": {
                     case: {"passed": case != "fresh_context", "measurement": {"fixture": True}}
                     for case in ("fresh_context", "model_attestation")}}), True,
                 "does not substantiate"),
                ("proof past its expiry",
                 self.pinned_provider(checked_at=(datetime.now(timezone.utc) - timedelta(days=31)).isoformat()),
                 True, "expired or absent"),
                ("record states another check time",
                 self.pinned_provider(proof_changes={"checked_at": datetime.now(timezone.utc).isoformat()}),
                 True, "does not substantiate"),
                ("not in a launchable shape",
                 self.pinned_provider(config_changes={"auth_mode": "api-key"}), True, "launchable")):
            with self.subTest(label):
                facts = self.panel(cfg, pin=pin)
                self.assertIs(facts["verified"], False)
                self.assertIs(facts["available"], False)
                self.assertIn(fragment, facts["reason"])
                self.assertEqual(facts["warnings"], [])
                for key in ("evidenced_positive_calls", "recorded_tier_withdrawn", "checked_at",
                            "age_days", "expires_in_days"):
                    self.assertNotIn(key, facts, key)

    def test_panel_warnings_are_each_reachable_and_none_is_permanent(self):
        soon = (datetime.now(timezone.utc) - timedelta(days=28)).isoformat()
        warnings = self.panel(self.pinned_provider(checked_at=soon))["warnings"]
        self.assertTrue(any("expires within three days" in w for w in warnings))
        mixed = self.MEASURED | {"provider_response_header_calls": 2, "recorded_response_model_calls": 2}
        header = self.panel(self.pinned_provider(mixed, mode="provider-response-header"))["warnings"]
        self.assertTrue(any("after the call is spent" in w for w in header))
        header_only = self.MEASURED | {"provider_response_header_calls": 4,
                                       "recorded_response_model_calls": 0}
        self.assertEqual(self.panel(self.pinned_provider(header_only,
                                                         mode="provider-response-header"))["warnings"], [])
        impossible = self.MEASURED | {"unserved_echo_observable": True}
        self.assertTrue(any("no run of this harness produces" in w
                            for w in self.panel(self.pinned_provider(impossible))["warnings"]))

    def test_panel_never_raises_on_any_record_shape(self):
        from project_creator.providers import attestation_facts
        for label, cfg in (("not a mapping", "codex"), ("empty", {}), ("proof not a mapping", {"proof": []}),
                           ("argv empty", {"argv": [], "output": "codex-data-only",
                                           "filesystem_scope": "codex-home-auth-only",
                                           "loader_policy": "pe-dependent-load-system32",
                                           "auth_mode": "codex-subscription",
                                           "attestation": {"mode": "recorded-response-model"}})):
            with self.subTest(label):
                facts = attestation_facts(cfg)
                self.assertIs(facts["verified"], False)
                self.assertIs(facts["available"], False)
                self.assertTrue(facts["reason"])
        # A record the host HAS pinned can take the gate past its shape checks into a raise no
        # GateError covers; the summary reports that instead of ending the command.
        broken = {"argv": [sys.executable, "--model", "{model}"], "output": "codex-data-only",
                  "filesystem_scope": "codex-home-auth-only",
                  "loader_policy": "pe-dependent-load-system32", "auth_mode": "codex-subscription",
                  "attestation": {"mode": "recorded-response-model"}, "proof": []}
        facts = self.panel(broken)
        self.assertIs(facts["verified"], False)
        self.assertIn("unreadable", facts["reason"])

    def test_the_unreachable_for_a_provider_claims_rest_on_a_clause_this_test_holds(self):
        """R27, INVERTER F4: `unreachable for a provider` is a theorem, not a property of the line.

        Four conditions of `validate_capability` carry no test row, on a recorded reason of the form
        "unreachable for a provider". That is never a fact about those four lines. It follows from a
        clause somewhere else, and both clauses it follows from have already been widened once each
        (R23-SPEC-04, R24-SPEC-01). Written as prose in a registry field, the claim keeps reading
        true for as long as nobody re-derives it, which is to say forever.

        This test is the re-derivation. It fails on the day either clause moves, which is the only
        form of that claim worth holding.
        """
        scripted = {".py", ".js", ".mjs", ".cjs", ".exe"}
        launchable = {"auth_mode": "codex-subscription", "attestation": {"mode": "recorded-response-model"}}
        pinned_tail = ["--model", "{model}"]

        # CLAUSE ONE: the launchable shape pins the argv tail to exactly these two elements, so the
        # script-or-binary scan below it can never see an argument with one of those suffixes.
        self.assertIsNone(_launch_refusal(launchable | {"argv": [sys.executable] + pinned_tail}, "codex"))
        for tail in ([], ["--model"], ["--model", "{model}", "helper.py"], ["--script", "run.py"],
                     ["--model", "{model}", "payload.exe"], ["{model}", "--model"]):
            with self.subTest(tail=tail):
                self.assertIsNotNone(_launch_refusal(launchable | {"argv": [sys.executable] + tail}, "codex"),
                                     "the argv pin widened; the script-scan conditions may now be reachable "
                                     "and their registry dispositions have to be re-derived")
        for argument in pinned_tail:
            self.assertNotIn(Path(argument).suffix.lower(), scripted)

        # CLAUSE TWO: a provider carrying any runtime file at all is refused before the dependency
        # loop, so that loop runs only over an empty mapping and its body is never entered. The
        # refusal itself has its own row; what this holds is the ORDER the unreachability rests on.
        source = Path(providers_module.__file__).read_text(encoding="utf-8")
        refusal = source.index('raise GateError("data-only provider must be one reviewed native executable")')
        loop = source.index("for dependency, expected_hash in dependencies.items():")
        self.assertLess(refusal, loop, "the dependency loop moved above the refusal that empties it")

    def test_capability_admission_refuses_on_every_condition_it_states(self):
        """REQ-LC-023: one row per refusing condition of `validate_capability`, changed alone.

        The list is closed on purpose (D7, D9, D10): a review finding outside it is a residual,
        a finding inside it blocks. This docstring deliberately makes no completeness claim.
        Which conditions have a row, which rows are isolated by their mutant, and which
        conditions are owed, equivalent or a recorded debt is computed from this list, the
        gate's own source and the round's mutation log by the plan repository's coverage
        ledger, which refuses to emit a paragraph when any of those disagree. Round 24 showed a
        hand-written account here drifting from the code within one round (R24-SPEC-02).

        Every row is computed inside `check`, which turns an exception other than GateError
        into the text "raised <type>". The rows are built before the subTest loop, so without
        that an exception from one row ended the whole method with no row label, and a mutant
        killed that way could not be told apart from one killed by the row it names.
        """
        cases = ("fresh_context", "tools_disabled", "ambient_not_observed_in_output", "child_cleanup",
                 "model_attestation", "subscription_auth_only",
                 "dependent_load_flags_system32", "delay_imports_absent", "imports_allowlisted")
        recorded_run = {"evidenced_positive_calls": 4, "provider_response_header_calls": 0,
                        "recorded_response_model_calls": 4, "recorded_tier_withdrawn": False,
                        "unserved_echo_observable": False}
        header_run = recorded_run | {"provider_response_header_calls": 4, "recorded_response_model_calls": 0}
        honest = {"answering_model_proven": False, "echo_tested": False}

        def check(attestation, measurement, limits=None, evidence_changes=None, proof_changes=None,
                  config_changes=None, case_changes=None, evidence_files=None, environment=None,
                  argv=UNSET, host_pin=True, name="codex", evidence_raw=None):
            try:
                return admission(attestation, measurement, limits, evidence_changes, proof_changes,
                                 config_changes, case_changes, evidence_files, environment, argv,
                                 host_pin, name, evidence_raw)
            except Exception as exc:  # noqa: BLE001 - reported per row, never swallowed silently
                return "raised " + type(exc).__name__

        def admission(attestation, measurement, limits, evidence_changes, proof_changes,
                      config_changes, case_changes, evidence_files, environment, argv, host_pin,
                      name, evidence_raw):
            with tempfile.TemporaryDirectory() as tmp:
                evidence = Path(tmp) / "evidence.json"
                if callable(argv):
                    argv = argv(Path(tmp))
                # A sentinel, not `argv or [...]`: a row that gives argv as None or as a string is
                # testing exactly the shape checks that a falsy default would hide.
                cfg = {"argv": [sys.executable, "--model", "{model}"] if argv is UNSET else argv,
                       "output": "codex-data-only",
                       "filesystem_scope": "codex-home-auth-only",
                       "loader_policy": "pe-dependent-load-system32",
                       "auth_mode": "codex-subscription"}
                if attestation is not None:
                    cfg["attestation"] = attestation
                cfg |= config_changes or {}
                current = datetime.now(timezone.utc).isoformat()
                executable = digest(Path(sys.executable).read_bytes())
                record = {"schema_version": 1, "purpose": "provider", "configuration_hash": digest(cfg),
                          "probe_harness_sha256": "fixture", "checked_at": current,
                          "executable_sha256": executable, "runtime_files": {},
                          "cases": {case: {"passed": True, "measurement": (
                              measurement if case == "model_attestation" else {"fixture": True})}
                              for case in cases}}
                record |= evidence_changes or {}
                # `evidence_raw` writes the file's bytes directly. Every other row reaches the gate
                # through `json.dumps` of a mapping, which is structurally unable to produce evidence
                # that is not JSON at all, or that is JSON but not an object. Those two refusals were
                # recorded as untestable when what could not produce them was this helper, not the
                # gate (R27, INVERTER F6).
                if evidence_raw is None:
                    evidence.write_text(json.dumps(record))
                else:
                    evidence.write_bytes(evidence_raw)
                evidence_hash = digest(evidence.read_bytes())
                cfg["proof"] = {"schema_version": 1, "probe_harness_sha256": "fixture",
                                "checked_at": current, "configuration_hash": digest(cfg),
                                "executable_sha256": executable,
                                "environment_hash": digest(provider_environment(cfg)),
                                "runtime_files": {}, "evidence_files": {evidence_hash: str(evidence)},
                                "cases": {case: {"expected": True, "observed": True, "passed": True,
                                                 "evidence_sha256": evidence_hash} for case in cases}}
                if limits is not None:
                    cfg["proof"]["limits"] = limits
                if case_changes is not None:
                    cfg["proof"]["cases"]["model_attestation"] |= case_changes
                if evidence_files is not None:
                    cfg["proof"]["evidence_files"] = {evidence_hash: evidence_files(evidence)}
                cfg["proof"] |= proof_changes or {}
                # A None in proof_changes deletes the key, which is how an absent field is tested.
                cfg["proof"] = {key: value for key, value in cfg["proof"].items() if value is not None}
                pins = {digest(cfg): "provider"} if host_pin else {}
                with patch.dict("project_creator.providers._HOST_CAPABILITIES", pins, clear=not host_pin):
                    try:
                        validate_capability(cfg, "provider", name=name,
                                            environment=environment or provider_environment(cfg))
                        return "admitted"
                    except GateError as exc:
                        return str(exc)

        def other_case(attested, **overrides):
            """Evidence whose `fresh_context` case carries the change, not `model_attestation`.

            The REQ-LC-020 invariant answers for the attestation case first, so a row that
            changed that case could not tell whether the per-case check still existed.
            """
            built = {case: {"passed": True, "measurement": (
                attested if case == "model_attestation" else {"fixture": True})} for case in cases}
            built["fresh_context"] |= overrides
            return {"cases": built}

        def swapped(path):
            """A different file at a different path, so the recorded hash no longer matches."""
            other = path.with_name("swapped.json")
            other.write_text("{}")
            return str(other)

        recorded = {"mode": "recorded-response-model"}
        header = {"mode": "provider-response-header"}
        substantiate = "retained evidence does not substantiate the claimed capability"
        incomplete = "provider containment cases incomplete"
        evidence_gone = "provider retained test evidence absent or changed"
        now = datetime.now(timezone.utc)
        expectations = (
            # D10: the gate no longer reads the attestation label, so none of these refuse.
            ("recorded mode, recorded run", check(recorded, recorded_run, honest), "admitted"),
            ("recorded mode, header run", check(recorded, header_run, honest), "admitted"),
            ("header mode, recorded run", check(header, recorded_run), "admitted"),
            ("header mode, recorded-mode limits stated", check(header, header_run, honest), "admitted"),
            ("recorded mode, echo flipped in the limits", check(recorded, recorded_run, honest | {"echo_tested": True}),
             "admitted"),
            ("recorded mode, limits overstate the proof",
             check(recorded, recorded_run, honest | {"answering_model_proven": True}), "admitted"),
            # R22-SEC-02: naming a floor asserts nothing about the world, it says which tier
            # `invoke` will enforce, so the well-formedness half of the old check is restored.
            ("no attestation block at all", check(None, header_run),
             "provider capability is not in a launchable data-only shape"),
            ("attestation block with an extra key", check({"mode": "recorded-response-model", "echo": False},
                                                          recorded_run, honest),
             "provider capability is not in a launchable data-only shape"),
            ("attestation naming an unknown mode", check({"mode": "trust-me"}, recorded_run, honest),
             "provider capability is not in a launchable data-only shape"),
            ("attestation whose mode is not a string", check({"mode": ["recorded-response-model"]},
                                                             recorded_run, honest),
             "provider capability is not in a launchable data-only shape"),
            # The one measured invariant that survived (REQ-LC-020).
            ("recorded tier withdrawn", check(recorded, recorded_run | {"recorded_tier_withdrawn": True}, honest),
             substantiate),
            ("recorded tier withdrawal absent", check(recorded, {k: v for k, v in recorded_run.items()
                                                                if k != "recorded_tier_withdrawn"}, honest),
             substantiate),
            ("no evidenced positive call", check(recorded, recorded_run | {"evidenced_positive_calls": 0}, honest),
             substantiate),
            ("evidenced positive calls not an integer",
             check(recorded, recorded_run | {"evidenced_positive_calls": True}, honest), substantiate),
            # Host admission and the data-only provider shape.
            ("not pinned by the host", check(recorded, recorded_run, honest, host_pin=False),
             "capability is not pinned by the trusted host admission registry"),
            ("another output kind", check(recorded, recorded_run, honest, config_changes={"output": "codex-agent"}),
             "provider is not an admitted data-only worker"),
            ("another filesystem scope", check(recorded, recorded_run, honest,
                                               config_changes={"filesystem_scope": "project"}),
             "provider is not an admitted data-only worker"),
            ("another loader policy", check(recorded, recorded_run, honest,
                                            config_changes={"loader_policy": "default"}),
             "provider is not an admitted data-only worker"),
            # The one branch a provider record can still steer itself into. `kind` is read from the
            # record, not from the purpose, so a provider that declares itself a Docker capability
            # enters the branch and is refused inside it. It was recorded as reachable and always
            # refusing, with no row and no mutant, for two rounds (R27).
            #
            # MEASURED, not assumed: the refusal arrives at the directory guard on the branch's first
            # line, before the closure checks below it. The expectation this row was written with was
            # the closure message, and the row itself corrected it. Which means the two closure
            # conditions stay unreachable behind this guard for any provider whose executable sits in
            # a directory the active token can augment, and they keep their recorded debt rather than
            # a row that would have to claim more than it proves.
            ("provider record declaring the docker kind",
             check(recorded, recorded_run, honest, config_changes={"kind": "docker"}),
             "executable directory is augmentable by the active token"),
            # Freshness and the bindings to this exact configuration and environment.
            ("another environment", check(recorded, recorded_run, honest, environment={"PATH": "elsewhere"}),
             "provider auth-home/runtime environment changed after host review"),
            ("proof for another configuration", check(recorded, recorded_run, honest,
                                                      proof_changes={"configuration_hash": "c" * 64}),
             "provider configuration has no matching containment proof"),
            ("no check time", check(recorded, recorded_run, honest, proof_changes={"checked_at": None}),
             "provider containment proof expired or absent"),
            ("check time without a zone", check(recorded, recorded_run, honest,
                                                proof_changes={"checked_at": datetime.now().isoformat()}),
             "provider containment proof expired or absent"),
            ("check time in the future", check(recorded, recorded_run, honest,
                                               proof_changes={"checked_at": (now + timedelta(days=1)).isoformat()}),
             "provider containment proof expired or absent"),
            ("check time past the 30-day expiry",
             check(recorded, recorded_run, honest,
                   proof_changes={"checked_at": (now - timedelta(days=31)).isoformat()}),
             "provider containment proof expired or absent"),
            # The required cases and their per-case flags.
            ("a required case missing", check(recorded, recorded_run, honest, proof_changes={"cases": {}}),
             incomplete),
            ("case not passed", check(recorded, recorded_run, honest, case_changes={"passed": False}), incomplete),
            ("case passed by a truthy value", check(recorded, recorded_run, honest, case_changes={"passed": 1}),
             incomplete),
            ("observed differs from expected", check(recorded, recorded_run, honest,
                                                     case_changes={"observed": False}), incomplete),
            ("case names no evidence", check(recorded, recorded_run, honest, case_changes={"evidence_sha256": ""}),
             incomplete),
            ("unsupported proof schema", check(recorded, recorded_run, honest, proof_changes={"schema_version": 2}),
             "unsupported capability evidence schema"),
            # The retained evidence file itself.
            ("evidence path not listed", check(recorded, recorded_run, honest, proof_changes={"evidence_files": {}}),
             evidence_gone),
            ("evidence path relative", check(recorded, recorded_run, honest,
                                             evidence_files=lambda path: path.name), evidence_gone),
            ("evidence file swapped for one with other bytes",
             check(recorded, recorded_run, honest, evidence_files=swapped), evidence_gone),
            # What the evidence file must itself say. The first two rows write the file's bytes
            # directly: one is not JSON, one is JSON that is not an object. Both were recorded as
            # having no row because the helper above could not produce them (R27).
            ("evidence bytes are not JSON at all", check(recorded, recorded_run, honest,
                                                         evidence_raw=b"this is not json"), substantiate),
            ("evidence is JSON but not an object", check(recorded, recorded_run, honest,
                                                         evidence_raw=b"[1, 2]"), substantiate),
            ("evidence of another schema", check(recorded, recorded_run, honest,
                                                 evidence_changes={"schema_version": 2}), substantiate),
            ("evidence of another purpose", check(recorded, recorded_run, honest,
                                                  evidence_changes={"purpose": "verification"}), substantiate),
            ("evidence for another configuration", check(recorded, recorded_run, honest,
                                                         evidence_changes={"configuration_hash": "c" * 64}),
             substantiate),
            ("evidence from an older run", check(recorded, recorded_run, honest,
                                                 evidence_changes={"checked_at": "2000-01-01T00:00:00+00:00"}),
             substantiate),
            ("evidence from another executable", check(recorded, recorded_run, honest,
                                                       evidence_changes={"executable_sha256": "0" * 64}),
             substantiate),
            ("evidence from another harness", check(recorded, recorded_run, honest,
                                                    evidence_changes={"probe_harness_sha256": "other"}),
             substantiate),
            ("no harness identity on either side",
             check(recorded, recorded_run, honest, evidence_changes={"probe_harness_sha256": ""},
                   proof_changes={"probe_harness_sha256": ""}), substantiate),
            ("evidence naming other runtime files", check(recorded, recorded_run, honest,
                                                          evidence_changes={"runtime_files": {"a": "b"}}),
             substantiate),
            ("evidence case not passed", check(recorded, recorded_run, honest,
                                               evidence_changes=other_case(recorded_run, passed=False)),
             substantiate),
            ("evidence case with an empty measurement",
             check(recorded, recorded_run, honest, evidence_changes=other_case(recorded_run, measurement={})),
             substantiate),
            ("evidence case measurement not a mapping",
             check(recorded, recorded_run, honest, evidence_changes=other_case(recorded_run, measurement=[1])),
             substantiate),
            # Only `fresh_context` is malformed here. An earlier version replaced every case with a
            # fixture measurement, so the attestation case refused first and the row produced the
            # right message for the wrong reason: the coverage ledger measured that its mutant
            # changed no row's value, which is how the mistake surfaced.
            ("evidence case that is not a mapping",
             check(recorded, recorded_run, honest,
                   evidence_changes={"cases": other_case(recorded_run)["cases"] | {"fresh_context": "not a mapping"}}),
             substantiate),
            # The executable and what may sit beside it.
            # The evidence names the same executable as the proof, so the binding holds and the
            # file on disk is what fails: without moving both, an earlier binding answers first.
            ("executable changed after proof", check(recorded, recorded_run, honest,
                                                     proof_changes={"executable_sha256": "0" * 64},
                                                     evidence_changes={"executable_sha256": "0" * 64}),
             "provider executable changed after proof"),
            ("executable named by a relative path", check(recorded, recorded_run, honest,
                                                          argv=[Path(sys.executable).name, "--model", "{model}"]),
             "provider executable changed after proof"),
            ("provider carrying a runtime file", check(recorded, recorded_run, honest,
                                                       proof_changes={"runtime_files": {"a": "b"}},
                                                       evidence_changes={"runtime_files": {"a": "b"}}),
             "data-only provider must be one reviewed native executable"),
            # Since the launchable shape pins argv[1:] to exactly ["--model", "{model}"], a
            # provider can no longer carry a script argument at all, so the script-in-dependencies
            # check is unreachable for this purpose and the shape check is what refuses. The row
            # asserts the refusal that actually happens; the script check is owed to verification.
            ("argv naming a script the proof does not carry",
             check(recorded, recorded_run, honest, argv=[sys.executable, "helper.py"]),
             "provider capability is not in a launchable data-only shape"),
            # The launchable-shape class itself (R23-SPEC-04), one disjunct per row.
            # R24-SPEC-01 / R24-SEC-01: the vendor key is the member the first version could not
            # see, because it is not a field of the record. A missing key refuses, never excuses.
            ("filed under another vendor key", check(recorded, recorded_run, honest, name="second"),
             "only the codex vendor key may launch a reviewed data-only worker"),
            ("no vendor key named", check(recorded, recorded_run, honest, name=None),
             "only the codex vendor key may launch a reviewed data-only worker"),
            ("no subscription auth mode", check(recorded, recorded_run, honest,
                                                config_changes={"auth_mode": "api-key"}),
             "provider capability is not in a launchable data-only shape"),
            ("argv of the wrong length", check(recorded, recorded_run, honest,
                                               argv=[sys.executable, "--model"]),
             "provider capability is not in a launchable data-only shape"),
            ("argv that is not a list", check(recorded, recorded_run, honest, argv=None),
             "provider capability is not in a launchable data-only shape"),
            ("argv whose first element is not a string",
             check(recorded, recorded_run, honest, argv=[7, "--model", "{model}"]),
             "provider capability is not in a launchable data-only shape"),
            ("argv template changed", check(recorded, recorded_run, honest,
                                            argv=[sys.executable, "--model", "gpt-6-astra"]),
             "provider capability is not in a launchable data-only shape"),
            # R24-SEC-08: the first version named a file that does not exist, so its mutant died
            # of FileNotFoundError rather than by being admitted. This row names a real file with
            # the executable's exact bytes under a `.bat` name, so every other binding holds and
            # only the suffix disjunct can refuse it.
            ("executable is not an exe", check(recorded, recorded_run, honest,
                                               argv=lambda tmp: [str(Path(shutil.copyfile(
                                                   sys.executable, tmp / "worker.bat"))),
                                                   "--model", "{model}"]),
             "provider capability is not in a launchable data-only shape"),
        )
        gate_inventory.record_row_values("edges", {label: actual for label, actual, _ in expectations})
        for label, actual, expected in expectations:
            with self.subTest(label):
                self.assertEqual(actual, expected)

    def test_self_forged_receipt_does_not_grant_host_authority(self):
        cfg = docker_config() | {"proof": {"passed": True, "cases": {"all": True}}}
        with self.assertRaisesRegex(GateError, "trusted host"):
            validate_capability(cfg, "verification")

    def test_docker_route_preserves_fixed_test_argv(self):
        from project_creator.processes import Result
        cfg = {"kind": "docker"}
        with patch("project_creator.providers.validate_capability", return_value={}), \
                patch("project_creator.providers._verified_docker_class") as loader:
            sandbox = loader.return_value
            sandbox.return_value.run.return_value = Result(0, "verified", "", 0.1)
            expected = {"source.py": "a" * 64}
            result = VerificationRunner(cfg, {"unit": ["python", "tests.py"]}, read_set=["source.py"]).run(
                ["unit"], Path.cwd(), expected_files=expected)
            sandbox.return_value.run.assert_called_once_with(
                ["python", "tests.py"], Path.cwd(), "unit", expected_files=expected)
            self.assertEqual(result[0]["exit_code"], 0)

    def test_docker_class_executes_the_already_validated_runtime_bytes(self):
        package = Path(__file__).resolve().parents[1] / "project_creator"
        runtime = {str(package / name): (package / name).read_bytes()
                   for name in ("contracts.py", "processes.py", "docker_sandbox.py")}
        with patch.object(Path, "read_bytes", side_effect=AssertionError("disk reopened")):
            cls = _verified_docker_class(runtime)
        self.assertEqual(cls.__name__, "DockerSandbox")
        self.assertTrue(cls.__module__.startswith("project_creator._validated_docker_"))

    def test_docker_class_binds_validated_helper_bytes(self):
        package = Path(__file__).resolve().parents[1] / "project_creator"
        runtime = {str(package / name): (package / name).read_bytes()
                   for name in ("contracts.py", "processes.py", "docker_sandbox.py")}
        runtime[str(package / "contracts.py")] = runtime[str(package / "contracts.py")].replace(
            b'class GateError(Exception):', b'class BoundGateError(Exception):')
        with self.assertRaisesRegex(GateError, "contract helpers"):
            _verified_docker_class(runtime)


if __name__ == "__main__":
    unittest.main()
