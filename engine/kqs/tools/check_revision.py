#!/usr/bin/env python3
"""Verify the explicitly selected successor manifest without importing the engine."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = "engine/kqs/revisions/EINT-016.json"


def verify(root: Path = ROOT, manifest_path: str = DEFAULT_MANIFEST) -> dict:
    errors = []
    try:
        manifest = json.loads((root / manifest_path).read_text())
        code = manifest["code"]
        src = root / "engine/kqs/src"
        paths = list(src.rglob("*.py")) + list((src / "kqs_publication/calendars").glob("*.json"))
        actual = {path.relative_to(src).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted(paths)}
        if actual != code["component_sha256"]:
            errors.append("Executable components differ from the selected revision.")
        digest = hashlib.sha256(json.dumps(actual, sort_keys=True, ensure_ascii=False,
                                           separators=(",", ":")).encode()).hexdigest()
        if digest != code["code_sha256"]:
            errors.append("Executable code digest differs from the selected revision.")
        for field in ("initial_manifest", "dependency_lock", "package_metadata"):
            entry = manifest[field]
            path = (root / entry["path"]).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                errors.append("Missing or escaped revision reference: " + field)
            elif hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                errors.append("Revision reference changed: " + field)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append("Unreadable revision: " + str(exc))
    return {"integrity_ok": not errors, "manifest": manifest_path, "errors": errors,
            "scope": "registered executable bytes/metadata only; no research or engine execution"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    result = verify(manifest_path=args.manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["integrity_ok"] else 1)
