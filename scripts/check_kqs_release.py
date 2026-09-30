#!/usr/bin/env python3
"""Read-only local release checks and a grouped Git inventory; never deploys or stages files."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml

from scripts.test_database_safety import validate_test_target
DEPLOY_ROOT = "/opt/investing-app"
STATE_DIR = DEPLOY_ROOT + "/state/kqs"


def _load(root, relative):
    return yaml.load((root / relative).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def inspect_configuration(root=ROOT):
    errors, deployment_warnings = [], []
    try:
        prod = _load(root, "docker-compose.prod.yml")
        mounts = prod["services"]["backend"].get("volumes", [])
        mount = next((item for item in mounts if isinstance(item, dict)
                      and item.get("target") == "/app/state/kqs"), {})
        if (mount.get("source") != "${KQS_STATE_DIR:-" + STATE_DIR + "}"
                or mount.get("type") != "bind" or mount.get("read_only") != "true"
                or mount.get("bind", {}).get("create_host_path") != "false"):
            errors.append("Production KQS state must use the explicit, existing, read-only host directory.")
        worker = _load(root, "infra/docker/docker-compose.engine.yml")
        for service in ("kqs-pipeline", "kqs-backup", "kqs-status"):
            if not any(value.startswith(STATE_DIR + ":") for value in worker["services"][service]["volumes"]):
                errors.append(service + " must share the production KQS state directory.")
        ci = _load(root, ".github/workflows/kqs-runtime.yml")
        job = ci["jobs"].get("manual-ledger-and-datasets", {})
        validate_test_target(job.get("env", {}).get("MONGODB_URL", ""),
                             job.get("env", {}).get("MONGODB_DB", ""))
        if job.get("services", {}).get("mongo", {}).get("image") != "mongo:7":
            errors.append("KQS backend CI needs its own disposable MongoDB service.")
        commands = "\n".join(step.get("run", "") for step in job.get("steps", []))
        for test in ("tests/test_kqs_bridge.py", "tests/test_kqs_contract.py", "tests/test_kqs_learning_readiness.py"):
            if test not in commands:
                errors.append("KQS backend CI does not execute " + test)
        if set(job.get("strategy", {}).get("matrix", {}).get("python-version", [])) != {"3.12", "3.14"}:
            errors.append("KQS backend CI must cover Python 3.12 and 3.14.")
        for trigger in ("push", "pull_request"):
            filters = ci["on"][trigger]["paths"]
            for path in ("tests/**", "scripts/test_database_safety.py", "app/frontend/js/views/strategyList.js"):
                if path not in filters:
                    errors.append(trigger + " does not watch " + path)
        if "workflow_call" not in ci["on"]:
            errors.append("KQS CI must be callable as a release gate.")

        deploy = _load(root, ".github/workflows/deploy.yml")
        if deploy.get("env", {}).get("DEPLOY_PATH") != DEPLOY_ROOT:
            deployment_warnings.append("Automatic deployment does not use the verified deployment root.")
        for step in deploy["jobs"]["deploy"]["steps"]:
            target = step.get("env", {}).get("DEPLOY_PATH")
            if target and target != DEPLOY_ROOT:
                deployment_warnings.append("Existing automatic deployment still uses " + target)
        gate = deploy["jobs"].get("kqs-checks", {})
        if (gate.get("uses") != "./.github/workflows/kqs-runtime.yml"
                or deploy["jobs"]["build"].get("needs") != "kqs-checks"):
            deployment_warnings.append("Automatic deployment does not require the KQS regression gate.")
        deploy_commands = "\n".join(step.get("run", "") for step in deploy["jobs"]["deploy"]["steps"])
        if "kqs-deployment-preflight.sh" not in deploy_commands or "COMPOSE_PROJECT_NAME" not in deploy_commands:
            deployment_warnings.append("Automatic deployment does not preserve verified existing state/volume identity.")
        if "/tmp/backend.env" in deploy_commands:
            deployment_warnings.append("Automatic deployment still overwrites the existing backend environment.")
        if "docker container prune" in deploy_commands or "docker image prune -af" in deploy_commands:
            deployment_warnings.append("Automatic deployment prunes unrelated Docker resources.")
        if "up -d --no-deps backend" not in deploy_commands or "previous_image=" not in deploy_commands:
            deployment_warnings.append("Automatic deployment does not limit restart scope or provide backend rollback.")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        errors.append("Unreadable release configuration: " + type(exc).__name__)
    return {"configuration_ok": not errors, "errors": errors,
            "deployment_warnings": sorted(set(deployment_warnings)),
            "deployment_configuration_ready": not errors and not deployment_warnings}


def inspect_git(root=ROOT):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True)
    tracked = set(git("ls-files", "-z").split("\0")) - {""}
    entries = [entry for entry in git("status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0") if entry]
    groups = {"engine_and_evidence": 0, "homepage_kqs": 0, "test_ci_release": 0, "other_existing_changes": 0}
    for entry in entries:
        path = entry[3:]
        if path.startswith(("engine/", "docs/engine/")) or path == "AGENTS.md":
            group = "engine_and_evidence"
        elif path.startswith(("infra/", ".github/", "scripts/", "tests/")) or path.startswith(("Dockerfile", "docker-compose")):
            group = "test_ci_release"
        elif "kqs" in path.lower() or path in ("app/frontend/js/views/strategyList.js", "docs/KQS_HUMAN_DECISION_DATASET.md"):
            group = "homepage_kqs"
        else:
            group = "other_existing_changes"
        groups[group] += 1
    required = {
        "AGENTS.md", "tests/conftest.py", "tests/test_kqs_bridge.py", "tests/test_kqs_contract.py", "tests/test_kqs_learning_readiness.py",
        "app/backend/kqs_contract.py", "app/backend/kqs_recommendations.py", "app/backend/routers/kqs.py",
        "app/frontend/js/views/kqsRecommendations.js", "app/frontend/js/views/strategyList.js",
        "scripts/test_database_safety.py", "scripts/check_kqs_release.py",
        "scripts/tests/test_database_safety.py", "scripts/tests/test_kqs_release_safety.py",
        "scripts/tests/test_kqs_deployment_preflight.py", "scripts/tests/requirements.kqs-backend.txt",
        ".github/workflows/kqs-runtime.yml", "infra/ops/kqs-deployment-preflight.sh",
        "infra/docker/docker-compose.engine.yml", "engine/kqs/pyproject.toml", "engine/kqs/requirements.lock",
        "engine/kqs/revisions/EINT-007A.json", "engine/kqs/migration_manifest.json",
        "docs/engine/evidence_registry.json", "docs/engine/source_manifest.json",
    }
    required.update(path.relative_to(root).as_posix() for path in (root / "engine/kqs/src").rglob("*.py"))
    required.update(path.relative_to(root).as_posix() for path in (root / "engine/kqs/src/kqs_publication/calendars").glob("*.json"))
    missing = sorted(required - tracked)
    private = sorted(path for path in tracked if
                     (path.startswith("state/kqs/") and path != "state/kqs/.gitignore")
                     or Path(path).suffix in {".sqlite3", ".key"}
                     or Path(path).name == ".env")
    return {"clean": not entries, "change_count": len(entries), "change_groups": groups,
            "untracked_required_count": len(missing), "untracked_required_examples": missing[:12],
            "private_files_tracked": private, "git_ready": not entries and not missing and not private}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-clean", action="store_true", help="Fail unless required code is tracked and the worktree is clean")
    parser.add_argument("--require-deployment-ready", action="store_true", help="Fail for pending automatic deployment configuration")
    args = parser.parse_args(argv)
    report = inspect_configuration()
    try:
        report["git"] = inspect_git()
    except (OSError, subprocess.CalledProcessError):
        report["errors"].append("Cannot inspect Git source inventory.")
        report["configuration_ok"] = False
        report["git"] = {"git_ready": False}
    ok = report["configuration_ok"] and not report["git"].get("private_files_tracked", [])
    if args.require_clean:
        ok = ok and report["git"]["git_ready"]
    if args.require_deployment_ready:
        ok = ok and report["deployment_configuration_ready"]
    report["check_ok"] = bool(ok)
    report["production_deployed_or_verified"] = False
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
