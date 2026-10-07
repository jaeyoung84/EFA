"""EINT-032: 사전등록 B-1 관측 파일 읽기(없음·형식 오류·정상)와 로그인 필요."""
import json

import pytest

from app.backend import kqs_strategy_board


def test_reader_reports_missing_foreign_and_valid_files(tmp_path):
    path = tmp_path / "rebound-shadow.json"
    assert kqs_strategy_board.read_rebound_shadow(path)["available"] is False
    path.write_text(json.dumps({"schema": "other"}))
    assert kqs_strategy_board.read_rebound_shadow(path)["available"] is False
    path.write_text(json.dumps({"schema": "kqs-rebound-shadow/v1", "through": "2026-10-20", "registered_from": "2026-10-07",
                                "preregistration": "docs/x.md", "preregistration_sha256": "ab" * 32,
                                "costs": {"buy": 0.00065, "sell": 0.00265},
                                "summary": {"signals": 2, "closed_pairs": 1, "verdict": "not_due"},
                                "rows": [{"as_of": "2026-10-08", "rank": 1}, {"as_of": "2026-10-09", "rank": 2}]}))
    data = kqs_strategy_board.read_rebound_shadow(path)
    assert data["available"] is True and data["summary"]["verdict"] == "not_due"
    assert [row["as_of"] for row in data["rows"]] == ["2026-10-09", "2026-10-08"]
    assert "판정하지 않습니다" in data["disclaimer"]


@pytest.mark.asyncio
async def test_endpoint_requires_login(client, make_user):
    assert (await client.get("/api/kqs/rebound-shadow")).status_code == 401
    headers = await make_user("rebound@example.com")
    response = await client.get("/api/kqs/rebound-shadow", headers=headers)
    assert response.status_code == 200 and "available" in response.json()
