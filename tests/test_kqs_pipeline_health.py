"""EINT-020 엔진 상태 경고: 엔진 check_health 재사용 + 실행 실패·오래된 실행·보조 단계 실패."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.backend.kqs_pipeline_health import pipeline_health
from test_kqs_native_views import native_publication  # noqa: F401  (fixture)

# native_publication 게시본: 2026-09-28 기준, 2026-09-29 00:00 UTC 생성, 2시간 유효
NOW = datetime(2026, 9, 29, 0, 30, tzinfo=timezone.utc)


def _status(directory, **fields):
    run = {"status": "published", "session": "2026-09-28", "finished_at": NOW.isoformat(),
           "steps": {"instrument_catalog": {"status": "written"}, "recommendation_outcomes": {"status": "written"}}}
    run.update(fields)
    (directory / "pipeline-status.json").write_text(json.dumps(run))


def test_published_and_last_run_ok_is_ok(native_publication):  # noqa: F811
    _, path = native_publication
    _status(path.parent)
    health = pipeline_health(path.parent, now=NOW)
    assert health["level"] == "ok" and health["alerts"] == []
    assert (health["expected_session"], health["published_session"]) == ("2026-09-28", "2026-09-28")


@pytest.mark.parametrize("fields,code", [
    ({"status": "collect_failed"}, "last_run_failed"),
    ({"finished_at": (NOW - timedelta(hours=31)).isoformat()}, "run_log_stale"),
    ({"steps": {"recommendation_outcomes": {"status": "failed", "error": "x"}}}, "recommendation_outcomes_failed"),
])
def test_problems_with_a_valid_publication_are_warnings(native_publication, fields, code):  # noqa: F811
    _, path = native_publication
    _status(path.parent, **fields)
    health = pipeline_health(path.parent, now=NOW)
    assert health["level"] == "warning" and [a["code"] for a in health["alerts"]] == [code]


def test_missing_publication_after_grace_is_an_error(tmp_path):
    _status(tmp_path, status="collect_failed", finished_at=datetime(2026, 9, 29, 3, tzinfo=timezone.utc).isoformat())
    health = pipeline_health(tmp_path, now=datetime(2026, 9, 29, 4, tzinfo=timezone.utc))
    codes = [a["code"] for a in health["alerts"]]
    assert health["level"] == "error" and codes[:2] == ["publication_missing", "last_run_failed"]
    assert health["alerts"][1]["level"] == "error"


def test_missing_run_log_is_a_warning(native_publication):  # noqa: F811
    _, path = native_publication
    health = pipeline_health(path.parent, now=NOW)
    assert health["level"] == "warning" and [a["code"] for a in health["alerts"]] == ["run_log_missing"]


@pytest.mark.asyncio
async def test_board_shows_my_card_decisions_and_health(native_publication, client, make_user):  # noqa: F811
    publication, _ = native_publication
    headers = await make_user("card-decisions@example.com")
    first, second = publication["signals"][0], publication["signals"][1]
    bought = await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": first["signal_id"], "decision": "buy", "quantity": 2, "fill_price": 100,
        "request_id": "card-buy-00001"})
    assert bought.status_code == 200, bought.text
    held = await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": second["signal_id"], "decision": "hold", "request_id": "card-hold-0001"})
    assert held.status_code == 200, held.text
    data = (await client.get("/api/kqs/strategy-board", headers=headers)).json()
    assert [row["decision"] for row in data["my_decisions"][first["signal_id"]]] == ["buy"]
    assert data["my_decisions"][first["signal_id"]][0]["quantity"] == 2
    assert [row["decision"] for row in data["my_decisions"][second["signal_id"]]] == ["hold"]
    assert data["health"]["level"] in {"ok", "warning", "error"}
    other = await make_user("card-other@example.com")
    assert (await client.get("/api/kqs/strategy-board", headers=other)).json()["my_decisions"] == {}
    assert (await client.get("/api/kqs/pipeline-health")).status_code == 401
    assert (await client.get("/api/kqs/pipeline-health", headers=headers)).status_code == 200



def test_eint028_collect_failure_while_waiting_for_the_next_attempt_is_not_an_alert(native_publication):  # noqa: F811
    """22:00 시도가 KRX 공개 전이라 실패해도, 새 거래일 유예 시간 안이면 경고하지 않는다(06:00·08:30 재시도)."""
    _, path = native_publication          # 2026-09-28 게시본, 09-29 09:00 KST 생성
    late_night = datetime(2026, 9, 29, 13, 5, tzinfo=timezone.utc)        # 09-29 22:05 KST, 새 거래일 09-29 대기 중
    _status(path.parent, status="collect_failed", session="2026-09-29", finished_at=late_night.isoformat())
    health = pipeline_health(path.parent, now=late_night)
    assert health["expected_session"] == "2026-09-29" and health["level"] == "ok" and health["alerts"] == []
    after_grace = datetime(2026, 9, 30, 0, 45, tzinfo=timezone.utc)       # 09-30 09:45 KST, 마지막 08:30 시도 뒤
    _status(path.parent, status="collect_failed", session="2026-09-29", finished_at=after_grace.isoformat())
    late = pipeline_health(path.parent, now=after_grace)
    assert late["level"] == "error" and [a["code"] for a in late["alerts"]][:2] == ["publication_missing", "last_run_failed"]
