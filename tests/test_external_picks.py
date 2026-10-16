"""외부 추천 기록: 티커 인식(열·토큰·국내 코드), 저장 검증, 다음 거래일 시가 진입 성과, 연결 성과, 본인 전용 API."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.backend import external_picks as picks
from app.backend.db import get_db

DIRECTORY = {"AAPL": "Apple Inc.", "NVDA": "NVIDIA Corporation", "BRK·B": "Berkshire Hathaway", "ON": "ON Semiconductor", "SPY": "SPDR S&P 500"}
KRX = lambda code: {"ticker": f"{code}.KS", "name": "삼성전자", "code": code} if code == "005930" else None  # noqa: E731


def test_column_mode_uses_ticker_column_and_reports_unknowns():
    text = "Name,Symbol,Upside\nApple,AAPL,12%\nNvidia,NVDA,30%\nGhost,ZZZZ,1%\nBerkshire,BRK.B,5%\n"
    result = picks.parse_candidates(text, DIRECTORY, KRX)
    assert result["mode"] == "column" and [r["ticker"] for r in result["recognized"]] == ["AAPL", "NVDA", "BRK.B"]
    assert result["unrecognized"] == ["ZZZZ"]


def test_token_mode_skips_noise_words_and_reads_korean_codes():
    text = "AI picks for October: Apple (NASDAQ:AAPL) EPS beat, ON is excluded as a word, 삼성전자 005930 and NVDA."
    result = picks.parse_candidates(text, DIRECTORY, KRX)
    assert result["mode"] == "tokens"
    assert [r["ticker"] for r in result["recognized"]] == ["AAPL", "NVDA", "005930.KS"]   # AI·EPS·ON(단어)은 빼고


def _bars(start: date, opens: list[float]) -> dict:
    out, day, i = {}, start, 0
    while i < len(opens):
        if day.weekday() < 5:
            out[day.isoformat()] = [opens[i], opens[i] * 1.01]
            i += 1
        day += timedelta(days=1)
    return out


def test_entry_is_next_session_open_and_horizons():
    bars = _bars(date(2026, 10, 1), [100 + i for i in range(30)])      # 10/1(목) 시작
    path = picks.position_path(bars, "2026-10-01")
    assert path["entry_date"] == "2026-10-02" and path["entry_price"] == 101
    assert path["horizons"]["1m"] == round((bars[sorted(d for d in bars if d > "2026-10-01")[21]][1] / 101 - 1) * 100, 2)
    assert picks.position_path(bars, "2026-12-31") is None


def test_list_and_track_performance_against_benchmark():
    start = date(2026, 9, 1)
    prices = {"AAPL": {"bars": _bars(start, [100] * 10 + [110] * 40)}, "NVDA": {"bars": _bars(start, [100] * 50)},
              "SPY": {"bars": _bars(start, [100] * 50)}}
    first = {"as_of": "2026-09-01", "created_at": datetime(2026, 9, 1), "list_name": "9월", "items": [
        {"ticker": "AAPL", "market": "US", "name": "Apple"}, {"ticker": "NVDA", "market": "US", "name": "Nvidia"}]}
    perf = picks.list_performance(first, prices)
    assert perf["summary"]["priced"] == 2 and perf["summary"]["beat_rate_pct"] == 50.0
    second = {"as_of": "2026-10-01", "created_at": datetime(2026, 10, 1), "list_name": "10월", "items": [{"ticker": "NVDA", "market": "US", "name": "Nvidia"}]}
    track = picks.source_track([second, first], prices)
    assert [p["list_name"] for p in track["periods"]] == ["9월", "10월"] and track["periods"][0]["until"] == "2026-10-02"
    assert track["periods"][0]["return_pct"] == round((0.0 + 10.0) / 2 - picks.SWITCH_COST_PCT, 2)


@pytest.mark.asyncio
async def test_save_validation_and_user_scoped_api(client, make_user, monkeypatch):
    await get_db()[picks.DIRECTORY].insert_one({"_id": "alpaca", "symbols": DIRECTORY, "fetched_at": datetime.now(timezone.utc)})
    scheduled = []
    monkeypatch.setattr(picks, "schedule_prices", lambda tickers: scheduled.append(set(tickers)))
    assert (await client.get("/api/external-picks")).status_code == 401
    me, other = await make_user("picks-me@test.com"), await make_user("picks-other@test.com")
    parsed = (await client.post("/api/external-picks/parse", headers=me, json={"text": "Symbol\nAAPL\nNVDA\n"})).json()
    assert [r["ticker"] for r in parsed["recognized"]] == ["AAPL", "NVDA"]
    today = datetime.now(picks.KST).date()
    saved = await client.post("/api/external-picks", headers=me, json={"source": "InvestingPro ProPicks", "list_name": "10월 Tech Titans",
                                                                      "as_of": today.isoformat(), "items": parsed["recognized"], "raw_text": "Symbol\nAAPL\nNVDA\n"})
    assert saved.status_code == 200 and scheduled == [{"AAPL", "NVDA", "SPY"}]
    future = await client.post("/api/external-picks", headers=me, json={"source": "x", "as_of": (today + timedelta(days=2)).isoformat(),
                                                                       "items": parsed["recognized"]})
    assert future.status_code == 422
    mine = (await client.get("/api/external-picks", headers=me)).json()
    assert len(mine["lists"]) == 1 and mine["lists"][0]["pending"] == ["AAPL", "NVDA"] and "InvestingPro ProPicks" in mine["tracks"]
    assert (await client.get("/api/external-picks", headers=other)).json()["lists"] == []
    list_id = mine["lists"][0]["id"]
    assert (await client.delete(f"/api/external-picks/{list_id}", headers=other)).status_code == 404
    assert (await client.delete(f"/api/external-picks/{list_id}", headers=me)).status_code == 200


@pytest.mark.asyncio
async def test_collect_prices_stores_bars_for_tickers_and_benchmarks():
    await get_db()[picks.LISTS].insert_one({"user_id": "u", "source": "s", "list_name": "l", "as_of": "2026-10-01", "created_at": datetime.now(timezone.utc),
                                            "items": [{"ticker": "AAPL", "market": "US", "name": "A"}, {"ticker": "005930.KS", "market": "KR", "name": "삼성"}]})
    seen = {}

    def fake_us(symbols, start, now):
        seen["us"] = (sorted(symbols), start)
        return {s: {"2026-10-02": [1.0, 1.1]} for s in symbols}

    async def fake_kr(ticker, start):
        seen.setdefault("kr", []).append(ticker)
        return {"2026-10-02": [70000.0, 71000.0]}
    result = await picks.collect_prices(fetch_us=fake_us, fetch_kr=fake_kr)
    assert seen["us"] == (["AAPL", "SPY"], date(2026, 9, 21)) and sorted(seen["kr"]) == ["005930.KS", "069500.KS"]
    assert result["stored"] == 4 and await get_db()[picks.PRICES].count_documents({}) == 4


def test_korean_list_ignores_english_fragments_inside_company_names():
    text = ("| **A204320 HL만도** | 42,650 | 저평가 | 1,969.9 B |\n| **A003550 LG** | 106,300 |\n| **A000270 기아** | 108,600 |\n"
            "| **A035420 NAVER** | 179,400 |\n")
    result = picks.parse_candidates(text, {"HL": "Hecla Mining", "LG": "?", "B": "?"}, lambda c: {"ticker": f"{c}.KS", "name": c})
    assert [r["ticker"] for r in result["recognized"]] == ["204320.KS", "003550.KS", "000270.KS", "035420.KS"]


def test_backfilled_lists_are_marked_and_tracked_separately():
    created = datetime(2026, 10, 14, 3, tzinfo=timezone.utc)
    assert picks.is_backfilled({"as_of": "2024-10-31", "created_at": created})
    assert not picks.is_backfilled({"as_of": "2026-10-10", "created_at": created})     # 7일 이내 = 직접 기록


def _ascii_pdf(lines: list[str]) -> bytes:
    """글꼴 하나(Helvetica)로 한 쪽짜리 PDF를 만든다(실제 pypdf 추출 경로 시험용)."""
    stream = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({line}) Tj 0 -16 Td" for line in lines) + " ET"
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode() + "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    return out + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def test_pdf_sections_reads_real_pdf_and_rejects_bad_files():
    sections = picks.pdf_sections(_ascii_pdf(["Samsung (A005930)KOSE", "SK hynix A000660"]))
    assert len(sections) == 1 and sections[0]["month"] is None and sections[0]["pages"] == 1
    result = picks.parse_candidates(sections[0]["text"], {}, lambda c: {"ticker": f"{c}.KS", "name": c})
    assert [r["ticker"] for r in result["recognized"]] == ["005930.KS", "000660.KS"]
    for bad in (b"not a pdf", b"%PDF-1.4\ngarbage", b"%PDF" + b"0" * picks.PDF_MAX_BYTES):
        with pytest.raises(picks.ExternalPicksError):
            picks.pdf_sections(bad)


def test_pdf_sections_split_by_month_heading_latest_first(monkeypatch):
    import pypdf

    pages = ["한국 대장주 2026년 9월\nA005930", "A000660 (제목 없는 다음 쪽)", "2026년 10월 명단\nA035420", "2026년 13월 표기 아님"]

    class Reader:
        is_encrypted = False

        def __init__(self, _stream):
            self.pages = [type("P", (), {"extract_text": lambda self, t=t: t})() for t in pages]

    monkeypatch.setattr(pypdf, "PdfReader", Reader)
    sections = picks.pdf_sections(b"%PDF-1.7 fake")
    assert [(s["month"], s["pages"]) for s in sections] == [("2026-10", 2), ("2026-09", 2)]
    assert "A000660" in sections[1]["text"] and "A035420" in sections[0]["text"]


@pytest.mark.asyncio
async def test_parse_pdf_endpoint_is_private_and_returns_sections(client, make_user):
    pdf = _ascii_pdf(["A005930"])
    assert (await client.post("/api/external-picks/parse-pdf", content=pdf, headers={"Content-Type": "application/pdf"})).status_code == 401
    me = await make_user("picks-pdf@test.com")
    ok = await client.post("/api/external-picks/parse-pdf", content=pdf, headers={**me, "Content-Type": "application/pdf"})
    assert ok.status_code == 200 and "A005930" in ok.json()["sections"][0]["text"]
    bad = await client.post("/api/external-picks/parse-pdf", content=b"hello", headers={**me, "Content-Type": "application/pdf"})
    assert bad.status_code == 422 and "PDF" in bad.json()["detail"]
