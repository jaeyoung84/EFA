#!/usr/bin/env python3
"""EINT-044: 전향 관측 등록부 점검. 형식 오류·근거 파일 누락·기한 지남이 있으면 종료 코드 1.

usage: python3 scripts/check_observation_registry.py [--today YYYY-MM-DD] [--warn-days 45]
"""
import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "backend"))
import observation_registry as reg  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--today", default=datetime.now(timezone(timedelta(hours=9))).date().isoformat())
ap.add_argument("--warn-days", type=int, default=reg.WARN_DAYS)
args = ap.parse_args()
registry = reg.load()
errors = reg.validate(registry, ROOT)
due = reg.due_items(registry, date.fromisoformat(args.today), args.warn_days)
print(f"등록부 {registry.get('updated_on')} · 활성 {len(registry.get('active_hypotheses', []))} · 참고 {len(registry.get('descriptive_observations', []))} · "
      f"시작 전 {len(registry.get('planned_not_started', []))} · 기준일 {args.today}")
for e in errors:
    print("  [형식] " + e)
for d in due:
    print(f"  [{'지남' if d['overdue'] else '임박'}] {d['date']} ({d['days_left']:+d}일) {d['kind']:6s} {d['id']} — {d['title']}")
if not errors and not due:
    print(f"  {args.warn_days}일 안에 다가오는 기한 없음")
sys.exit(1 if errors or any(d["overdue"] for d in due) else 0)
