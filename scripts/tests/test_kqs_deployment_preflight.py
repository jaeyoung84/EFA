"""Preflight shell regressions; temporary files and fake Docker only, never AWS."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if len(args) != 4 or args[:2] != ["inspect", "--format"]:
    sys.exit(91)
template, container = args[2:]
overrides = json.loads(os.environ.get("FAKE_DOCKER_OVERRIDES", "{}"))
if "Labels" in template:
    value = overrides.get(container + ":project", "investing-app")
elif ".Source" in template:
    value = overrides.get("source", os.environ["KQS_STATE_DIR"])
elif ".RW" in template:
    value = overrides.get("rw", "false")
elif ".Name" in template:
    suffix = {"investment_mongo": "investment_mongo_data",
              "investment_qdrant": "investment_qdrant_data",
              "investment_meilisearch": "investment_meilisearch_data"}[container]
    value = overrides.get(container + ":volume", "investing-app_" + suffix)
else:
    sys.exit(92)
print(value)
'''


class DeploymentPreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "deployment"
        self.state = self.root / "state/kqs"
        self.state.mkdir(parents=True)
        self.environment_file = self.root / "app/backend/.env"
        self.environment_file.parent.mkdir(parents=True)
        self.environment_file.write_text("# synthetic, no secrets\n")
        (self.state / "recommendations.sqlite3").write_bytes(b"synthetic fixture")
        self.script = Path(self.directory.name) / "preflight.sh"
        # Only the test copy is relocated; the production script keeps its strict absolute root.
        self.script.write_text((ROOT / "infra/ops/kqs-deployment-preflight.sh").read_text()
                               .replace("/opt/investing-app", str(self.root)))
        self.binary_dir = Path(self.directory.name) / "bin"
        self.binary_dir.mkdir()
        docker = self.binary_dir / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o700)
        self.log = Path(self.directory.name) / "docker-calls.jsonl"

    def run_check(self, overrides=None, root=None):
        environment = {**os.environ, "PATH": str(self.binary_dir) + os.pathsep + os.environ["PATH"],
                       "KQS_STATE_DIR": str(self.state), "FAKE_DOCKER_LOG": str(self.log),
                       "FAKE_DOCKER_OVERRIDES": json.dumps(overrides or {})}
        return subprocess.run(["bash", str(self.script), str(root or self.root)],
                              env=environment, capture_output=True, text=True)

    def test_valid_existing_project_uses_inspect_only(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "investing-app\n")
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(calls), 9)
        self.assertTrue(all(call[:2] == ["inspect", "--format"] for call in calls))

    def test_missing_environment_or_database_stops_before_docker(self):
        for path in (self.environment_file, self.state / "recommendations.sqlite3"):
            with self.subTest(path=path.name):
                content = path.read_bytes()
                path.unlink()
                result = self.run_check()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())
                path.write_bytes(content)

    def test_symlink_state_stops_before_docker(self):
        real_state = self.root / "real-state"
        self.state.rename(real_state)
        self.state.symlink_to(real_state, target_is_directory=True)
        self.assertNotEqual(self.run_check().returncode, 0)
        self.assertFalse(self.log.exists())

    def test_wrong_deployment_root_stops_before_docker(self):
        self.assertNotEqual(self.run_check(root=self.root / "other").returncode, 0)
        self.assertFalse(self.log.exists())

    def test_wrong_or_writable_state_mount_is_rejected(self):
        for overrides in ({"source": "/unexpected/state"}, {"rw": "true"}):
            with self.subTest(overrides=overrides):
                result = self.run_check(overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("read-only", result.stderr)

    def test_missing_project_cross_project_or_nonstandard_volume_is_rejected(self):
        for overrides in ({"investment_backend:project": ""},
                          {"investment_mongo:project": "other"},
                          {"investment_mongo:volume": "other_volume"}):
            with self.subTest(overrides=overrides):
                self.assertNotEqual(self.run_check(overrides).returncode, 0)


if __name__ == "__main__":
    unittest.main()
