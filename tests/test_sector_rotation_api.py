"""EINT-021 섹터 순환: 엔진이 만든 파일을 웹이 그대로 읽는다(없으면 사용할 수 없음)."""
import sqlite3
import sys
from pathlib import Path

import pytest

from app.backend import sector_rotation as web

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine/kqs/tests"))


def _engine_file(tmp_path):
    from kqs_collection.sector_rotation import build_sector_rotation, write_sector_rotation
    import test_sector_rotation as engine

    db = tmp_path / "market.sqlite3"
    engine.market_db(db, {"069500": lambda i: 30000 + i, "091160": lambda i: 50000 + 9 * i, "091170": lambda i: 10000 - i})
    target = tmp_path / "state" / "sector-rotation.json"
    write_sector_rotation(target, build_sector_rotation(db, engine.AS_OF.isoformat()))
    return target


def test_reader_passes_engine_values_through(tmp_path):
    data = web.read_sector_rotation(_engine_file(tmp_path))
    assert data["available"] and data["benchmark"]["symbol"] == "069500"
    keys = {s["key"]: s for s in data["sectors"]}
    assert set(keys) == {"semis", "banks"} and keys["semis"]["rs_ratio"] > keys["banks"]["rs_ratio"]
    assert data["smoothing_weeks"] == {"short": 4, "long": 13, "momentum": 4}
    assert "매수·매도 신호" in data["disclaimer"]


def test_reader_missing_or_foreign_file(tmp_path):
    assert web.read_sector_rotation(tmp_path / "absent.json")["available"] is False
    other = tmp_path / "other.json"
    other.write_text('{"schema": "x"}')
    assert web.read_sector_rotation(other)["available"] is False


@pytest.mark.asyncio
async def test_public_endpoint(client, monkeypatch, tmp_path):
    monkeypatch.setenv("KQS_SECTOR_ROTATION", str(_engine_file(tmp_path)))
    response = await client.get("/api/market/sector-rotation")
    assert response.status_code == 200 and response.json()["available"] is True
