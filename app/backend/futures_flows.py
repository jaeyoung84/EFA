"""EINT-040: 코스피200 선물·코스피 현물의 투자자별 일별 수급과 선물 일봉·미결제약정을 최근 3개월만 보관한다.

출처는 모두 한국투자증권 Open API(KRX 자료)이며 추정값은 저장하지 않는다. 주문·추천과 무관한 참고자료다.

- 투자자별 일별 수급: 시장별 투자자매매동향(일별) FHPTJ04040000. 선물 = FID_INPUT_ISCD_1 K2I / ISCD_2 F001,
  현물(코스피) = KSP / 0001. 한 번 부르면 지정일까지 약 300거래일이 온다(2026-10-15 서버 확인).
  수량은 계약(선물; 현물 수량 단위는 미확인이라 화면에 쓰지 않음), 금액은 백만 원(10/14 외국인 선물 3,557계약 = 844,524백만 원 ≈ 944 × 25만 원 × 계약).
- 선물 일봉: 선물옵션 기간별시세 FHKIF03020100, 그때의 최근월물 단축코드(현재가 조회의 futs_shrn_iscd) 기준.
  월물이 바뀌기 전 날짜도 '현재 최근월물'의 일봉이다(화면에 월물을 표시).
- 미결제약정·베이시스·코스피200: 현재가 조회 FHMIF10000000에는 과거 값이 없어 장 마감 뒤 수집 때 그날 값만 기록한다.
- 수집이 실패하거나 빈 응답이면 기존 저장분을 지우지 않는다. 보관 기간(RETAIN_DAYS)이 지난 날짜만 지운다.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    from .db import get_db
    from .kis_quotes import kis_service
except ImportError:
    from db import get_db  # type: ignore
    from kis_quotes import kis_service  # type: ignore

logger = logging.getLogger("jy_investing.futures_flows")
KST = ZoneInfo("Asia/Seoul")
COLLECTION = "market_futures_daily"
RETAIN_DAYS = 92                      # 최대 약 3개월
SOURCE = "한국투자증권 Open API(KRX)"
FLOW_PATH, FLOW_TR = "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market", "FHPTJ04040000"
PRICE_PATH, PRICE_TR = "/uapi/domestic-futureoption/v1/quotations/inquire-price", "FHMIF10000000"
CHART_PATH, CHART_TR = "/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice", "FHKIF03020100"
FRONT_MONTH = "10100000"              # 한투의 코스피200 선물 최근월물 대표 코드(krx_futures.py와 같음)
MARKETS = {"futures": ("K2I", "F001"), "spot": ("KSP", "0001")}
# 투자자 구분: 응답 접두어 → 저장 키
INVESTORS = {"frgn": "foreign", "orgn": "institution", "prsn": "individual", "scrt": "securities", "ivtr": "trust",
             "pe_fund": "private_fund", "bank": "bank", "insu": "insurance", "mrbn": "merchant_bank", "fund": "pension",
             "etc_orgt": "other_institution", "etc_corp": "other_corp"}
CLOSE_TIME = time(15, 45)             # 이 시각 뒤 수집분만 그날 미결제약정·베이시스로 기록


def _num(value) -> float | None:
    try:
        result = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _iso(raw: str) -> str | None:
    raw = str(raw or "").strip()
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 and raw.isdigit() else None


def parse_flow_rows(rows: list[dict]) -> dict[str, dict]:
    """일별 수급 응답 → {날짜: {'index': 지수 종가, 'investors': {구분: {'qty', 'amount_mn'}}}}."""
    out = {}
    for row in rows or []:
        day = _iso(row.get("stck_bsop_date"))
        if not day:
            continue
        investors = {}
        for prefix, key in INVESTORS.items():
            qty = _num(row.get(f"{prefix}_ntby_qty", row.get(f"{prefix}_ntby_vol")))
            amount = _num(row.get(f"{prefix}_ntby_tr_pbmn"))
            if qty is not None or amount is not None:
                investors[key] = {"qty": qty, "amount_mn": amount}
        if investors:
            out[day] = {"index": _num(row.get("bstp_nmix_prpr")), "index_change_pct": _num(row.get("bstp_nmix_prdy_ctrt")),
                        "investors": investors}
    return out


def parse_chart_rows(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for row in rows or []:
        day = _iso(row.get("stck_bsop_date"))
        close = _num(row.get("futs_prpr"))
        if day and close:
            out[day] = {"open": _num(row.get("futs_oprc")), "high": _num(row.get("futs_hgpr")), "low": _num(row.get("futs_lwpr")),
                        "close": close, "volume": _num(row.get("acml_vol")), "amount_won": _num(row.get("acml_tr_pbmn"))}
    return out


def parse_snapshot(payload: dict) -> dict | None:
    """현재가 조회 → 최근월물 코드·이름과 그 시점의 미결제약정·베이시스·코스피200."""
    o1, o3 = payload.get("output1") or {}, payload.get("output3") or {}
    if str(payload.get("rt_cd")) != "0" or not o1.get("futs_prpr"):
        return None
    return {"code": str(o1.get("futs_shrn_iscd") or "").strip(), "name": o1.get("hts_kor_isnm"),
            "open_interest": _num(o1.get("hts_otst_stpl_qty")), "open_interest_change": _num(o1.get("otst_stpl_qty_icdc")),
            "basis": _num(o1.get("mrkt_basis")), "theoretical_basis": _num(o1.get("basis")),
            "kospi200": _num(o3.get("bstp_nmix_prpr")), "last_trade_date": _iso(o1.get("futs_last_tr_date"))}


def merge_day(existing: dict | None, *, futures=None, spot=None, bar=None, snapshot=None, contract=None) -> dict:
    """빈 값으로 기존 값을 덮지 않는다(재수집 실패 시 기존 유지)."""
    doc = dict(existing or {})
    for key, value in (("futures_flows", futures), ("spot_flows", spot), ("futures_bar", bar), ("close_snapshot", snapshot)):
        if value:
            doc[key] = value
    if bar and contract:
        doc["bar_contract"] = contract
    return doc


async def _get(service, path: str, tr: str, params: dict) -> dict:
    payload = await service._rest_get(path, tr, params)
    if str(payload.get("rt_cd")) != "0":
        raise RuntimeError(f"{tr} {payload.get('msg_cd', '')} {payload.get('msg1', '')}".strip())
    return payload


async def collect_futures_flows(now: datetime | None = None, service=None, db=None) -> dict:
    """평일 장 마감 뒤 실행. 최근 3개월의 일별 수급·선물 일봉을 다시 받아 날짜별로 합치고, 그날 미결제약정을 기록한다."""
    now = (now or datetime.now(timezone.utc)).astimezone(KST)
    service = service or kis_service()
    if not service.enabled:
        return {"status": "skipped", "reason": "한투 키 없음"}
    db = db if db is not None else get_db()
    coll = db[COLLECTION]
    today = now.strftime("%Y%m%d")
    cutoff = (now.date() - timedelta(days=RETAIN_DAYS)).isoformat()
    parts, errors = {}, []
    for name, (iscd1, iscd2) in MARKETS.items():
        try:
            payload = await _get(service, FLOW_PATH, FLOW_TR, {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001", "FID_INPUT_DATE_1": today,
                                                              "FID_INPUT_ISCD_1": iscd1, "FID_INPUT_DATE_2": today, "FID_INPUT_ISCD_2": iscd2})
            parts[name] = parse_flow_rows(payload.get("output") or [])
        except Exception as exc:  # 한 부분이 실패해도 나머지는 저장
            errors.append(f"{name}: {str(exc)[:120]}")
            parts[name] = {}
    snapshot, bars = None, {}
    try:
        snapshot = parse_snapshot(await service._rest_get(PRICE_PATH, PRICE_TR, {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": FRONT_MONTH}))
        if snapshot and snapshot["code"]:
            chart = await _get(service, CHART_PATH, CHART_TR, {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": snapshot["code"],
                                                               "FID_INPUT_DATE_1": cutoff.replace("-", ""), "FID_INPUT_DATE_2": today,
                                                               "FID_PERIOD_DIV_CODE": "D"})
            bars = parse_chart_rows(chart.get("output2") or [])
    except Exception as exc:
        errors.append(f"futures_price: {str(exc)[:120]}")
    days = {d for d in (*parts["futures"], *parts["spot"], *bars) if d >= cutoff}
    # 미결제약정은 장 마감 뒤, 그날 수급이 이미 나온 날에만 그날 값으로 기록한다(장중 값·휴장일 기록 방지).
    today_iso = now.date().isoformat()
    close_snapshot = None
    if snapshot and now.time() >= CLOSE_TIME and today_iso in parts["futures"]:
        close_snapshot = {k: snapshot[k] for k in ("open_interest", "open_interest_change", "basis", "theoretical_basis", "kospi200")}
        close_snapshot["captured_at"] = now.isoformat()
    written = 0
    for day in sorted(days):
        existing = await coll.find_one({"_id": day}) or {}
        doc = merge_day(existing, futures=parts["futures"].get(day), spot=parts["spot"].get(day), bar=bars.get(day),
                        snapshot=close_snapshot if day == today_iso else None,
                        contract={"code": snapshot["code"], "name": snapshot["name"]} if snapshot else None)
        if doc != existing:
            doc.update(_id=day, date=day, source=SOURCE, updated_at=now.astimezone(timezone.utc))
            await coll.replace_one({"_id": day}, doc, upsert=True)
            written += 1
    pruned = (await coll.delete_many({"_id": {"$lt": cutoff}})).deleted_count
    result = {"status": "ok" if not errors else ("partial" if days else "error"), "days": len(days), "written": written,
              "pruned": pruned, "errors": errors, "open_interest_recorded": bool(close_snapshot), "at": now.isoformat()}
    await db["market_futures_runs"].insert_one({**result, "created_at": datetime.now(timezone.utc)})
    (logger.warning if errors else logger.info)("선물·수급 수집 %s", result)
    return result


async def read_futures_flows(db=None) -> dict:
    db = db if db is not None else get_db()
    rows = [doc async for doc in db[COLLECTION].find({}, {"source": 0, "updated_at": 0}).sort("_id", 1)]
    for row in rows:
        row.pop("_id", None)
    last_run = await db["market_futures_runs"].find_one({}, {"_id": 0}, sort=[("created_at", -1)])
    return {"rows": rows, "retain_days": RETAIN_DAYS, "source": SOURCE, "last_run": last_run,
            "units": {"futures_qty": "계약", "amount": "백만 원"}}   # 현물 수량 단위는 미확인이라 표시하지 않음
