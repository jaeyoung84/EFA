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
    """EINT-031(v4): 08:20 시도가 KRX 공개 전이라 실패해도, 새 거래일 유예 시간 안이면 경고하지 않는다(08:50·09:30·10:30 재시도)."""
    _, path = native_publication          # 2026-09-28 게시본, 09-29 09:00 KST 생성
    evening = datetime(2026, 9, 29, 13, 5, tzinfo=timezone.utc)           # 09-29 22:05 KST: 09-29 자료는 아직 공개 전
    assert pipeline_health(path.parent, now=evening)["expected_session"] == "2026-09-28"
    first_try = datetime(2026, 9, 29, 23, 25, tzinfo=timezone.utc)        # 09-30 08:25 KST, 첫 시도 실패
    _status(path.parent, status="collect_failed", session="2026-09-29", finished_at=first_try.isoformat())
    health = pipeline_health(path.parent, now=first_try)
    assert health["expected_session"] == "2026-09-29" and health["level"] == "ok" and health["alerts"] == []
    after_grace = datetime(2026, 9, 30, 2, 45, tzinfo=timezone.utc)       # 09-30 11:45 KST(공개 08:00 + 유예 3.5시간 뒤)
    _status(path.parent, status="collect_failed", session="2026-09-29", finished_at=after_grace.isoformat())
    late = pipeline_health(path.parent, now=after_grace)
    assert late["level"] == "error" and [a["code"] for a in late["alerts"]][:2] == ["publication_missing", "last_run_failed"]



def test_eint030_december_calendar_alerts(monkeypatch):
    """12월에 내년 캘린더가 없으면 1일부터, 잠정본이면 20일부터 경고. 다른 달·확정본은 경고 없음."""
    from app.backend import kqs_pipeline_health as module
    from kqs_publication import calendar as krx_calendar
    at = lambda m, d, y=2026: datetime(y, m, d, 3, tzinfo=timezone.utc)  # noqa: E731
    assert module.calendar_alerts(at(11, 30)) == []
    assert module.calendar_alerts(at(12, 5)) == []                                     # 2027 잠정본, 20일 전
    provisional = module.calendar_alerts(at(12, 21))
    assert [a["code"] for a in provisional] == ["calendar_next_year_provisional"] and "2027" in provisional[0]["title"]
    missing = module.calendar_alerts(at(12, 2, 2027))                                    # 2028 미등록
    assert [a["code"] for a in missing] == ["calendar_next_year_missing"] and "2028" in missing[0]["title"]
    monkeypatch.setitem(krx_calendar.REVIEWED_CALENDARS, "krx-equity-2028", "krx_equity_2026.json")   # 확정본처럼
    assert module.calendar_alerts(at(12, 22, 2027)) == []
