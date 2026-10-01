"""Engine pipeline health for the website (EINT-020).

Reuses the engine's own monitor (``kqs_ops.status.check_health``: is the latest expected KRX
session published, with a grace period after the data becomes due) and adds what the user should
notice on the site: a failed last run, a stale run log, and failed side steps (instrument catalog,
recommendation outcomes). Read-only on the state directory; it never runs or retries the pipeline.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kqs_ops.status import check_health

SUCCESS = {"published", "already_published"}
STALE_RUN_AFTER = timedelta(hours=30)
SIDE_STEPS = {"instrument_catalog": "종목명 검색 목록", "recommendation_outcomes": "추천 성과"}
RUN_STATUS_LABELS = {
    "collect_failed": "KRX 시세 수집 실패", "coverage_rejected": "수집 범위 검사 불통과",
    "quality_rejected": "시세 품질 검사 불통과", "blocked_quality": "시세 품질 검사 불통과",
    "publish_failed": "추천 게시 실패", "writer_busy": "다른 게시 작업과 겹침", "failed": "실행 실패",
}


def state_dir() -> Path:
    snapshot = os.getenv("KQS_SNAPSHOT_DB", "").strip()
    if snapshot:
        return Path(snapshot).parent
    return Path(__file__).resolve().parents[2] / "state/kqs"


def _parse(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def pipeline_health(directory: Path | None = None, *, now: datetime | None = None) -> dict:
    directory = directory or state_dir()
    now = now or datetime.now(timezone.utc)
    try:
        report = check_health(directory, now=now)
    except (OSError, ValueError) as exc:
        report = {"healthy": False, "reason": f"상태를 확인할 수 없습니다: {exc}"}
    alerts = []
    if not report.get("healthy"):
        alerts.append({"level": "error", "code": "publication_missing", "title": "오늘 추천이 게시되지 않았습니다",
                       "message": (f"기대 기준일 {report.get('expected_session') or '-'}, 마지막 게시 "
                                   f"{report.get('published_session') or '없음'}. 화면의 추천은 최신이 아닐 수 있습니다.")})

    last = {}
    status_file = directory / "pipeline-status.json"
    try:
        last = json.loads(status_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        alerts.append({"level": "warning", "code": "run_log_missing", "title": "엔진 실행 기록이 없습니다",
                       "message": "엔진 일일 실행 상태 파일을 찾지 못했습니다."})
    except (OSError, ValueError):
        alerts.append({"level": "warning", "code": "run_log_unreadable", "title": "엔진 실행 기록을 읽을 수 없습니다",
                       "message": "엔진 일일 실행 상태 파일이 손상됐거나 권한이 없습니다."})
    if last:
        status = last.get("status")
        if status not in SUCCESS:
            alerts.append({"level": "error" if not report.get("healthy") else "warning", "code": "last_run_failed",
                           "title": f"최근 엔진 실행: {RUN_STATUS_LABELS.get(status, status or '알 수 없음')}",
                           "message": (f"기준일 {last.get('session') or '-'} 실행이 끝나지 않았습니다. "
                                       "다음 예약 실행(08:30·10:30 KST)에서 다시 시도합니다.")})
        finished = _parse(last.get("finished_at"))
        if finished is None or now - finished > STALE_RUN_AFTER:
            alerts.append({"level": "warning", "code": "run_log_stale", "title": "엔진이 최근에 실행되지 않았습니다",
                           "message": "마지막 실행이 30시간보다 오래됐습니다. 서버 예약 실행(타이머)을 확인하세요."})
        for step, label in SIDE_STEPS.items():
            result = (last.get("steps") or {}).get(step) or {}
            if result.get("status") == "failed":
                alerts.append({"level": "warning", "code": f"{step}_failed", "title": f"{label} 갱신 실패",
                               "message": "추천 게시는 정상이지만 이 정보는 이전 값일 수 있습니다."})

    level = "error" if any(a["level"] == "error" for a in alerts) else "warning" if alerts else "ok"
    return {
        "level": level, "alerts": alerts, "checked_at": now.isoformat(),
        "expected_session": report.get("expected_session"), "published_session": report.get("published_session"),
        "last_run_status": last.get("status"), "last_run_session": last.get("session"),
        "last_run_finished_at": last.get("finished_at"),
    }
