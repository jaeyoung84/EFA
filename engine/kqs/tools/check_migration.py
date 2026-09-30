#!/usr/bin/env python3
"""Validate initial migration provenance without importing or executing the engine."""

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN = ("app", "korea_quant.paper_trading", "korea_quant.daily_picks_cli",
             "korea_quant.krx", "korea_quant.reversion_validation", "vectorbt",
             "boto3", "requests", "httpx")


def verify(root: Path = ROOT) -> dict:
    manifest = json.loads((root / "engine/kqs/migration_manifest.json").read_text())
    evidence = json.loads((root / "docs/engine/source_manifest.json").read_text())
    sources = {row.get("archive_path"): row for row in evidence["files"] if row.get("archive_path")}
    errors = []
    if manifest["source_commit"] != evidence["source"]["head"]:
        errors.append("Source commit does not match the captured evidence.")
    for entry in manifest["copied_modules"]:
        source = sources.get(entry["archive"])
        if source is None or source["source_sha256"] != entry["source_sha256"]:
            errors.append("Unknown original source/hash: " + entry["archive"])
            continue
        target = (root / entry["target"]).resolve()
        if not target.is_relative_to((root / "engine/kqs/src/korea_quant").resolve()):
            errors.append("Target escapes the migrated core: " + entry["target"])
        elif not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != entry["source_sha256"]:
            errors.append("Core differs from initial migration: " + entry["target"])
    installed = sorted(path.name for path in (root / "engine/kqs/src/korea_quant").glob("*.py"))
    expected = sorted(Path(row["target"]).name for row in manifest["copied_modules"])
    if installed != expected:
        errors.append("Unregistered modules in the core package.")
    for path in (root / "engine/kqs/src").glob("*/*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN):
                    errors.append(f"Forbidden runtime import: {path.name}: {name}")
    return {"integrity_ok": not errors, "copied_modules": len(manifest["copied_modules"]),
            "errors": errors, "scope": "initial migration bytes/import boundary; no engine execution"}


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["integrity_ok"] else 1)
