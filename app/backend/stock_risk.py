"""EINT-025: 추천 종목의 위험 공시·시장 경고 표시(참고용).

추천 규칙·순위·청산·계약과 무관하게, 판단 전에 확인해야 할 위험 신호를 모아 보여 준다.
- DART 최근 30일 공시 제목에서 위험 유형(상장폐지·관리종목·거래정지·횡령배임·회생·감자·불성실공시·
  실질심사·감사의견 비적정 / 유상증자·CB·BW·EB·최대주주 변경·소송·조회공시)을 찾는다.
- 한투 현재가 응답의 종목 상태(관리종목·투자위험·투자경고·투자주의·단기과열·투자유의·거래정지·정리매매)를 읽는다.
값을 추정하지 않는다. 조회하지 못한 출처는 '확인 실패'로 따로 표시한다. 매수·매도 권유가 아니다.
"""
from __future__ import annotations

import re
from datetime import date

DISCLOSURE_WINDOW_DAYS = 30
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# (심각도, 표시 이름, 찾을 문구들[, 있으면 건너뛸 문구들[, 하나는 꼭 있어야 할 문구들]]) — 공백을 지우고 비교.
# 위에서부터 먼저 맞는 것 하나만 쓴다. 실데이터(2026-10-02, 코스닥 60종목) 확인으로 우려·병합분할·종속회사를 구분했다.
DISCLOSURE_RULES: list[tuple] = [
    ("high", "상장폐지 관련", ("상장폐지",)),
    ("high", "상장적격성 실질심사", ("상장적격성", "실질심사")),
    ("high", "관리종목 지정 우려", ("관리종목지정우려",)),
    ("high", "관리종목 지정", ("관리종목",)),
    ("caution", "매매거래 정지(병합·분할)", ("매매거래정지", "거래정지"), (), ("병합", "분할")),
    ("high", "매매거래 정지", ("매매거래정지", "거래정지")),
    ("high", "횡령·배임", ("횡령", "배임")),
    ("high", "회생·파산 절차", ("회생절차", "파산")),
    ("high", "감자 결정", ("감자결정",)),
    ("high", "불성실공시법인 지정", ("불성실공시",)),
    ("high", "감사의견 비적정", ("의견거절", "부적정의견", "한정의견", "감사의견비적정")),
    ("high", "투자주의환기종목 지정", ("투자주의환기종목",)),
    ("caution", "전환사채(CB) 발행", ("전환사채권발행결정",), ("종속회사",)),
    ("caution", "신주인수권부사채(BW) 발행", ("신주인수권부사채권발행결정",), ("종속회사",)),
    ("caution", "교환사채(EB) 발행", ("교환사채권발행결정",), ("종속회사",)),
    ("caution", "유상증자", ("유상증자결정", "유무상증자결정", "증권신고서(지분증권)"), ("종속회사",)),
    ("caution", "최대주주 변경 관련", ("최대주주변경",)),
    ("caution", "소송 제기", ("소송등의제기",)),
    ("caution", "조회공시 요구", ("조회공시요구",)),
]

# 한투 주식현재가(FHKST01010100) 응답의 종목 상태 필드.
STATUS_CODE_FLAGS = {"51": ("high", "관리종목"), "52": ("high", "투자위험"), "53": ("high", "투자경고"),
                     "54": ("caution", "투자주의"), "58": ("high", "거래정지"), "59": ("caution", "단기과열")}
WARN_CODE_FLAGS = {"01": ("caution", "투자주의"), "02": ("high", "투자경고"), "03": ("high", "투자위험")}
YN_FLAGS = {"mang_issu_cls_code": ("high", "관리종목"), "temp_stop_yn": ("high", "거래정지"),
            "sltr_yn": ("high", "정리매매"), "short_over_yn": ("caution", "단기과열"),
            "invt_caful_yn": ("caution", "투자유의")}
SEVERITY_ORDER = {"high": 2, "caution": 1}


def classify_disclosure(report_nm: str) -> tuple[str, str] | None:
    """공시 제목 → (심각도, 표시 이름). 위험 유형이 아니면 None. 무상증자만은 위험으로 보지 않는다."""
    text = re.sub(r"\s+", "", str(report_nm or ""))
    if "해제" in text:   # 지정 해제·거래정지 해제 등은 위험이 끝났다는 공시다
        return None
    for severity, label, needles, *rest in DISCLOSURE_RULES:
        excludes = rest[0] if rest else ()
        requires = rest[1] if len(rest) > 1 else ()
        if (any(needle in text for needle in needles) and not any(word in text for word in excludes)
                and (not requires or any(word in text for word in requires))):
            return severity, label
    return None


def disclosure_flags(items: list[dict]) -> list[dict]:
    """위험 공시 목록. 같은 날 같은 유형(원본과 정정 공시)은 한 번만 센다."""
    flags, seen = [], set()
    for item in items:
        found = classify_disclosure(item.get("report_nm", ""))
        if not found:
            continue
        severity, label = found
        rcept_dt = str(item.get("rcept_dt") or "")
        if (label, rcept_dt) in seen:
            continue
        seen.add((label, rcept_dt))
        flags.append({
            "source": "dart", "severity": severity, "label": label,
            "title": re.sub(r"\s+", " ", str(item.get("report_nm") or "")).strip(),
            "date": f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}" if len(rcept_dt) == 8 else None,
            "url": DART_VIEW_URL.format(rcept_no=item["rcept_no"]) if item.get("rcept_no") else None,
        })
    return flags


def market_status_flags(output: dict) -> list[dict]:
    """한투 종목 상태 → 경고 목록(같은 이름은 한 번만)."""
    found: dict[str, str] = {}

    def add(severity: str, label: str) -> None:
        if SEVERITY_ORDER[severity] > SEVERITY_ORDER.get(found.get(label, ""), 0):
            found[label] = severity

    if output.get("iscd_stat_cls_code") in STATUS_CODE_FLAGS:
        add(*STATUS_CODE_FLAGS[output["iscd_stat_cls_code"]])
    if output.get("mrkt_warn_cls_code") in WARN_CODE_FLAGS:
        add(*WARN_CODE_FLAGS[output["mrkt_warn_cls_code"]])
    for field, flag in YN_FLAGS.items():
        if str(output.get(field) or "").upper() == "Y":
            add(*flag)
    return [{"source": "krx", "severity": severity, "label": label, "title": f"거래소 지정 상태: {label}"}
            for label, severity in found.items()]


def risk_summary(symbol: str, *, disclosures: list[dict] | None, status_output: dict | None,
                 errors: dict[str, str], checked_on: date) -> dict:
    """한 종목의 위험 요약. disclosures/status_output이 None이면 그 출처는 확인하지 못한 것이다."""
    flags = market_status_flags(status_output) if status_output is not None else []
    flags += disclosure_flags(disclosures) if disclosures is not None else []
    flags.sort(key=lambda flag: (-SEVERITY_ORDER[flag["severity"]], flag["source"] != "krx", -(int((flag.get("date") or "0").replace("-", "")))))
    level = "high" if any(f["severity"] == "high" for f in flags) else "caution" if flags else "none"
    checked = [name for name, value in (("dart", disclosures), ("krx", status_output)) if value is not None]
    return {"symbol": symbol, "level": level, "flags": flags, "checked": checked, "errors": errors,
            "window_days": DISCLOSURE_WINDOW_DAYS, "checked_on": checked_on.isoformat()}
