"""NXT 프리마켓·KRX 개장 분봉 수집 (운영 백엔드 컨테이너 안에서 실행, 조회 전용).

갭하락 날의 매도 시각 비교용 기술통계 자료다. 엔진·추천·주문과 무관하다.
한투 주식일별분봉조회(FHKST03010230): NX=NXT 08:00~09:10, J=KRX 09:00~09:30.
홈페이지와 같은 키·저장 토큰(kis_service)을 쓰고 새 토큰을 직접 발급하지 않는다.

실행: python -c "import runpy,sys; sys.argv=['pull.py','pilot','/tmp/kisnxt/out.json.gz']; runpy.run_path('/tmp/kisnxt/pull.py', run_name='__main__')"
"""
import asyncio
import gzip
import json
import os
import sys

from app.backend.kis_quotes import kis_service

PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
TR_ID = "FHKST03010230"
RATE_PER_SECOND = 6.0   # 홈페이지 프로세스(초당 3건)와 합쳐도 한투 한도 아래
CONCURRENCY = 6
HOURS = {"NX": "091000", "J": "093000"}
PILOT = {"20261015": ["005380", "005930"]}


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


async def fetch(svc, market: str, code: str, date: str, raw: bool) -> dict:
    params = {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code, "FID_INPUT_HOUR_1": HOURS[market],
              "FID_INPUT_DATE_1": date, "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": ""}
    payload: dict = {}
    for _ in range(5):
        try:
            payload = await svc._rest_get(PATH, TR_ID, params)
        except Exception as error:   # 네트워크 오류는 잠시 쉬고 재시도
            payload = {"rt_cd": "x", "msg1": str(error)[:120]}
            await asyncio.sleep(2)
            continue
        if payload.get("msg_cd") == "EGW00201":   # 초당 건수 초과
            await asyncio.sleep(1.5)
            continue
        break
    bars = [[_int(r.get("stck_cntg_hour")), _int(r.get("stck_oprc")), _int(r.get("stck_hgpr")),
             _int(r.get("stck_lwpr")), _int(r.get("stck_prpr")), _int(r.get("cntg_vol"))]
            for r in payload.get("output2") or [] if r.get("stck_bsop_date") == date]
    bars.sort()
    out = {"rt": payload.get("rt_cd"), "msg": payload.get("msg_cd") or payload.get("msg1"), "bars": bars}
    if raw:
        out["raw_output1"] = payload.get("output1")
        out["raw_first"] = (payload.get("output2") or [])[:3]
    return out


async def main(mode: str, out_path: str) -> None:
    svc = kis_service()
    if not svc.enabled:
        raise SystemExit("KIS key not configured in this container")
    svc.max_rate = svc.rate = RATE_PER_SECOND
    if mode == "pilot":
        selection = PILOT
    else:
        here = os.path.dirname(os.path.abspath(out_path))
        with open(os.path.join(here, "selection.json"), encoding="utf-8") as handle:
            selection = json.load(handle)
    jobs = [(d, c, m) for d, codes in selection.items() for c in codes for m in ("NX", "J")]
    gate = asyncio.Semaphore(CONCURRENCY)
    result: dict = {}

    async def one(date: str, code: str, market: str) -> None:
        async with gate:
            result.setdefault(date, {}).setdefault(code, {})[market] = await fetch(svc, market, code, date, mode == "pilot")

    await asyncio.gather(*(one(*job) for job in jobs))
    with gzip.open(out_path, "wt", encoding="utf-8") as handle:
        json.dump(result, handle, separators=(",", ":"))
    ok = sum(1 for d in result.values() for c in d.values() for m in c.values() if m["rt"] == "0")
    nonempty = sum(1 for d in result.values() for c in d.values() for m in c.values() if m["bars"])
    print(json.dumps({"mode": mode, "calls": len(jobs), "ok": ok, "nonempty": nonempty}))
    if mode == "pilot":
        print(json.dumps(result, ensure_ascii=False)[:20000])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
