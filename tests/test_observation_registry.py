"""EINT-044 관측 등록부: 실제 등록부 형식·근거 파일, 45일 경고·기한 지남 판정, 엔진 상태 알림 연결."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

from app.backend import observation_registry as reg

ROOT = Path(__file__).resolve().parents[1]
REAL_ALERTS = reg.observation_alerts      # conftest가 테스트 중 끄기 전에 잡아 둔 원래 함수


def test_checked_in_registry_is_valid_and_sources_exist():
    registry = reg.load()
    assert reg.validate(registry, ROOT) == []
    ids = {i["id"] for s in reg.SECTIONS for i in registry.get(s, [])}
    assert {"D-33-EINT-023", "CODX-MA-002", "KR-CRASH-REBOUND-v1", "KR-LV-v2", "CODX-DIR-002-DENSITY"} <= ids


def _registry():
    return {"schema": "kqs-observation-registry/v1", "active_hypotheses": [
        {"id": "H1", "title": "가설", "status": "active", "maturity": "100", "decision_deadline_on": "2028-01-01",
         "deadline_result_if_immature": "x", "decision_rule": "x", "next_required_review_on": "2026-10-31",
         "review_scope": "x", "source_paths": ["README.md"], "application_policy": "x", "interim_report_on": "2026-09-01"}],
        "descriptive_observations": [], "planned_not_started": [], "explicitly_not_active": []}


def test_due_items_warn_within_45_days_and_flag_overdue():
    due = reg.due_items(_registry(), date(2026, 10, 16))
    assert [(d["field"], d["overdue"], d["days_left"]) for d in due] == [("interim_report_on", True, -45),
                                                                        ("next_required_review_on", False, 15)]
    assert reg.due_items(_registry(), date(2026, 6, 1)) == []


def test_validate_reports_missing_fields_bad_dates_and_duplicates():
    r = _registry()
    r["active_hypotheses"].append({**r["active_hypotheses"][0], "decision_deadline_on": "2028-13-01", "review_scope": ""})
    errors = reg.validate(r)
    assert "H1: id 중복" in errors and any("날짜 형식 오류" in e for e in errors) and "H1: review_scope 없음" in errors


def test_alerts_levels_and_unreadable_file(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_registry()), encoding="utf-8")
    alerts = REAL_ALERTS(datetime(2026, 10, 16, 3, tzinfo=timezone.utc), path)
    assert [a["level"] for a in alerts] == ["error", "warning"] and "D-15" in alerts[1]["message"]
    assert REAL_ALERTS(datetime(2026, 10, 16, tzinfo=timezone.utc), tmp_path / "none.json")[0]["code"] == "observation_registry_unreadable"


def test_pipeline_health_includes_observation_alerts(tmp_path, monkeypatch):
    from app.backend import kqs_pipeline_health as health
    monkeypatch.setattr(reg, "observation_alerts", REAL_ALERTS)
    monkeypatch.setattr(reg, "REGISTRY", tmp_path / "r.json")
    (tmp_path / "r.json").write_text(json.dumps(_registry()), encoding="utf-8")
    report = health.pipeline_health(tmp_path, now=datetime(2026, 10, 16, 3, tzinfo=timezone.utc))
    assert any(a["code"].startswith("observation_") for a in report["alerts"])
