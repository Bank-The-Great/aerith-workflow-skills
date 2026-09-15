"""Parser/adapter unit fixtures, not substitutes for live containment probes."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_creator.contracts import GateError, digest
from project_creator.providers import (parse_claude_stream, provider_environment, VerificationRunner,
                                       CLIProvider, validate_capability, _verified_docker_class)
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
        def validate(cfg, purpose, *, environment):
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
                with self.assertRaisesRegex(GateError, "one reviewed native executable"):
                    provider.invoke("implement", "chosen", packet, Path.cwd())
                self.assertEqual(launched, [])

    def test_recorded_mode_capability_states_exact_limits(self):
        from project_creator import providers
        for limits in ({"answering_model_proven": False, "echo_tested": False},
                       {"answering_model_proven": False, "echo_tested": True}):
            self.assertEqual(providers._recorded_mode_limits({"limits": limits}), limits)
        for limits in (None, {}, {"answering_model_proven": False},
                       {"answering_model_proven": True, "echo_tested": False},
                       {"answering_model_proven": 0, "echo_tested": False},
                       {"answering_model_proven": False, "echo_tested": 1},
                       {"answering_model_proven": False, "echo_tested": False, "model": "chosen"}):
            with self.subTest(limits=limits), self.assertRaisesRegex(GateError, "must state its limits"):
                providers._recorded_mode_limits({} if limits is None else {"limits": limits})

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
            cfg = {"argv": [sys.executable], "output": "codex-data-only",
                   "filesystem_scope": "codex-home-auth-only",
                   "loader_policy": "pe-dependent-load-system32",
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
                    validate_capability(cfg, "provider", environment=provider_environment(cfg))

    def test_capability_binds_its_attestation_mode_to_the_measured_attestation(self):
        cases = ("fresh_context", "tools_disabled", "ambient_not_observed_in_output", "child_cleanup",
                 "model_attestation", "subscription_auth_only",
                 "dependent_load_flags_system32", "delay_imports_absent", "imports_allowlisted")
        recorded_run = {"evidenced_positive_calls": 4, "provider_response_header_calls": 0,
                        "recorded_response_model_calls": 4, "recorded_tier_withdrawn": False,
                        "unserved_echo_observable": False}
        header_run = recorded_run | {"provider_response_header_calls": 4, "recorded_response_model_calls": 0}
        honest = {"answering_model_proven": False, "echo_tested": False}

        def check(attestation, measurement, limits=None, evidence_changes=None, proof_changes=None):
            with tempfile.TemporaryDirectory() as tmp:
                evidence = Path(tmp) / "evidence.json"
                cfg = {"argv": [sys.executable], "output": "codex-data-only",
                       "filesystem_scope": "codex-home-auth-only",
                       "loader_policy": "pe-dependent-load-system32"}
                if attestation is not None:
                    cfg["attestation"] = attestation
                current = datetime.now(timezone.utc).isoformat()
                executable = digest(Path(sys.executable).read_bytes())
                record = {"schema_version": 1, "purpose": "provider", "configuration_hash": digest(cfg),
                          "probe_harness_sha256": "fixture", "checked_at": current,
                          "executable_sha256": executable, "runtime_files": {},
                          "cases": {case: {"passed": True, "measurement": (
                              measurement if case == "model_attestation" else {"fixture": True})}
                              for case in cases}}
                record |= evidence_changes or {}
                evidence.write_text(json.dumps(record))
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
                cfg["proof"] |= proof_changes or {}
                with patch.dict("project_creator.providers._HOST_CAPABILITIES", {digest(cfg): "provider"}):
                    try:
                        validate_capability(cfg, "provider", environment=provider_environment(cfg))
                        return "admitted"
                    except GateError as exc:
                        return str(exc)

        recorded = {"mode": "recorded-response-model"}
        header = {"mode": "provider-response-header"}
        substantiate = "retained evidence does not substantiate the claimed capability"
        expectations = (
            ("recorded mode, honest limits", check(recorded, recorded_run, honest), "admitted"),
            ("recorded mode, header run", check(recorded, header_run, honest), "admitted"),
            ("recorded mode, echo flipped", check(recorded, recorded_run, honest | {"echo_tested": True}), substantiate),
            ("recorded mode, tier withdrawn", check(recorded, recorded_run | {"recorded_tier_withdrawn": True}, honest),
             substantiate),
            ("recorded mode, proof overstated", check(recorded, recorded_run, honest | {"answering_model_proven": True}),
             "recorded-model capability must state its limits"),
            ("header mode, header run", check(header, header_run), "admitted"),
            ("header mode, recorded run", check(header, recorded_run), substantiate),
            ("header mode, one call without a header", check(header, header_run | {"provider_response_header_calls": 3}),
             substantiate),
            ("header mode, a recorded call counted", check(header, header_run | {"recorded_response_model_calls": 1}),
             substantiate),
            ("header mode, no evidenced call", check(header, header_run | {"evidenced_positive_calls": 0,
                                                                           "provider_response_header_calls": 0}),
             substantiate),
            ("header mode, tier withdrawn", check(header, header_run | {"recorded_tier_withdrawn": True}), substantiate),
            ("no attestation", check(None, header_run), "provider capability names no admitted attestation mode"),
            ("header mode, recorded-mode limits stated", check(header, header_run, honest),
             "header-mode capability must not state recorded-mode limits"),
            # Each binding between the evidence and the proof, changed alone against evidence the
            # attestation check admits, so no other guard can answer for it.
            ("evidence from an older run", check(header, header_run,
                                                 evidence_changes={"checked_at": "2000-01-01T00:00:00+00:00"}),
             substantiate),
            ("evidence from another executable", check(header, header_run,
                                                       evidence_changes={"executable_sha256": "0" * 64}),
             substantiate),
            ("evidence for another configuration", check(recorded, recorded_run, honest,
                                                         evidence_changes={"configuration_hash": "c" * 64}),
             substantiate),
            ("proof for another configuration", check(recorded, recorded_run, honest,
                                                      proof_changes={"configuration_hash": "c" * 64}),
             "provider configuration has no matching containment proof"),
        )
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
