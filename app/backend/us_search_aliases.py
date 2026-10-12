"""미국 종목 한글 검색 별칭과 포트폴리오 입력용 검색 결과 정리.

Yahoo 자동완성은 한글 회사명을 찾지 못하므로 자주 쓰는 미국 종목·ETF를 한글 이름으로 보완한다.
포트폴리오 입력(scope=holdings)에서는 보유 기록에 맞지 않는 선물·지수·암호화폐·해외 상장 CDR을 빼고,
입력과 정확히 맞는 종목(티커, 한글 별칭, 법인 접미어를 뗀 회사명)을 맨 앞에 두며 exact 표시를 붙인다.
"""
from __future__ import annotations

import re

# 한글 별칭 -> (티커, 표시 이름). 티커는 Yahoo 표기(클래스 주식은 BRK-B처럼 '-')를 쓴다.
US_KOREAN_ALIASES: dict[str, tuple[str, str]] = {
    "애플": ("AAPL", "Apple Inc."), "엔비디아": ("NVDA", "NVIDIA Corporation"), "테슬라": ("TSLA", "Tesla, Inc."),
    "마이크로소프트": ("MSFT", "Microsoft Corporation"), "마소": ("MSFT", "Microsoft Corporation"),
    "아마존": ("AMZN", "Amazon.com, Inc."), "알파벳": ("GOOGL", "Alphabet Inc."), "구글": ("GOOGL", "Alphabet Inc."),
    "메타": ("META", "Meta Platforms, Inc."), "페이스북": ("META", "Meta Platforms, Inc."), "넷플릭스": ("NFLX", "Netflix, Inc."),
    "브로드컴": ("AVGO", "Broadcom Inc."), "AMD": ("AMD", "Advanced Micro Devices, Inc."), "인텔": ("INTC", "Intel Corporation"),
    "퀄컴": ("QCOM", "QUALCOMM Incorporated"), "TSMC": ("TSM", "Taiwan Semiconductor Manufacturing"),
    "마이크론": ("MU", "Micron Technology, Inc."), "ASML": ("ASML", "ASML Holding N.V."), "ARM": ("ARM", "Arm Holdings plc"),
    "팔란티어": ("PLTR", "Palantir Technologies Inc."), "오라클": ("ORCL", "Oracle Corporation"),
    "세일즈포스": ("CRM", "Salesforce, Inc."), "어도비": ("ADBE", "Adobe Inc."), "슈퍼마이크로": ("SMCI", "Super Micro Computer, Inc."),
    "델": ("DELL", "Dell Technologies Inc."), "아이온큐": ("IONQ", "IonQ, Inc."), "리게티": ("RGTI", "Rigetti Computing, Inc."),
    "코인베이스": ("COIN", "Coinbase Global, Inc."), "로빈후드": ("HOOD", "Robinhood Markets, Inc."),
    "스트래티지": ("MSTR", "Strategy Inc"), "마이크로스트래티지": ("MSTR", "Strategy Inc"),
    "우버": ("UBER", "Uber Technologies, Inc."), "에어비앤비": ("ABNB", "Airbnb, Inc."), "쇼피파이": ("SHOP", "Shopify Inc."),
    "버크셔해서웨이": ("BRK-B", "Berkshire Hathaway Inc."), "버크셔": ("BRK-B", "Berkshire Hathaway Inc."),
    "JP모건": ("JPM", "JPMorgan Chase & Co."), "비자": ("V", "Visa Inc."), "마스터카드": ("MA", "Mastercard Incorporated"),
    "월마트": ("WMT", "Walmart Inc."), "코스트코": ("COST", "Costco Wholesale Corporation"), "코카콜라": ("KO", "The Coca-Cola Company"),
    "펩시": ("PEP", "PepsiCo, Inc."), "맥도날드": ("MCD", "McDonald's Corporation"), "스타벅스": ("SBUX", "Starbucks Corporation"),
    "나이키": ("NKE", "NIKE, Inc."), "디즈니": ("DIS", "The Walt Disney Company"), "보잉": ("BA", "The Boeing Company"),
    "일라이릴리": ("LLY", "Eli Lilly and Company"), "노보노디스크": ("NVO", "Novo Nordisk A/S"), "화이자": ("PFE", "Pfizer Inc."),
    "존슨앤존슨": ("JNJ", "Johnson & Johnson"), "유나이티드헬스": ("UNH", "UnitedHealth Group Incorporated"),
    "엑슨모빌": ("XOM", "Exxon Mobil Corporation"), "셰브론": ("CVX", "Chevron Corporation"),
    "S&P500 ETF": ("SPY", "SPDR S&P 500 ETF Trust"), "나스닥100 ETF": ("QQQ", "Invesco QQQ Trust"),
    "뱅가드 S&P500": ("VOO", "Vanguard S&P 500 ETF"), "슈드": ("SCHD", "Schwab U.S. Dividend Equity ETF"),
    "제피": ("JEPI", "JPMorgan Equity Premium Income ETF"), "티큐": ("TQQQ", "ProShares UltraPro QQQ"),
    "속슬": ("SOXL", "Direxion Daily Semiconductor Bull 3X"), "유프로": ("UPRO", "ProShares UltraPro S&P500"),
}

HOLDING_TYPES = {"EQUITY", "ETF"}
_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "holdings", "holding",
             "group", "nv", "sa", "ag", "the", "class", "a", "b", "com"}


def base_name(value: str) -> str:
    """'Tesla, Inc.' -> 'tesla', 'Amazon.com, Inc.' -> 'amazon'. 법인 접미어와 기호를 뗀 비교용 이름."""
    words = re.sub(r"[^0-9a-z가-힣&]+", " ", str(value or "").lower()).split()
    return "".join(word for word in words if word not in _SUFFIXES)


def alias_items(query: str) -> list[dict]:
    """한글 별칭 검색(입력이 별칭에 포함될 때). 같은 티커는 한 번만."""
    lowered, out, seen = query.strip().lower().replace(" ", ""), [], set()
    if not lowered or (lowered.isascii() and len(lowered) < 2):
        return out
    # 입력과 똑같은 별칭을 먼저 본다('버크셔'가 '버크셔해서웨이'보다 앞서 정확 일치로 표시되게).
    ordered = sorted(US_KOREAN_ALIASES.items(), key=lambda kv: kv[0].lower().replace(" ", "") != lowered)
    for alias, (ticker, name) in ordered:
        # 입력이 별칭의 앞부분이거나 별칭 안에 있을 때만(별칭이 입력 속에 있다고 고르지 않는다: '델타항공'의 '델')
        if lowered in alias.lower().replace(" ", "") and ticker not in seen:
            seen.add(ticker)
            out.append({"ticker": ticker, "name": name, "exchange": "US", "source": "us_alias", "quote_type": "EQUITY",
                        "alias": alias})
    return out


def holding_candidate(item: dict) -> bool:
    """보유 기록에 쓸 수 있는 종목만: 국내(.KS/.KQ) 또는 미국 주식·ETF(선물·지수·해외 상장 제외)."""
    ticker = str(item.get("ticker") or "").upper()
    if re.fullmatch(r"\d{6}\.(KS|KQ)", ticker):
        return True
    if any(mark in ticker for mark in ("=", "^")) or "." in ticker:
        return False
    return item.get("quote_type", "EQUITY") in HOLDING_TYPES


def mark_and_rank(query: str, items: list[dict]) -> list[dict]:
    """정확히 맞는 항목에 exact=True를 붙이고 정확 > 주식 > ETF 순으로 둔다(같은 순위는 원래 순서)."""
    q_upper, q_base, q_plain = query.strip().upper(), base_name(query), query.strip().lower().replace(" ", "")
    ranked = []
    for index, item in enumerate(items):
        alias = str(item.get("alias") or "").lower().replace(" ", "")
        exact = (str(item["ticker"]).upper() == q_upper or (q_base and base_name(item["name"]) == q_base)
                 or (alias and alias == q_plain) or str(item["name"]).lower().replace(" ", "") == q_plain)
        kind = 0 if exact else (1 if item.get("quote_type", "EQUITY") == "EQUITY" else 2)
        ranked.append((kind, index, {**item, "exact": bool(exact)}))
    return [item for _, _, item in sorted(ranked, key=lambda row: (row[0], row[1]))]
