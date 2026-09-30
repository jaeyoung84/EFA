"""Evidence-tool tests, isolated from backend fixtures/network/engine/market DB."""
import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "check_engine_evidence.py"
SPEC = importlib.util.spec_from_file_location("check_engine_evidence", SCRIPT)
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.relative = "docs/engine/snapshots/test/DECISIONS.md.txt"
        self.path = self.root / self.relative
        self.path.parent.mkdir(parents=True)
        self.content = "# Decisions\n\n## D-1. Fixed rule\n\nHistoric evidence.\n"
        self.path.write_text(self.content, encoding="utf-8")
        self.entry = {"source_path": "/legacy/DECISIONS.md", "availability": "present",
                      "archive_path": self.relative, "archive_sha256": evidence.sha256(self.content.encode()),
                      "source_sha256": evidence.sha256(self.content.encode()),
                      "size_bytes": len(self.content.encode()), "bytes_preserved": True}
        self.manifest = {"schema": "kqs-evidence-manifest/v1", "source": {"head": "a" * 40},
                         "files": [self.entry]}
        section = evidence.decision_sections(self.content)[0]
        self.registry = {"schema": "kqs-evidence-registry/v1", "source_commit": "a" * 40,
                         "decision_archive": self.relative,
                         "decisions": [{key: value for key, value in section.items() if key != "body"}],
                         "reuse_rules": [{"decision_refs": ["D-1"]}], "datasets": []}

    def check(self):
        return evidence.validate_evidence(self.root, self.manifest, self.registry)

    def test_pristine(self):
        result = self.check()
        self.assertTrue(result["integrity_ok"])
        self.assertEqual(result["decision_count"], 1)
        self.assertFalse(result["historical_results_reexecuted"])

    def test_modified_archive(self):
        self.path.write_text(self.content + "Changed\n", encoding="utf-8")
        self.assertFalse(self.check()["integrity_ok"])

    def test_missing_archive(self):
        self.path.unlink()
        self.assertFalse(self.check()["integrity_ok"])

    def test_metadata_not_backup(self):
        self.manifest["files"].append({"source_path": "/legacy/results.csv", "availability": "present"})
        result = self.check()
        self.assertTrue(result["integrity_ok"])
        self.assertFalse(result["full_backup_complete"])

    def test_missing_original_reported(self):
        self.manifest["files"].append({"source_path": "/legacy/missing.csv", "availability": "missing"})
        self.assertEqual(self.check()["missing_source_files_at_capture"], 1)
        self.assertFalse(self.check()["full_backup_complete"])

    def test_wrong_section_hash(self):
        self.registry["decisions"][0]["section_sha256"] = "0" * 64
        self.assertFalse(self.check()["integrity_ok"])

    def test_duplicate_decision(self):
        self.registry["decisions"].append(copy.deepcopy(self.registry["decisions"][0]))
        self.assertFalse(self.check()["integrity_ok"])

    def test_wrong_source_version(self):
        self.registry["source_commit"] = "b" * 40
        self.assertFalse(self.check()["integrity_ok"])

    def test_unknown_reference(self):
        self.registry["reuse_rules"][0]["decision_refs"] = ["D-999"]
        self.assertFalse(self.check()["integrity_ok"])

    def test_path_escape(self):
        self.entry["archive_path"] = "../outside.txt"
        self.assertTrue(any("escapes repository" in error for error in self.check()["errors"]))

    def test_normalized_final_newline(self):
        original = self.content.encode()[:-1]
        self.entry.update(source_sha256=evidence.sha256(original), size_bytes=len(original),
                          bytes_preserved=False, normalization="append-final-LF-if-missing")
        self.assertTrue(self.check()["integrity_ok"])

    def test_empty_archive(self):
        self.manifest["files"] = []
        self.assertFalse(self.check()["integrity_ok"])

    def test_mixed_original_line_endings(self):
        original = self.content.replace("# Decisions\n", "# Decisions\r\n", 1).encode()
        self.entry.update(source_sha256=evidence.sha256(original), size_bytes=len(original),
                          bytes_preserved=False, normalization="restore-original-crlf",
                          original_crlf_line_indices=[0])
        self.assertTrue(self.check()["integrity_ok"])

    def test_invalid_line_ending_map(self):
        self.entry.update(bytes_preserved=False, normalization="restore-original-crlf",
                          original_crlf_line_indices=[999])
        self.assertFalse(self.check()["integrity_ok"])

    def test_false_original_hash_claim(self):
        self.manifest["declared_raw_input_hash_checks"] = [
            {"name": "data.csv", "expected_sha256": "a", "actual_sha256": "b", "matches": True}]
        self.assertFalse(self.check()["integrity_ok"])


if __name__ == "__main__":
    unittest.main()
