"""Public command boundary. Status/doctor/help never launch workers or models."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .contracts import GateError, STAGES, digest
from .engine import Engine, start
from .mirror import GitHub, sync
from .models import load_config, refresh_catalog
from .providers import attestation_facts, validate_capability
from .admission import verify_package
from .processes import stable_directory
from .store import Store, atomic_text, exclusive
from .workspace import directory_identity, project_lock


def parser():
    p = argparse.ArgumentParser(prog="project-creator", description="Portable, gated project workflow")
    p.add_argument("--state", type=Path, help="private per-project state directory")
    sub = p.add_subparsers(dest="command", required=True)
    new = sub.add_parser("start")
    new.add_argument("--project", type=Path, required=True)
    new.add_argument("--config", type=Path, required=True)
    new.add_argument("--catalog", type=Path, required=True)
    new.add_argument("--vendor", required=True)
    new.add_argument("--spec-vendor")
    new.add_argument("--objective", required=True)
    new.add_argument("--standalone", choices=STAGES)
    new.add_argument("--background", action="store_true")
    # REQ-LC-021. Both tiers, because the first three stages run on the highest and the last
    # three on the second-highest. `--accept-catalog-pins` is the operator saying the shown
    # catalog proposal is their choice; it is never a default.
    new.add_argument("--model-highest", help="model id for grill-with-docs, to-spec, to-tickets")
    new.add_argument("--model-second", help="model id for implement, spec-review, defect-review")
    new.add_argument("--spec-model-highest", help="as --model-highest, for a different --spec-vendor")
    new.add_argument("--spec-model-second", help="as --model-second, for a different --spec-vendor")
    new.add_argument("--accept-catalog-pins", action="store_true",
                     help="accept the catalog's model proposal for every vendor this run uses")
    for command in ("status", "pause", "resume", "cancel", "answer", "review", "sync", "run"):
        cmd = sub.add_parser(command)
        cmd.add_argument("run_id")
        if command == "resume":
            cmd.add_argument("--vendor")
            cmd.add_argument("--background", action="store_true")
        if command == "answer":
            cmd.add_argument("text")
            cmd.add_argument("--background", action="store_true")
        if command == "review":
            cmd.add_argument("--spec-vendor")
        if command == "run":
            cmd.add_argument("--max-steps", type=int)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--config", type=Path)
    update = sub.add_parser("catalog-refresh")
    update.add_argument("--catalog", type=Path, required=True)
    update.add_argument("--vendor", required=True)
    update.add_argument("--output", type=Path, required=True)
    sub.add_parser("recover")
    return p


def provider_panel(adapter):
    """The gate's verdict and the measured facts, computed once for both readiness surfaces.

    REQ-LC-022 with R22-SEC-01's correction: the facts carry the verdict, so a panel printed
    beside a refusal cannot read as a clean bill of health. One owner, because `doctor` and
    `start` disagreeing about a provider's readiness would be worse than either being wrong.
    `AttributeError` is caught because a record that is not a mapping at all reaches the gate
    before anything has validated its shape.
    """
    try:
        validate_capability(adapter, "provider")
        return "proof-current", attestation_facts(adapter, verified=True)
    except GateError as exc:
        return str(exc), attestation_facts(adapter, verified=False)
    except (OSError, KeyError, TypeError, AttributeError):
        return "adapter unavailable", attestation_facts(adapter, verified=False)


def announce_providers(run_config, vendors, **extra):
    """Print the measured attestation facts before the first provider call of a session.

    REQ-LC-022 as corrected (R22-SPEC-04, R22-SEC-12): the first build printed this only at
    `start`, so a run resumed weeks later dispatched against a proof whose age the operator had
    last been shown when it was fresh. Every command that can dispatch prints it now.
    """
    providers = run_config.get("providers") if isinstance(run_config, dict) else None
    print(json.dumps(dict(extra, provider_attestation={
        name: provider_panel(adapter)[1] for name, adapter in (providers or {}).items()
        if not vendors or name in vendors}), ensure_ascii=False, indent=2))


def status(store, run):
    return {key: run.get(key) for key in ("id", "status", "stage", "branch", "worktree", "vendor", "spec_vendor", "questions", "last_error", "delivery_commit", "last_receipt", "mirror_status")} | {
        "models": run["pins"], "model_declaration": run.get("model_declaration"),
        "mirror": [dict(x) for x in store.db.execute("SELECT key,issue,status,error FROM outbox WHERE run_id=?", (run["id"],))]}


def background(state: Path, run_id: str):
    # A package must not verify itself only after imports execute. The trusted
    # host bootstrap replaces this boundary with its own verified restart path.
    raise GateError("background execution requires a trusted host bootstrap")


def main(argv=None):
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    args = parser().parse_args(argv)
    store = None
    try:
        if args.command == "catalog-refresh":
            catalog = refresh_catalog(load_config(args.catalog), args.vendor)
            atomic_text(args.output, json.dumps(catalog, ensure_ascii=False, indent=2))
            print(json.dumps({"status": "refreshed", "output": str(args.output)}))
            return 0
        if args.command == "doctor":
            checks = {}
            attestation = {}
            if args.config:
                cfg = load_config(args.config)
                checks["providers_configured"] = bool(cfg.get("providers"))
                for vendor, adapter in cfg.get("providers", {}).items():
                    # REQ-LC-022: the facts are reported even when the gate refuses, because the
                    # reason for a refusal is usually in these numbers, and they carry the verdict.
                    checks[vendor], attestation[vendor] = provider_panel(adapter)
                try:
                    validate_capability(cfg.get("verification_sandbox", {}), "verification")
                    checks["verification"] = "proof-current"
                except (GateError, OSError, KeyError) as exc:
                    checks["verification"] = str(exc) if isinstance(exc, GateError) else "sandbox unavailable"
                try:
                    verify_package(Path(__file__).resolve().parents[1], Path(cfg["admission_file"]))
                    checks["package_admission"] = "proof-current"
                except (GateError, OSError, KeyError) as exc:
                    checks["package_admission"] = str(exc) if isinstance(exc, GateError) else "admission unavailable"
            if args.state and (args.state / "ledger.sqlite3").is_file():
                store = Store(args.state, read_only=True)
                checks["ledger"] = store.verify()
            passed = bool(args.config) and all(x is True or x == "proof-current" for x in checks.values())
            print(json.dumps({"read_only": True, "status": "configured-gates-pass" if passed else "not-ready",
                              "checks": checks, "provider_attestation": attestation, "model_calls": 0}, ensure_ascii=False))
            return 0 if passed else 2
        if not args.state:
            raise GateError("--state is required; choose a private per-project directory")
        store = Store(args.state, create=args.command == "start", read_only=args.command == "status")
        if args.command == "start":
            run_config = load_config(args.config)
            declaration = {}
            if args.model_highest or args.model_second:
                declaration[args.vendor] = {"highest": args.model_highest, "second-highest": args.model_second}
            if args.spec_model_highest or args.spec_model_second:
                if not args.spec_vendor or args.spec_vendor == args.vendor:
                    raise GateError("--spec-model-* needs a --spec-vendor different from --vendor")
                declaration[args.spec_vendor] = {"highest": args.spec_model_highest,
                                                 "second-highest": args.spec_model_second}
            result = start(store, args.project, args.objective, run_config, load_config(args.catalog), args.vendor,
                           spec_vendor=args.spec_vendor, standalone=args.standalone,
                           models=declaration or None, accept_catalog=args.accept_catalog_pins)
            # REQ-LC-022: the provider's MEASURED attestation facts and the gate's verdict,
            # printed before the first provider call is made.
            announce_providers(run_config, result["model_declaration"]["vendors"],
                               run_id=result["id"], models=result["pins"],
                               model_declaration=result["model_declaration"])
            if args.background:
                background(args.state, result["id"])
            else:
                result = Engine(store).run(result["id"])
        elif args.command == "recover":
            launched = []
            for run in store.all():
                if run["status"] in {"running", "ready"}:
                    launched.append({"run_id": run["id"], "pid": background(args.state, run["id"])})
            print(json.dumps({"recovery_started": launched}))
            return 0
        elif args.command == "run":
            pending = store.get(args.run_id)
            announce_providers(pending["config"], (pending.get("model_declaration") or {}).get("vendors"),
                               run_id=args.run_id)
            result = Engine(store).run(args.run_id, max_steps=args.max_steps)
        else:
            result = store.get(args.run_id)
            if args.command == "status":
                pass
            elif args.command in {"pause", "cancel"}:
                if result["status"] == "completed":
                    raise GateError("completed run cannot be cancelled or paused")
                result["status"] = "paused" if args.command == "pause" else "cancelled"
                store.save(result, args.command, expected_revision=result["revision"])
            elif args.command == "review":
                report = Engine(store).review_once(args.run_id, spec_vendor=args.spec_vendor)
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 0 if report["review_passed"] else 2
            elif args.command in {"resume", "answer"}:
                project = Path(result["project"])
                git_dir = Path(result["git_dir"])
                lock = project_lock(git_dir)
                with stable_directory(project), stable_directory(git_dir), exclusive(lock):
                    if directory_identity(git_dir) != result.get("git_dir_identity"):
                        raise GateError("pinned Git metadata directory identity changed")
                    result = store.get(args.run_id)
                    if result["status"] == "cancelled":
                        raise GateError("cancelled run is terminal; start a new run")
                    if args.command == "answer":
                        if result["status"] != "waiting_for_answer":
                            raise GateError("run is not waiting for an answer")
                        result["answers"].append({"questions": result["questions"], "answer": args.text})
                        result["questions"] = []
                    if args.command == "resume":
                        if result["status"] in {"completed", "waiting_for_answer"}:
                            raise GateError("completed runs cannot resume; waiting questions require answer")
                        if args.vendor:
                            if args.vendor not in result["pins"]:
                                raise GateError("destination vendor was not pinned at run creation")
                            result["vendor"] = args.vendor
                    result["status"] = "ready"
                    store.save(result, args.command, expected_revision=result["revision"])
                if getattr(args, "background", False):
                    background(args.state, result["id"])
                else:
                    announce_providers(result["config"], (result.get("model_declaration") or {}).get("vendors"),
                                       run_id=result["id"])
                    result = Engine(store).run(result["id"])
            elif args.command == "sync":
                store.verify()
                config = result["config"].get("github")
                if not config:
                    raise GateError("GitHub mirror not configured")
                with exclusive(store.root / "github-sync.lock"):
                    rows = sync(store, GitHub(config["executable"], config["repository"], store.root), result["id"])
                result = store.get(args.run_id)
                result["mirror_status"] = "synced" if rows and all(x["status"] == "synced" for x in rows) else "pending"
                store.save(result, "mirror_status", expected_revision=result["revision"])
                print(json.dumps(status(store, result), ensure_ascii=False, indent=2))
                return 0 if result["mirror_status"] == "synced" else 2
        print(json.dumps(status(store, result), ensure_ascii=False, indent=2))
        return 0 if result["status"] not in {"paused", "review_failed", "cancelled"} else 2
    except (GateError, OSError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc) if isinstance(exc, GateError) else type(exc).__name__}, ensure_ascii=False))
        return 2
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
