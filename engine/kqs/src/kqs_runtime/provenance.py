"""Separate source, executable-code and dependency identities."""

import hashlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

from kqs_runtime import SOURCE_COMMIT, __version__


def digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def code_identity() -> dict:
    root = Path(__file__).resolve().parents[1]
    for name in ("korea_quant", "korea_quant.strategy", "korea_quant.eligibility",
                 "korea_quant.reversion_scanner", "kqs_publication", "kqs_publication.contract",
                 "kqs_publication.publisher", "kqs_publication.store", "kqs_publication.calendar",
                 "kqs_publication.writer", "kqs_publication.attempts",
                 "kqs_collection", "kqs_collection.quality", "kqs_collection.store",
                 "kqs_collection.krx", "kqs_collection.coverage", "kqs_ops"):
        module = sys.modules.get(name)
        if module is not None and Path(module.__file__).resolve().parent != root / name.split(".")[0]:
            raise ValueError("Conflicting legacy korea_quant installation; use a separate environment.")
    files = {}
    for package in ("korea_quant", "kqs_runtime", "kqs_publication", "kqs_collection", "kqs_ops"):
        for path in sorted((root / package).glob("*.py")):
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted((root / "kqs_publication/calendars").glob("*.json")):
        files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "package_version": __version__, "source_commit": SOURCE_COMMIT,
        "code_sha256": digest(files), "component_sha256": files,
        "python": platform.python_version(),
        "dependencies": {name: importlib.metadata.version(name) for name in
                         ("pandas", "numpy", "python-dateutil", "pytz", "tzdata", "six")},
    }
