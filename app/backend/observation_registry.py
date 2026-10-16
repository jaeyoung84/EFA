"""EINT-044: 전향 관측 등록부(docs/engine/observation_registry.json, Codex CODX-OBS-001)의 기한 점검.

등록부의 날짜(다음 무결성 점검일·중간 보고일·판정 시한)를 매번 읽어 45일 안이면 주의, 지났으면 오류로 알린다.
홈페이지 상단 알림(엔진 상태)과 `scripts/check_observation_registry.py`가 같은 함수를 쓴다. 판정·규칙은 바꾸지 않는다.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[2] / "docs" / "engine" / "observation_registry.json"
WARN_DAYS = 45
KST = timezone(timedelta(hours=9))
SECTIONS = ("active_hypotheses", "descriptive_observations", "planned_not_started", "explicitly_not_active")
DATE_FIELDS = {"next_required_review_on": "무결성 점검", "interim_report_on": "중간 보고", "decision_deadline_on": "판정 시한"}
REQUIRED = {
    "active_hypotheses": ("id", "title", "status", "maturity", "decision_deadline_on", "deadline_result_if_immature",
                          "decision_rule", "next_required_review_on", "review_scope", "source_paths", "application_policy"),
    "descriptive_observations": ("id", "title", "status", "next_required_review_on", "review_cadence_months", "review_scope",
                                 "source_paths", "application_policy"),
    "planned_not_started": ("id", "title", "status", "activation_requirement", "source_paths"),
    "explicitly_not_active": ("id", "reason", "source_paths"),
}


def load(path: Path | None = None) -> dict:
    return json.loads((path or REGISTRY).read_text(encoding="utf-8"))


def validate(registry: dict, root: Path | None = None) -> list[str]:
    """형식 오류 목록(빈 목록이면 정상). root를 주면 source_paths 파일이 실제로 있는지도 본다."""
    errors, seen = [], set()
    if registry.get("schema") != "kqs-observation-registry/v1":
        errors.append("schema가 kqs-observation-registry/v1이 아님")
    for section in SECTIONS:
        for item in registry.get(section, []):
            ident = item.get("id", "?")
            if ident in seen:
                errors.append(f"{ident}: id 중복")
            seen.add(ident)
            errors += [f"{ident}: {field} 없음" for field in REQUIRED[section] if item.get(field) in (None, "", [])
                       and not (field == "started_on")]
            for field in (*DATE_FIELDS, "started_on", "event_cutoff_on"):
                value = item.get(field)
                if value is not None:
                    try:
                        date.fromisoformat(value)
                    except (TypeError, ValueError):
                        errors.append(f"{ident}: {field} 날짜 형식 오류({value})")
            if root is not None:
                errors += [f"{ident}: 근거 파일 없음 {p}" for p in item.get("source_paths", []) if not (root / p).exists()]
    return errors


def due_items(registry: dict, today: date, warn_days: int = WARN_DAYS) -> list[dict]:
    """활성·참고 관측의 날짜 중 warn_days 안에 오거나 이미 지난 것. 지난 것(overdue)이 먼저, 그다음 가까운 순."""
    out = []
    for section in ("active_hypotheses", "descriptive_observations"):
        for item in registry.get(section, []):
            for field, label in DATE_FIELDS.items():
                value = item.get(field)
                if not value:
                    continue
                days = (date.fromisoformat(value) - today).days
                if days <= warn_days:
                    out.append({"id": item["id"], "title": item.get("title", ""), "kind": label, "field": field,
                                "date": value, "days_left": days, "overdue": days < 0})
    return sorted(out, key=lambda x: (not x["overdue"], x["days_left"]))


def observation_alerts(now: datetime, path: Path | None = None) -> list[dict]:
    """엔진 상태 알림 형식({level, code, title, message}). 등록부를 못 읽으면 경고 1건."""
    try:
        registry = load(path)
    except (OSError, ValueError):
        return [{"level": "warning", "code": "observation_registry_unreadable", "title": "관측 등록부를 읽을 수 없습니다",
                 "message": "docs/engine/observation_registry.json을 확인하세요. 관측 기한 알림이 꺼진 상태입니다."}]
    alerts = []
    for item in due_items(registry, now.astimezone(KST).date()):
        when = f"{item['date']}({'지남 ' + str(-item['days_left']) + '일' if item['overdue'] else 'D-' + str(item['days_left'])})"
        alerts.append({"level": "error" if item["overdue"] else "warning", "code": f"observation_{item['field']}:{item['id']}",
                       "title": f"관측 {item['kind']} {'기한 지남' if item['overdue'] else '임박'} — {item['id']}",
                       "message": f"{item['title']} · {item['kind']} {when}. 처리 뒤 등록부 날짜와 근거를 갱신하세요(시한은 연장 금지)."})
    return alerts
