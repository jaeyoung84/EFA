"""EINT-033: 신호 단계 기록 읽기(없음·형식 오류·정상)와 로그인 필요."""
import json

import pytest

from app.backend import kqs_strategy_board


def test_reader_states(tmp_path):
    path = tmp_path / "signal-funnel.json"
    assert kqs_strategy_board.read_signal_funnel(path)["available"] is False
    path.write_text(json.dumps({"schema": "x"}))
    assert kqs_strategy_board.read_signal_funnel(path)["available"] is False
    path.write_text(json.dumps({"schema": "kqs-signal-funnel/v1", "as_of": "2026-10-07", "today": {"swing": {"both": 0}},
                                "watch": {"swing": [], "long_term": []}, "history": [{"as_of": str(i)} for i in range(80)]}))
    data = kqs_strategy_board.read_signal_funnel(path)
    assert data["available"] is True and len(data["history"]) == 60 and "추천이 아닙니다" in data["disclaimer"]


@pytest.mark.asyncio
async def test_endpoint_requires_login(client, make_user):
    assert (await client.get("/api/kqs/signal-funnel")).status_code == 401
    headers = await make_user("funnel@example.com")
    assert (await client.get("/api/kqs/signal-funnel", headers=headers)).status_code == 200
