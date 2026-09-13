"""Hash-pinned package admission. Audit approval is supplied by the host owner."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .contracts import GateError, digest

CORE_SKILLS = ("grill-with-docs", "to-spec", "to-tickets", "implement", "spec-review")


def verify_package(package: Path, admission_file: Path, *, stage=None):
    manifest_path = package / "release-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        admission = json.loads(admission_file.read_text(encoding="utf-8"))
        if admission.get("status") != "admitted" or admission.get("manifest_sha256") != digest(manifest_path.read_bytes()):
            raise GateError("package not admitted at this exact manifest hash")
        verified = datetime.fromisoformat(admission["reviewed_at"])
        age = datetime.now(timezone.utc) - verified
        if verified.tzinfo is None or age < timedelta(0) or age > timedelta(days=30):
            raise GateError("package audit expired; monthly rescan required")
        if admission.get("revocation_tested") is not True or admission.get("disableSkillShellExecution") is not True:
            raise GateError("host admission safeguards incomplete")
        if set(manifest["skills"]) != set(CORE_SKILLS):
            raise GateError("package must contain exactly five core skills")
        for name, expected in manifest["files"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
                raise GateError("invalid release manifest path")
            path = package / relative
            if not path.resolve().is_relative_to(package.resolve()) or path.is_symlink():
                raise GateError("release file escaped package")
            if digest(path.read_bytes()) != expected:
                raise GateError("package file changed after audit")
        # The executable instructions and all importable controller files must
        # be admitted, not only a selected skill document.
        required = {"project-creator.py", "references/defect-review.md", "references/engineering-method.md"}
        required |= {f"skills/{name}/SKILL.md" for name in CORE_SKILLS}
        required |= {x.relative_to(package).as_posix() for x in (package / "project_creator").glob("*.py")}
        if not required <= set(manifest["files"]):
            raise GateError("release manifest omitted runtime instructions")
        checked_stage = "spec-review" if stage == "defect-review" else stage
        if checked_stage and admission.get("skills", {}).get(checked_stage) != "enabled":
            raise GateError("skill revoked or not admitted")
        return {"manifest_sha256": digest(manifest_path.read_bytes()), "reviewed_at": admission["reviewed_at"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GateError("package admission is absent or malformed") from exc


def admission_stopped(config):
    path = config.get("admission_file")
    if not path:
        return False  # The entry gate refuses absence before any provider call.
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        skills = data.get("skills")
        return data.get("status") != "admitted" or not isinstance(skills, dict) or set(skills) != set(CORE_SKILLS) or any(v != "enabled" for v in skills.values())
    except (OSError, ValueError, AttributeError):
        return True
