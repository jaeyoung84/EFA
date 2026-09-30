#!/usr/bin/env python3
"""Verify archived evidence only; never import/run the engine or access market DBs."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


HEADING = re.compile(r"^## ([DK]-\d+)\.\s*(.*)$", re.MULTILINE)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decision_sections(text: str) -> list[dict]:
    matches = list(HEADING.finditer(text))
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.start():end]
        sections.append({"id": match.group(1), "title": match.group(2),
                         "line": text.count("\n", 0, match.start()) + 1,
                         "section_sha256": sha256(body.encode("utf-8")), "body": body})
    return sections


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_evidence(root: Path, manifest: dict, registry: dict) -> dict:
    """Check bytes/references, not the truth of historical test/performance claims."""
    errors: list[str] = []
    if manifest.get("schema") != "kqs-evidence-manifest/v1":
        errors.append("unsupported manifest schema")
    if registry.get("schema") != "kqs-evidence-registry/v1":
        errors.append("unsupported registry schema")
    if registry.get("source_commit") != manifest.get("source", {}).get("head"):
        errors.append("registry/source commit mismatch")
    archived = metadata_only = missing = 0
    archive_paths: set[str] = set()
    source_paths: set[str] = set()
    base = root.resolve()
    for entry in manifest.get("files", []):
        source = entry.get("source_path", "")
        if not source or source in source_paths:
            errors.append(f"missing/duplicate source path: {source}")
        source_paths.add(source)
        missing += entry.get("availability") != "present"
        relative = entry.get("archive_path")
        if not relative:
            metadata_only += 1
            continue
        archived += 1
        if relative in archive_paths:
            errors.append(f"duplicate archive path: {relative}")
        archive_paths.add(relative)
        path = (base / relative).resolve()
        if Path(relative).is_absolute() or not path.is_relative_to(base):
            errors.append(f"archive path escapes repository: {relative}")
            continue
        if not path.is_file():
            errors.append(f"missing archive: {relative}")
            continue
        content = path.read_bytes()
        if sha256(content) != entry.get("archive_sha256"):
            errors.append(f"archive hash mismatch: {relative}")
        original = content
        if not entry.get("bytes_preserved", True):
            normalization = entry.get("normalization")
            if normalization == "restore-original-crlf":
                parts = content.split(b"\n")
                indices = entry.get("original_crlf_line_indices", [])
                if (not indices or any(type(i) is not int or i < 0 or i >= len(parts) - 1 for i in indices)
                        or len(indices) != len(set(indices)) or b"\r\n" in content):
                    errors.append(f"invalid original CRLF map: {relative}")
                    continue
                original = b"\n".join(part + (b"\r" if i in indices else b"")
                                      for i, part in enumerate(parts))
            elif normalization == "append-final-LF-if-missing" and content.endswith(b"\n"):
                original = content[:-1]
            else:
                errors.append(f"unsupported normalization: {relative}")
                continue
        if sha256(original) != entry.get("source_sha256") or len(original) != entry.get("size_bytes"):
            errors.append(f"original bytes mismatch: {relative}")
    if not archived:
        errors.append("no archived evidence")
    document = registry.get("decision_archive", "")
    sections = []
    if document not in archive_paths:
        errors.append("decision archive is not in manifest")
    else:
        path = (base / document).resolve()
        if path.is_relative_to(base) and path.is_file():
            sections = decision_sections(path.read_text(encoding="utf-8"))
    source_sections = {section["id"]: section for section in sections}
    decisions = registry.get("decisions", [])
    ids = [decision.get("id") for decision in decisions]
    if len(ids) != len(set(ids)):
        errors.append("duplicate decision ID")
    if set(ids) != set(source_sections):
        errors.append("decision index does not cover the archived document")
    for decision in decisions:
        section = source_sections.get(decision.get("id"))
        if not section:
            continue
        for field in ("title", "line", "section_sha256"):
            if decision.get(field) != section[field]:
                errors.append(f"decision {field} mismatch: {decision.get('id')}")
        for later in decision.get("later_decisions", []):
            if later not in source_sections:
                errors.append(f"unknown later decision: {later}")
        extra_source = decision.get("latest_rule_archive")
        if extra_source and extra_source not in archive_paths:
            errors.append(f"latest rule archive missing: {extra_source}")
    for rule in registry.get("reuse_rules", []) + registry.get("datasets", []):
        for reference in rule.get("decision_refs", []):
            if reference not in source_sections:
                errors.append(f"unknown decision reference: {reference}")
    declared_checks = manifest.get("declared_raw_input_hash_checks", [])
    for check in declared_checks:
        originals = [entry for entry in manifest.get("files", [])
                     if entry.get("category") == "declared_holdout_raw_input_metadata"
                     and entry.get("source_path", "").endswith("/" + check.get("name", ""))]
        if (len(originals) != 1 or not check.get("matches")
                or check.get("expected_sha256") != check.get("actual_sha256")
                or originals[0].get("source_sha256") != check.get("actual_sha256")):
            errors.append(f"declared raw input hash mismatch: {check.get('name')}")
    return {"integrity_ok": not errors, "errors": errors, "archived_files": archived,
            "declared_original_hash_matches_at_capture": len(declared_checks),
            "metadata_only_files": metadata_only, "missing_source_files_at_capture": missing,
            "decision_count": len(decisions), "full_backup_complete": metadata_only == 0 and missing == 0,
            "scope": "archive bytes and references only; no engine/backtest execution",
            "historical_results_reexecuted": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--topic", help="Find decisions by topic; read latest_rules before historical text.")
    parser.add_argument("--require-full-backup", action="store_true",
                        help="Fail if artifacts are missing or inventoried but not copied.")
    args = parser.parse_args(argv)
    try:
        manifest = read_json(args.root / "docs/engine/source_manifest.json")
        registry = read_json(args.root / "docs/engine/evidence_registry.json")
        result = validate_evidence(args.root, manifest, registry)
        if args.topic and result["integrity_ok"]:
            sections = decision_sections((args.root / registry["decision_archive"]).read_text(encoding="utf-8"))
            bodies = {section["id"]: section["body"] for section in sections}
            needle = args.topic.casefold()
            result["matches"] = [decision for decision in registry["decisions"]
                                 if needle in (json.dumps(decision, ensure_ascii=False)
                                               + bodies[decision["id"]]).casefold()]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["integrity_ok"] and (not args.require_full_backup or result["full_backup_complete"]) else 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"evidence check failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
