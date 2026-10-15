"""KR-CRASH-REBOUND-v1 전향 관측 집계(사전등록 PREREGISTRATION_CRASH_REBOUND_v1.md).

판정 시점에 한투 자료를 다시 받아(ff_hist.json, 서버 `ffhist` 절차) 2026-10-15 이후 사건만 센다.
계산 정의는 rebound_study.build()와 같다.

usage: python observe.py <ff_hist.json>   → OBSERVATION.json
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rebound_study as rs  # noqa: E402

START = "2026-10-15"
FINAL_EPISODES, FINAL_DATE = 10, "2028-10-15"
MIN_N = 5


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    centre, half = (p + z * z / (2 * n)) / (1 + z * z / n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round((centre - half) * 100, 1), round((centre + half) * 100, 1)


def judge(k: int, n: int, lo: float, hi: float) -> dict:
    ci = wilson(k, n)
    if n < MIN_N:
        verdict = "보류"
    elif ci[1] < lo or ci[0] > hi:
        verdict = "틀림"
    else:
        verdict = "맞음"
    return {"k": k, "n": n, "pct": round(k / n * 100, 1) if n else None, "ci95": ci, "predicted": [lo, hi], "verdict": verdict}


def lock_ok() -> bool:
    lock = json.loads((HERE / "LOCK.json").read_text())
    return all(hashlib.sha256((HERE / f).read_bytes()).hexdigest() == h for f, h in lock["files"].items())


def main(path: str) -> None:
    d = rs.build(path)
    d = d[d.index >= START]
    a = d[d["r1"] <= -6]
    b = d[(d["r1"] <= -6) | (d["r1"] <= -4) | (d["r5_past"] <= -8)]
    done = lambda x, col: x[x[col].notna()]  # noqa: E731
    a20, a60, b20 = done(a, "f20"), done(a, "f60"), done(b, "f20")
    buy, sell = b20[b20["fut_z"] > 0], b20[b20["fut_z"] <= 0]
    p4 = {"buy": judge(int((buy.f20 > 0).sum()), len(buy), 0, 100), "sell": judge(int((sell.f20 > 0).sum()), len(sell), 0, 100)}
    if len(buy) >= MIN_N and len(sell) >= MIN_N:
        gap = abs(p4["buy"]["pct"] - p4["sell"]["pct"])
        p4.update(gap_pctp=round(gap, 1), verdict="맞음" if gap < 20 else "틀림")
    else:
        p4["verdict"] = "보류"
    episodes = rs.episodes(b.index, d) if len(b) else []
    final = len(episodes) >= FINAL_EPISODES or datetime.now().strftime("%Y-%m-%d") >= FINAL_DATE
    res = {"lock_ok": lock_ok(), "data_through": str(d.index[-1].date()) if len(d) else None,
           "events_A": [str(t.date()) for t in a.index], "events_B": [str(t.date()) for t in b.index],
           "independent_episodes_B": [str(t.date()) for t in episodes], "final_point_reached": final,
           "P1": judge(int(a20.bounce5_20.sum()), len(a20), 70, 100),
           "P2": judge(int((a20.f20 > 0).sum()), len(a20), 50, 70),
           "P3": judge(int((a60.f60 > 0).sum()), len(a60), 50, 70),
           "P4": p4,
           "P5": judge(int(b20.bounce5_20.sum()), len(b20), 60, 100),
           "events": b[["kospi", "r1", "r5_past", "fut_z", "spot_z", "f1", "f5", "f20", "f60", "bounce5_20"]].round(3)
           .reset_index().assign(date=lambda x: x["date"].dt.strftime("%Y-%m-%d")).to_dict("records"),
           "evaluated_at": datetime.now(timezone.utc).isoformat()}
    (HERE / "OBSERVATION.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: v for k, v in res.items() if k != "events"}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1])
