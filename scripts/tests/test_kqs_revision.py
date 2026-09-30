"""Successor evidence guard tests; no strategy execution or backend fixtures."""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("kqs_revision_check", ROOT / "engine/kqs/tools/check_revision.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        manifest = json.loads((ROOT / CHECK.DEFAULT_MANIFEST).read_text())
        paths = [CHECK.DEFAULT_MANIFEST]
        paths += ["engine/kqs/src/" + path for path in manifest["code"]["component_sha256"]]
        paths += [manifest[key]["path"] for key in ("initial_manifest", "dependency_lock", "package_metadata")]
        for path in paths:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, target)

    def test_current_registered_revision_is_valid(self):
        self.assertTrue(CHECK.verify(self.root)["integrity_ok"])

    def test_changed_code_fails_closed(self):
        path = self.root / "engine/kqs/src/kqs_publication/store.py"
        path.write_bytes(path.read_bytes() + b"\n# synthetic tamper\n")
        self.assertFalse(CHECK.verify(self.root)["integrity_ok"])

    def test_new_unregistered_module_fails_closed(self):
        (self.root / "engine/kqs/src/kqs_publication/extra.py").write_bytes(b"# synthetic\n")
        self.assertFalse(CHECK.verify(self.root)["integrity_ok"])

    def test_calendar_data_change_fails_closed(self):
        path = self.root / "engine/kqs/src/kqs_publication/calendars/krx_equity_2026.json"
        path.write_bytes(path.read_bytes() + b"\n")
        self.assertFalse(CHECK.verify(self.root)["integrity_ok"])

    def test_initial_manifest_cannot_be_silently_rewritten(self):
        path = self.root / "engine/kqs/migration_manifest.json"
        path.write_bytes(path.read_bytes() + b"\n")
        self.assertFalse(CHECK.verify(self.root)["integrity_ok"])

    def test_changed_dependencies_or_package_metadata_fail_closed(self):
        for relative in ("engine/kqs/requirements.lock", "engine/kqs/pyproject.toml"):
            path = self.root / relative
            original = path.read_bytes()
            path.write_bytes(original + b"\n# synthetic change\n")
            self.assertFalse(CHECK.verify(self.root)["integrity_ok"])
            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
