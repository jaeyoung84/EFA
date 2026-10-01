"""EINT-017 strategy board: item grouping, holding sell signals, and v4 exit parity with the engine."""
import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest

from app.backend import kqs_strategy_board as board
from test_kqs_native_views import native_publication  # noqa: F401  (fixture)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine/kqs/tests"))


def _lot(item=None, symbol="005930", bought="2026-09-01", price=100.0, remaining=10.0):
    snapshot = {"signal_type": item} if item else None
    buy = {"_id": f"buy-{symbol}-{item}", "symbol": symbol, "name": "합성", "fill_price": price,
           "trade_date": bought, "recommendation_snapshot": snapshot}
    return {"buy": buy, "quantity": remaining, "remaining": remaining, "allocations": []}


def _trade_day(document):
    return document["trade_date"]


def test_board_items_group_by_item_and_keep_published_rank():
    payload = {"schema_version": "kqs-recommendations/v2", "signals": [
        {"signal_id": "a", "action": "entry", "selection_outcome": "selected", "signal_type": "pullback_v4",
         "selection_rank": 2, "symbol": "000660", "name": "B", "reasons": [{"evidence": "e"}]},
        {"signal_id": "b", "action": "entry", "selection_outcome": "selected", "signal_type": "pullback_v4",
         "selection_rank": 1, "symbol": "005930", "name": "A", "reasons": []},
        {"signal_id": "c", "action": "entry", "selection_outcome": "selected", "signal_type": "swing",
         "selection_rank": 1, "symbol": "005930", "name": "A", "reasons": []},
    ]}
    items = {row["item"]: row for row in board.board_items(payload)}
    assert [p["symbol"] for p in items["pullback_v4"]["picks"]] == ["005930", "000660"]
    assert [p["symbol"] for p in items["swing"]["picks"]] == ["005930"]  # same symbol, two items
    assert items["long_term"]["picks"] == []
    v1 = {row["item"]: row for row in board.board_items({"schema_version": "kqs-recommendations/v1", "signals": []})}
    assert v1["pullback_v4"]["published"] is False


def test_holding_signals_cover_item_less_unavailable_and_reversion_states():
    book = {"lots": {"005930": [_lot("swing"), _lot(None, bought="2026-09-02")],
                     "000660": [_lot("long_term", symbol="000660")],
                     "035420": [_lot("swing", symbol="035420", remaining=0.0)]}}
    payload = {"exit_monitor": [{"symbol": "005930", "signal_type": "swing", "state": "exit_signal", "close": 99.0},
                                {"symbol": "000660", "signal_type": "long_term", "state": "no_current_bar"}]}
    rows = board.holding_signals(book, payload, _trade_day)
    states = {(row["symbol"], row["item"]): row for row in rows}
    assert states[("005930", "swing")]["state"] == "exit_signal"
    assert states[("005930", "swing")]["reason_label"] == "청산 조건 충족"
    assert states[("005930", None)]["state"] == "no_item"
    assert states[("000660", "long_term")]["state"] == "unavailable"
    assert ("035420", "swing") not in states  # fully sold lots are not holdings
    assert rows[0]["state"] == "exit_signal"  # exit signals sort first
    assert {row["state"] for row in board.holding_signals(book, None, _trade_day)} == {"unavailable", "no_item"}


def test_v4_holding_exit_matches_engine_holding_exit():
    import pullback_v4_fixture as fx
    from kqs_runtime import pullback_v4 as v4
    ohlcv, benchmarks, _ = fx.build()
    frame = next(iter(ohlcv.values()))
    bench = benchmarks["KOSPI"]
    compared = 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for start in (300, 420, 560):
            entry_date, entry_price = frame.index[start], float(frame["Open"].iloc[start])
            for offset in range(0, 18):
                as_of = frame.index[start + offset]
                engine = v4.holding_exit(frame, bench, entry_date, entry_price, as_of)
                prepared = v4.compute_indicators(frame.loc[:as_of].tail(v4.CONFIG["warmup_days"] + 1)).tail(40)
                gf = bench.loc[:as_of].copy()
                gf[v4.SMA_COL] = gf["Close"].rolling(50).mean()
                series = [{"date": d.date().isoformat(), "close": float(r["Close"]), "atr": float(r["ATR"])}
                          for d, r in prepared.iterrows()]
                web = board.v4_holding_exit(series, v4.regime_off(gf, as_of), entry_date.date().isoformat(), entry_price)
                assert (web["state"], web["reason"], web["holding_days"]) == (engine["state"], engine["reason"], engine["holding_days"])
                assert web["stop_price"] == pytest.approx(engine["stop_price"], abs=0.01)
                assert web["trailing_level"] == pytest.approx(engine["trailing_level"], abs=0.01)
                compared += 1
    assert compared == 54


def test_v4_entry_older_than_series_is_past_the_holding_limit():
    series = [{"date": f"2026-09-{day:02d}", "close": 100.0, "atr": 1.0} for day in range(1, 29)]
    result = board.v4_holding_exit(series, False, "2026-08-01", 100.0)
    assert result["reason"] == "holding_limit" and result["holding_days"] is None and result["stop_price"] is None


@pytest.mark.asyncio
async def test_strategy_board_api_shows_items_and_my_holding(native_publication, client, make_user):  # noqa: F811
    publication, _ = native_publication
    headers = await make_user("board@example.com")
    signal = publication["signals"][0]
    bought = await client.post("/api/kqs/decisions", headers=headers, json={
        "signal_id": signal["signal_id"], "decision": "buy", "quantity": 3, "fill_price": 100,
        "request_id": "board-buy-0001"})
    assert bought.status_code == 200, bought.text
    response = await client.get("/api/kqs/strategy-board", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["available"] and data["schema_version"] == "kqs-recommendations/v2"
    items = {row["item"]: row for row in data["items"]}
    assert set(items) == {"swing", "long_term", "pullback_v4"}
    assert any(p["signal_id"] == signal["signal_id"] for p in items[signal["signal_type"]]["picks"])
    [holding] = data["sell_signals"]
    assert holding["item"] == signal["signal_type"] and holding["state"] == "not_monitored"
    assert (await client.get("/api/kqs/strategy-board")).status_code == 401
