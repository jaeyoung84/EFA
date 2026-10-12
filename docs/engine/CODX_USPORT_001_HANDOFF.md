# CODX-USPORT-001 배포·운영 인계

<!-- Codex 수정: CODX-USPORT-001 (2026-10-12), 파일 전체. -->

## 완료 범위

- 미국 주식은 기존 포트폴리오 입력의 USD 통화와 미국 티커를 사용한다.
- 평가 API가 현재 USD/KRW 환율로 한국·미국 주식과 KRW 현금을 합산한다.
- 평가 API와 화면에 한국/미국 시장별 보유 건수·평가 건수·원화 평가 소계를 표시한다.
- 국내 보유 뉴스 캐시에 미국 Yahoo Finance 뉴스 캐시를 병합한다.
- 미국 보유종목의 SEC EDGAR 최근 제출자료 읽기 API와 화면을 추가한다.
- 미국 뉴스·SEC 수집은 매시간 스케줄 작업이며 화면 요청은 캐시만 읽는다.

## 배포 전 필수 설정

`app/backend/.env` 또는 운영 비밀 설정에 다음 값을 추가한다.

```dotenv
SEC_USER_AGENT=EFA Quant 실제운영연락이메일@example.com
```

SEC 정책상 서비스 식별명과 실제 연락 이메일이 필요하다. 값이 없거나 `@`가 없으면 미국 뉴스는 계속 수집되지만 SEC 수집만 비활성화된다. 키나 이메일을 Git에 커밋하지 않는다.

## 배포 후 읽기 전용 확인

1. 미국 주식(AAPL 등)을 포트폴리오에 저장했을 때 `currency=USD`인지 확인한다.
2. `/api/portfolio/valuation`에서 `fx_usd_krw`, `market_totals.KR`, `market_totals.US`, 전체 `value_krw`를 확인한다.
3. 스케줄러 로그에서 `collect_us_portfolio_updates` 실행 오류가 없는지 확인한다.
4. MongoDB의 `us_holding_news_cache`, `us_holding_sec_filings_cache`에 해당 사용자 보유 티커가 생성됐는지 확인한다.
5. `/api/portfolio/news`에서 국내·미국 뉴스가 시장 구분과 함께 보이는지 확인한다.
6. `/api/portfolio/disclosures`의 SEC 링크가 `https://www.sec.gov/Archives/edgar/`로 시작하는지 확인한다.
7. 내 보유자산 화면에서 한국·미국 소계의 합 + 현금이 원화 환산 총자산과 일치하는지 확인한다.

## 변경 금지 경계

- 이 작업은 뉴스·공시를 추천 또는 청산 신호로 사용하지 않는다.
- 레버리지 DCA 파일·주문·Alpaca 체결·사용자 수동 원장·KQS 추천 엔진은 변경하지 않는다.
- `Codex 수정: CODX-USPORT-001` 표식 및 `docs/CODEX_CHANGES.md` 등록 범위를 Claude Code가 수정하려면 대상 파일·범위별 사용자 명시적 승인이 필요하다.

## 미실행

- AWS 배포
- 운영 MongoDB 수집
- 실제 Yahoo/SEC 외부 호출
- 실계정 포트폴리오 입력
- pytest 전체 실행(현재 로컬 Python 환경에 pytest와 앱 의존성 없음)
