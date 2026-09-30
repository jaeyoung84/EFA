"""Target/fixture regressions; no real MongoDB or application import is required."""
import asyncio
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.test_database_safety import (
    DEFAULT_DATABASE, UnsafeTestDatabase, assert_cleanup_target,
    target_from_environment, validate_test_target,
)


class DatabaseSafetyTests(unittest.TestCase):
    def test_default_is_a_separate_local_direct_connection(self):
        target = target_from_environment({})
        self.assertEqual(target.database, DEFAULT_DATABASE)
        self.assertEqual(target.url, "mongodb://127.0.0.1:27017?directConnection=true")

    def test_explicit_local_test_namespaces_are_accepted(self):
        for host in ("localhost:27017", "127.0.0.1:37017", "[::1]:27017", "mongo:27017", "kqs-test-mongo:27017"):
            for name in ("jy_investing_test", "jy_investing_test_eint010"):
                target = validate_test_target("mongodb://" + host + "/" + name, name)
                self.assertIn("directConnection=true", target.url)

    def test_production_and_lookalike_names_are_rejected(self):
        for name in ("jy_investing", "investment_db", "admin", "test", "", "jy_investing_testprod",
                     "jy_investing_test_PROD", "jy_investing_test_", "jy_investing_test_" + "a" * 41):
            with self.subTest(name=name), self.assertRaises(UnsafeTestDatabase):
                validate_test_target("mongodb://localhost:27017", name)

    def test_remote_credentials_srv_multihost_and_invalid_ports_are_rejected(self):
        for url in ("mongodb://example.com", "mongodb://10.0.0.10", "mongodb+srv://localhost",
                    "mongodb://user:fixture-password@localhost", "mongodb://localhost,127.0.0.1",
                    "mongodb://localhost:0", "mongodb://localhost:65536", "mongodb://localhost:bad",
                    "mongodb://%6cocalhost", "mongodb://localhost#fragment", "not-a-uri"):
            with self.subTest(url=url), self.assertRaises(UnsafeTestDatabase):
                validate_test_target(url, DEFAULT_DATABASE)

    def test_uri_database_and_replica_redirects_cannot_bypass_guard(self):
        for suffix in ("/investment_db", "/%69nvestment_db", "/admin", "/jy_investing_test_extra",
                       "/?replicaSet=prod", "/?authSource=admin", "/?directConnection=false",
                       "/?directConnection=true&directConnection=false", "/?unknown", "/?tls=true"):
            with self.subTest(suffix=suffix), self.assertRaises(UnsafeTestDatabase):
                validate_test_target("mongodb://localhost:27017" + suffix, DEFAULT_DATABASE)

    def test_validation_is_idempotent_and_does_not_print_credentials(self):
        target = validate_test_target("mongodb://localhost/", DEFAULT_DATABASE)
        self.assertEqual(validate_test_target(target.url, target.database), target)
        with self.assertRaises(UnsafeTestDatabase) as caught:
            validate_test_target("mongodb://user:fixture-password@localhost", DEFAULT_DATABASE)
        self.assertNotIn("fixture-password", str(caught.exception))

    def test_runtime_environment_change_and_cached_production_handle_are_rejected(self):
        expected = target_from_environment({})
        assert_cleanup_target(expected, {}, DEFAULT_DATABASE)
        for env in ({"MONGODB_DB": "investment_db"}, {"MONGODB_URL": "mongodb://example.com"},
                    {"MONGODB_DB": "jy_investing_test_another"},
                    {"MONGODB_URL": "mongodb://127.0.0.1:37017"}):
            with self.subTest(env=env), self.assertRaises(UnsafeTestDatabase):
                assert_cleanup_target(expected, env)
        with self.assertRaises(UnsafeTestDatabase):
            assert_cleanup_target(expected, {}, "investment_db")

    def test_unsafe_conftest_stops_before_importing_application(self):
        for env_values in ({"MONGODB_DB": "investment_db"},
                           {"MONGODB_URL": "mongodb://remote.example/jy_investing_test"}):
            environment = {**os.environ, "MONGODB_URL": "mongodb://127.0.0.1:27017",
                           "MONGODB_DB": DEFAULT_DATABASE, **env_values}
            code = ("import runpy, sys\n"
                    "try: runpy.run_path('tests/conftest.py')\n"
                    "except RuntimeError as e:\n"
                    " print(type(e).__name__)\n"
                    " assert 'app.backend.main' not in sys.modules\n"
                    " assert 'app.backend.db' not in sys.modules\n"
                    "else: raise AssertionError('unsafe fixture was imported')\n")
            result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=environment,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("UnsafeTestDatabase", result.stdout)

    def _fixture_namespace(self, database):
        fake_modules = {
            "pytest_asyncio": SimpleNamespace(fixture=lambda fn=None, **_: fn if fn else lambda wrapped: wrapped),
            "httpx": SimpleNamespace(ASGITransport=object, AsyncClient=object),
            "app.backend.db": SimpleNamespace(get_db=lambda: database),
            "app.backend.main": SimpleNamespace(app=object()),
        }
        with patch.dict(sys.modules, fake_modules):
            return runpy.run_path(str(ROOT / "tests/conftest.py"))

    def test_actual_fixture_checks_before_setup_and_teardown_deletes(self):
        deleted = []
        class Database:
            name = DEFAULT_DATABASE
            def __getitem__(self, name):
                async def delete_many(query):
                    deleted.append(name)
                return SimpleNamespace(delete_many=delete_many)
        async def scenario():
            namespace = self._fixture_namespace(Database())
            fixture = namespace["_clean_database"]()
            await anext(fixture)
            count = len(namespace["TEST_COLLECTIONS"])
            self.assertEqual(len(deleted), count)
            os.environ["MONGODB_DB"] = "investment_db"
            with self.assertRaises(UnsafeTestDatabase):
                await anext(fixture)
            self.assertEqual(len(deleted), count)  # no teardown deletion after the change
        with patch.dict(os.environ, {"MONGODB_URL": "mongodb://127.0.0.1:27017",
                                     "MONGODB_DB": DEFAULT_DATABASE}):
            asyncio.run(scenario())

    def test_actual_fixture_rejects_wrong_handle_before_any_delete(self):
        class Database:
            name = "investment_db"
            def __getitem__(self, _):
                raise AssertionError("Must not access a collection")
        async def scenario():
            namespace = self._fixture_namespace(Database())
            with self.assertRaises(UnsafeTestDatabase):
                await anext(namespace["_clean_database"]())
        with patch.dict(os.environ, {"MONGODB_URL": "mongodb://127.0.0.1:27017",
                                     "MONGODB_DB": DEFAULT_DATABASE}):
            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
