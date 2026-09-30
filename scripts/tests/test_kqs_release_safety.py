"""Release config regression checks; temporary text files only, no Docker/SSH/AWS calls."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.check_kqs_release import inspect_configuration

FILES = ("docker-compose.prod.yml", "infra/docker/docker-compose.engine.yml",
         ".github/workflows/kqs-runtime.yml", ".github/workflows/deploy.yml", "infra/ops/deploy-backend-release.sh")


class ReleaseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for relative in FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def replace(self, relative, before, after):
        path = self.root / relative
        text = path.read_text()
        self.assertIn(before, text)
        path.write_text(text.replace(before, after))

    def test_current_local_configuration_is_valid(self):
        result = inspect_configuration(self.root)
        self.assertTrue(result["configuration_ok"], result["errors"])

    def test_relative_state_mount_and_host_directory_creation_are_rejected(self):
        for before, after in (
            ("${KQS_STATE_DIR:-/opt/investing-app/state/kqs}", "./state/kqs"),
            ("create_host_path: false", "create_host_path: true"),
            ("read_only: true", "read_only: false"),
        ):
            with self.subTest(before=before):
                path = self.root / "docker-compose.prod.yml"
                original = path.read_text()
                self.replace("docker-compose.prod.yml", before, after)
                self.assertFalse(inspect_configuration(self.root)["configuration_ok"])
                path.write_text(original)

    def test_worker_state_drift_is_rejected(self):
        self.replace("infra/docker/docker-compose.engine.yml",
                     "/opt/investing-app/state/kqs:", "/opt/other/state/kqs:")
        self.assertFalse(inspect_configuration(self.root)["configuration_ok"])

    def test_missing_manual_ledger_or_manifest_regression_is_rejected(self):
        self.replace(".github/workflows/kqs-runtime.yml", "tests/test_kqs_bridge.py", "tests/other.py")
        self.assertFalse(inspect_configuration(self.root)["configuration_ok"])

    def test_missing_learning_quality_or_consent_regression_is_rejected(self):
        self.replace(".github/workflows/kqs-runtime.yml", "tests/test_kqs_learning_readiness.py", "tests/other.py")
        self.assertFalse(inspect_configuration(self.root)["configuration_ok"])

    def test_ci_production_database_and_missing_trigger_are_rejected(self):
        self.replace(".github/workflows/kqs-runtime.yml", "jy_investing_test_kqs_ci", "investment_db")
        self.assertFalse(inspect_configuration(self.root)["configuration_ok"])

    def test_pending_old_deployment_is_reported_not_silently_accepted(self):
        path = self.root / ".github/workflows/deploy.yml"
        path.write_text(path.read_text().replace("/opt/investing-app", "/opt/investment-analysis"))
        result = inspect_configuration(self.root)
        self.assertFalse(result["deployment_configuration_ready"])
        self.assertTrue(result["deployment_warnings"])

    def test_current_ssm_deployment_is_ready(self):
        self.assertTrue(inspect_configuration(self.root)["deployment_configuration_ready"])

    def test_ssm_deployment_without_host_rollback_guard_is_reported(self):
        self.replace("infra/ops/deploy-backend-release.sh", 'previous_image="$(docker inspect', 'current="$(docker inspect')
        result = inspect_configuration(self.root)
        self.assertFalse(result["deployment_configuration_ready"])
        self.assertTrue(result["deployment_warnings"])

    def test_ssm_deployment_without_preflight_is_reported(self):
        self.replace("infra/ops/deploy-backend-release.sh", 'bash infra/ops/kqs-deployment-preflight.sh', 'echo investing-app')
        self.assertFalse(inspect_configuration(self.root)["deployment_configuration_ready"])

    def test_malformed_yaml_fails_closed(self):
        (self.root / "docker-compose.prod.yml").write_text("services: [unterminated")
        self.assertFalse(inspect_configuration(self.root)["configuration_ok"])


if __name__ == "__main__":
    unittest.main()
