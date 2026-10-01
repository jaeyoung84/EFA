"""추천 종목의 업종 → 섹터 순환(RRG) 섹터 연결 (EINT-022).

DART 기업개황(company.json)의 업종코드(induty_code, 한국표준산업분류 KSIC)를 섹터 순환 화면의 섹터로
옮긴다. 가장 길게 일치하는 코드가 이긴다(예: 2612 반도체 > 26 전자부품). 표에 없는 업종은 섹터를 정하지
않고 '분류 없음'으로 둔다(추측하지 않음). 추천·순위·청산에는 쓰지 않는 참고 표시다.
"""
from __future__ import annotations

# (KSIC 코드 접두어, 섹터 키). 섹터 키는 엔진 kqs_collection.sector_rotation.SECTORS와 같다.
KSIC_SECTORS: tuple[tuple[str, str], ...] = (
    ("10", "staples"), ("11", "staples"), ("12", "staples"),                      # 식료품·음료·담배
    ("13", "discretionary"), ("14", "discretionary"), ("15", "discretionary"),    # 섬유·의복·가죽
    ("19", "energy_chem"), ("20", "energy_chem"), ("22", "energy_chem"),          # 석유정제·화학·고무플라스틱
    ("20423", "cosmetics"),                                                       # 화장품 제조
    ("21", "healthcare"), ("271", "healthcare"), ("86", "healthcare"),            # 의약품·의료기기·보건
    ("7011", "healthcare"),                                                       # 자연과학 연구개발(상장사는 대부분 바이오)
    ("24", "steel"), ("25", "steel"),                                             # 1차 금속·금속가공
    ("252", "defense"), ("313", "defense"),                                       # 무기·총포탄, 항공기·우주선
    ("261", "semis"),                                                             # 반도체
    ("26", "it_hw"), ("272", "it_hw"), ("273", "it_hw"), ("274", "it_hw"),        # 전자부품·컴퓨터·통신·영상기기, 측정·광학
    ("282", "battery"),                                                           # 일차전지·축전지
    ("28", "machinery"), ("29", "machinery"), ("312", "machinery"), ("319", "machinery"),  # 전기장비·기타기계·철도·기타운송장비
    ("30", "auto"),                                                               # 자동차·부품
    ("311", "shipbuilding"),                                                      # 선박
    ("32", "discretionary"), ("33", "discretionary"),                             # 가구·기타 제품
    ("41", "construction"), ("42", "construction"),                               # 건설
    ("45", "discretionary"), ("46", "discretionary"), ("47", "discretionary"),    # 도소매
    ("4711", "staples"),                                                          # 종합 소매(대형마트·편의점)
    ("49", "transport"), ("50", "transport"), ("51", "transport"), ("52", "transport"),  # 운송·창고
    ("55", "discretionary"), ("56", "discretionary"), ("75", "discretionary"), ("91", "discretionary"),  # 숙박·음식·여행·오락
    ("581", "media"), ("59", "media"), ("60", "media"),                           # 출판·영상·음향·방송
    ("582", "it_sw"), ("62", "it_sw"), ("63", "it_sw"),                           # 소프트웨어·프로그래밍·정보서비스
    ("631", "internet"),                                                          # 자료처리·호스팅·포털
    ("641", "banks"), ("65", "insurance"), ("661", "brokers"), ("6612", "brokers"),  # 은행·보험·증권
)

SECTOR_NAMES = {
    "semis": "반도체", "it_hw": "IT하드웨어", "battery": "2차전지", "it_sw": "소프트웨어", "internet": "인터넷",
    "auto": "자동차", "shipbuilding": "조선", "defense": "방산", "machinery": "기계장비", "construction": "건설",
    "steel": "철강", "energy_chem": "에너지화학", "banks": "은행", "brokers": "증권", "insurance": "보험",
    "healthcare": "헬스케어", "staples": "필수소비재", "discretionary": "경기소비재", "cosmetics": "화장품",
    "media": "미디어·엔터", "transport": "운송",
}
QUADRANT_LABELS = {"leading": "주도", "weakening": "약화", "lagging": "부진", "improving": "개선"}


def sector_for_ksic(code: str | None) -> str | None:
    """가장 길게 일치하는 접두어의 섹터 키. 없으면 None."""
    code = str(code or "").strip()
    best = None
    for prefix, key in KSIC_SECTORS:
        if code.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, key)
    return best[1] if best else None


def sector_status(symbol: str, ksic: str | None, rotation: dict) -> dict:
    """종목 하나의 섹터와 섹터 순환 현재 구역. rotation은 read_sector_rotation() 결과."""
    key = sector_for_ksic(ksic)
    item = {"symbol": symbol, "ksic": ksic or None, "sector_key": key, "sector_name": SECTOR_NAMES.get(key) if key else None,
            "quadrant": None, "quadrant_label": None, "quadrant_4w_ago": None, "rotation_as_of": rotation.get("latest_week")}
    if not key:
        item["note"] = "업종 분류 없음" if ksic else "업종 정보 없음"
        return item
    sector = next((s for s in rotation.get("sectors", []) if s.get("key") == key), None) if rotation.get("available") else None
    if not sector:
        item["note"] = "섹터 순환 자료 없음"
        return item
    item.update(quadrant=sector["quadrant"], quadrant_label=QUADRANT_LABELS.get(sector["quadrant"]),
                quadrant_4w_ago=sector.get("quadrant_4w_ago"), relative_4w_pct=sector.get("relative_4w_pct"))
    return item
