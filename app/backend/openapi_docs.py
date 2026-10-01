"""OpenAPI metadata used by the interactive Swagger UI.

Keeping this separately from the route handlers makes the API guide readable
without changing the response payloads consumed by the frontend.
"""

from __future__ import annotations

from fastapi.openapi.utils import get_openapi


TAG_DESCRIPTIONS = [
    {"name": "시스템", "description": "서비스 상태와 접속 현황 API입니다."},
    {"name": "DART·기업", "description": "OpenDART 공시와 시장 데이터로 기업을 찾고 재무를 분석합니다. DART 키가 필요한 API는 키가 없으면 503을 반환합니다."},
    {"name": "산업·시장", "description": "실제 시세 기반의 시장·섹터·거시 지표를 조회합니다. 시세를 가져오지 못하면 가짜 데이터 대신 503과 실패 원인을 반환합니다."},
]

# Swagger UI는 외부 개발자용 전체 서버 기능 목록이 아니라, 이 웹앱 화면이
# 실제 호출하는 API만 안내합니다. 운영·관리 스크립트용 또는 아직 화면에 연결되지
# 않은 엔드포인트는 서버에 유지하되 OpenAPI 문서에서는 노출하지 않습니다.
FRONTEND_API_PATHS = frozenset({
    "/api/health",
    "/api/system/resources",
    "/api/visitors/heartbeat",
    "/api/dart/company-search",
    "/api/dart/financial-analysis",
    "/api/finance/company-financials",
    "/api/industry/sector",
    "/api/industry/peer",
    "/api/market/snapshot",
    "/api/market/top-gainers",
    "/api/market/top-sobujang",
    "/api/macro/realtime",
    "/api/home/market-candle",
    "/api/market/instruments",
})


# (tag, Korean title, implementation/response contract).  Request models already
# expose type, defaults and validation limits; these notes explain why to call
# each endpoint and how to interpret its returned data.
OPERATION_DOCS: dict[str, tuple[str, str, str]] = {
    "/api/health": ("시스템", "서비스 상태 확인", "서버가 요청을 처리할 수 있는지 확인합니다. `status: ok`면 정상입니다."),
    "/api/system/resources": ("시스템", "서버 리소스 사용량", "서버의 CPU, 메모리, 루트 디스크 사용률과 바이트 단위 사용량을 반환합니다. 운영 상태를 살피는 용도이며 컨테이너·호스트 환경에 따라 관측 범위가 달라질 수 있습니다."),
    "/api/visitors/heartbeat": ("시스템", "활성 브라우저 하트비트", "익명 브라우저 식별자를 갱신하고 최근 90초 안에 신호를 보낸 활성 브라우저 수를 반환합니다. 분석용 방문 이력을 저장하지 않습니다."),
    "/api/dart/company-search": ("DART·기업", "DART 기업명 검색", "회사명 일부를 기준으로 DART 기업코드 목록을 검색합니다. 결과의 `corp_code`는 재무분석 요청에 사용합니다."),
    "/api/dart/financial-analysis": ("DART·기업", "DART 재무제표 분석", "DART 고유번호와 보고서 코드를 사용해 핵심 재무 항목, 비율 및 차트를 계산합니다. `corp_code`는 8자리여야 합니다."),
    "/api/finance/company-financials": ("DART·기업", "상장사 재무정보 조회", "Yahoo Finance 기준 종목의 손익·재무상태·현금흐름 데이터를 연간 또는 분기별로 정리합니다. 지원되지 않는 티커 또는 공급자 오류는 502/404가 될 수 있습니다."),
    "/api/industry/sector": ("산업·시장", "섹터 수익률 비교", "ETF/지수 티커와 기간을 기준으로 수익률 비교 데이터를 조회하고 시각화 이미지를 반환합니다."),
    "/api/industry/peer": ("산업·시장", "동종기업 비교", "표시명과 티커의 매핑을 받아 경쟁사 가격·수익률 비교 결과를 반환합니다."),
    "/api/market/snapshot": ("산업·시장", "시장 스냅샷", "지정한 지수·환율 티커의 최신 가격, 변동률과 조회 시각을 반환합니다. 시장이 닫혀 있으면 마지막 거래 기준일 수 있습니다."),
    "/api/market/top-gainers": ("산업·시장", "금일 상승종목", "KOSPI 대표 종목 표본을 전일 종가 대비 등락률 순으로 반환합니다. 전체 시장 스크리너가 아닙니다."),
    "/api/market/top-sobujang": ("산업·시장", "금일 소부장 종목", "국내 반도체 소재·부품·장비 대표 종목 표본을 전일 종가 대비 등락률 순으로 반환합니다. 전체 소부장 시장을 포괄하지 않습니다."),
    "/api/macro/realtime": ("산업·시장", "거시지표 시계열 조회", "금리·원유·주가지수 등 티커의 지정 기간 시계열과 비교 차트를 반환합니다. 외부 시세 공급자 지연이 반영될 수 있습니다."),
    "/api/market/instruments": ("산업·시장", "국내 종목명 검색", "종목명·별칭·6자리 코드로 KOSPI/KOSDAQ 현재 상장 종목을 찾습니다. 출처는 엔진이 매 거래일 게시하는 KRX 상장 목록이며, 시가총액이 큰 종목이 먼저 나옵니다."),
    "/api/home/market-candle": ("산업·시장", "시장 캔들", "대표 시장·종목의 OHLCV 캔들을 반환합니다. 시세를 가져오지 못하면 가짜 캔들 대신 503과 원인·조회 시각을 반환합니다."),
}


def install_openapi(app) -> None:
    """Install a cached OpenAPI factory after all routers have been registered."""
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title="EFA API",
            version="2.0.0",
            summary="투자 판단 보조 API",
            description=(
                "## 사용 안내\n\n"
                "KQS 추천·보유자산·시장 데이터를 조회하는 API입니다. 모든 시세는 외부 공급자 상태에 따라 지연되거나 달라질 수 있으며, "
                "분석 결과는 투자 권유가 아닙니다.\n\n"
                "### 공통 규칙\n\n"
                "- 요청 본문의 필수 여부, 기본값, 범위와 정규식은 각 Schema에서 확인합니다.\n"
                "- 검증에 실패하면 `422`와 필드별 오류가 반환됩니다. 외부 데이터·저장소 의존 API는 연결 실패 시 주로 `502` 또는 `503`을 반환합니다.\n"
                "- 차트는 `data:image/png;base64,...` 또는 base64 문자열로 반환될 수 있습니다. 프런트엔드에서는 이미지 `src`에 그대로 넣어 표시합니다.\n"
                "- 별도 인증은 현재 요구되지 않습니다."
            ),
            routes=app.routes,
            tags=TAG_DESCRIPTIONS,
        )
        schema["paths"] = {
            path: operations
            for path, operations in schema.get("paths", {}).items()
            if path in FRONTEND_API_PATHS
        }
        for path, operations in schema.get("paths", {}).items():
            metadata = OPERATION_DOCS.get(path)
            if not metadata:
                continue
            tag, summary, description = metadata
            for method, operation in operations.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                operation["tags"] = [tag]
                operation["summary"] = summary
                operation["description"] = description + "\n\n**공통 오류:** 요청값 검증 실패 시 `422`입니다."
                operation.setdefault("responses", {}).setdefault(
                    "503", {"description": "필요한 외부 서비스 또는 서버 설정을 사용할 수 없습니다."}
                )
        used_tags = {
            tag
            for operations in schema["paths"].values()
            for operation in operations.values()
            for tag in operation.get("tags", [])
        }
        schema["tags"] = [tag for tag in TAG_DESCRIPTIONS if tag["name"] in used_tags]
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi
