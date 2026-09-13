"""Explicit public allowlist packager; never traverses private host roots."""
import argparse
import json
import re
from pathlib import Path

from project_creator.admission import CORE_SKILLS
from project_creator.contracts import GateError, digest, safe_text
from project_creator.store import atomic_text


def build(package: Path, export: Path | None = None):
    files = json.loads((package / "PUBLIC_FILES.json").read_text(encoding="utf-8"))
    if not isinstance(files, list) or len(files) != len(set(files)):
        raise GateError("invalid positive public-file manifest")
    content = {}
    for name in files:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise GateError("invalid public path")
        path = package / relative
        if path.is_symlink() or not path.resolve().is_relative_to(package.resolve()):
            raise GateError("public source link refused")
        raw = path.read_bytes()
        text = safe_text(raw.decode("utf-8"))
        # Additional host-specific leak indicators. This is a tripwire, not a
        # claim to classify all confidential information automatically.
        if re.search(r"C:[/\\]Users[/\\]|AERITH[-]Memory|system[/\\]aerith-core|sk-proj-[A-Za-z0-9]{16,}", text):
            raise GateError("host-private marker in publication allowlist")
        content[name] = raw
    manifest = {"format": 1, "upstream": "mattpocock/skills",
                "upstream_commit": "3cca18b368ae95cdbdebbff572ccafa662551015",
                "skills": list(CORE_SKILLS), "files": {n: digest(raw) for n, raw in sorted(content.items())}}
    atomic_text(package / "release-manifest.json", json.dumps(manifest, indent=2) + "\n")
    if export:
        if export.exists():
            raise GateError("export destination must be new; no overwrite")
        export.mkdir(parents=True)
        for name, raw in content.items():
            destination = export / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(raw)
        atomic_text(export / "release-manifest.json", json.dumps(manifest, indent=2) + "\n")
    return {"files": len(content), "manifest_sha256": digest((package / "release-manifest.json").read_bytes())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parent, args.export)))
