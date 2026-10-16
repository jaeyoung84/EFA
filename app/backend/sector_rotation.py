"""섹터 순환(RRG) 참고 화면용 파일 읽기 (EINT-021).

엔진이 매 거래일 KRX ETF 일봉으로 계산해 상태 디렉터리에 쓰는 sector-rotation.json을 읽기만 한다.
없거나 형식이 다르면 '사용할 수 없음'과 이유를 돌려주고 값을 만들지 않는다. 매매 신호가 아니다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SCHEMA = "kqs-sector-rotation/v1"


def sector_rotation_path() -> Path:
    configured = os.getenv("KQS_SECTOR_ROTATION", "").strip()
    if configured:
        return Path(configured)
    snapshot = os.getenv("KQS_SNAPSHOT_DB", "").strip()
    if snapshot:
        return Path(snapshot).parent / "sector-rotation.json"
    return Path(__file__).resolve().parents[2] / "state/kqs/sector-rotation.json"


def read_sector_rotation(path: Path | None = None) -> dict:
    path = path or sector_rotation_path()
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"available": False, "detail": "섹터 순환 자료가 아직 없습니다. 엔진의 다음 일일 실행 후 생성됩니다."}
    except (OSError, ValueError):
        return {"available": False, "detail": "섹터 순환 자료를 읽을 수 없습니다."}
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        return {"available": False, "detail": "섹터 순환 자료 형식이 다릅니다."}
    benchmark = (report.get("benchmark") or {}).get("name", "기준 지수")
    return {
        "available": True, **{key: report[key] for key in ("as_of", "latest_week", "benchmark", "smoothing_weeks",
                                                            "tail_weeks", "quadrant_labels", "sectors", "skipped")},
        # EINT-047: 동일가중 기본 기준·KODEX 200 참고 분면·대형주 쏠림(이전 형식 파일이면 없음)
        **{key: report.get(key) for key in ("reference_benchmark", "concentration", "benchmark_return_1w_pct",
                                            "benchmark_return_4w_pct")},
        "disclaimer": (f"국내 섹터 ETF를 {benchmark}와 비교한 주간 상대강도 그림(RRG)입니다. 지금 어느 섹터가 보통 종목보다 강하고 "
                       "그 힘이 커지는지 보여 주는 참고 자료이며, 매수·매도 신호나 미래 수익 예측이 아닙니다."),
    }
