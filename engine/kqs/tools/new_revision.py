#!/usr/bin/env python3
"""Write the next EINT successor manifest and verification record for the current engine bytes.

Used by the yearly calendar job (EINT-030) and by hand. It never overwrites an existing manifest,
reads the previous one from check_revision.DEFAULT_MANIFEST, and switches the default check to the
new record. It runs no research, engine or market code.
"""

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CHECK = ROOT / "engine/kqs/tools/check_revision.py"


def _sha(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def next_change_id() -> str:
    """One past the highest EINT number in revision files and in CHANGES.md headings (web-only EINTs have no manifest)."""
    numbers = [int(m.group(1)) for p in (ROOT / "engine/kqs/revisions").glob("EINT-*.json")
               if (m := re.match(r"EINT-(\d+)", p.stem))]
    numbers += [int(n) for n in re.findall(r"^## EINT-(\d+)", (ROOT / "docs/engine/CHANGES.md").read_text(), re.M)]
    return f"EINT-{max(numbers) + 1:03d}"


def current_default() -> str:
    match = re.search(r'DEFAULT_MANIFEST = "([^"]+)"', CHECK.read_text())
    if not match:
        raise SystemExit("check_revision.py DEFAULT_MANIFEST not found")
    return match.group(1)


def write(change_id: str, *, summary: str, scope: str, reason: str, tests: str) -> dict:
    manifest_path = f"engine/kqs/revisions/{change_id}.json"
    verification_path = f"docs/engine/verification/{change_id}.json"
    if (ROOT / manifest_path).exists() or (ROOT / verification_path).exists():
        raise SystemExit(f"{change_id} already exists; manifests are never overwritten")
    previous_path = current_default()
    previous = json.loads((ROOT / previous_path).read_text())
    src = ROOT / "engine/kqs/src"
    paths = list(src.rglob("*.py")) + list((src / "kqs_publication/calendars").glob("*.json"))
    components = {p.relative_to(src).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    digest = hashlib.sha256(json.dumps(components, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":")).encode()).hexdigest()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    now = datetime.now(timezone.utc).isoformat()
    changed = sorted(k for k in set(components) | set(previous["code"]["component_sha256"])
                     if previous["code"]["component_sha256"].get(k) != components.get(k))
    calendars = sorted((src / "kqs_publication/calendars").glob("krx_equity_*.json"))
    code = {**previous["code"], "source_commit": head, "code_sha256": digest, "component_sha256": components}
    previous_verification = previous.get("verification") or f"docs/engine/verification/{previous['change_id']}.json"
    manifest = {
        **previous, "change_id": change_id, "created_at": now, "supersedes": previous["change_id"],
        "previous_revision": {"path": previous_path, "sha256": _sha(previous_path)},
        "previous_verification": {"path": previous_verification, "sha256": _sha(previous_verification)},
        "dependency_lock": {"path": "engine/kqs/requirements.lock", "sha256": _sha("engine/kqs/requirements.lock")},
        "package_metadata": {"path": "engine/kqs/pyproject.toml", "sha256": _sha("engine/kqs/pyproject.toml")},
        "code": code,
        "calendar": {"calendar_id": [p.stem.replace("krx_equity_", "krx-equity-") for p in calendars],
                     "path": [p.relative_to(ROOT).as_posix() for p in calendars],
                     "sha256": [hashlib.sha256(p.read_bytes()).hexdigest() for p in calendars]},
        "changes": [summary, "changed components: " + ", ".join(changed)],
        "scope": scope, "verification": verification_path,
    }
    verification = {
        "change_id": change_id, "verified_at": now, "package": f"iv-kqs-engine {code['package_version']}", "code": code,
        "successor_manifest": manifest_path, "reason": reason, "tests": tests, "changed_components": changed,
        "deployment_performed": False, "orders_or_automatic_fills_created": False,
    }
    (ROOT / manifest_path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (ROOT / verification_path).write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n")
    CHECK.write_text(CHECK.read_text().replace(f'DEFAULT_MANIFEST = "{previous_path}"',
                                               f'DEFAULT_MANIFEST = "{manifest_path}"'))
    return {"change_id": change_id, "manifest": manifest_path, "verification": verification_path,
            "supersedes": previous["change_id"], "changed_components": changed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--change-id", help="default: next EINT number")
    parser.add_argument("--summary", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--tests", default="pending: CI (kqs-runtime workflow) on the pull request")
    args = parser.parse_args()
    print(json.dumps(write(args.change_id or next_change_id(), summary=args.summary, scope=args.scope,
                           reason=args.reason, tests=args.tests), ensure_ascii=False, indent=2))
