# JY investing — Claude Code 핸드오프

이 문서는 현재 작업 트리에서 Claude Code가 다음 개발을 이어갈 때 사용하는 기준이다. 제품 방향의 원문은 `docs/JY_INVESTING_DEVELOPMENT.md`를 우선한다.

## 제품 목표와 범위

JY investing은 개인 투자자를 위한 **리서치·전략 검증·포트폴리오 관리 시스템**이다.

- 자동매매보다 리서치와 모의 운용이 우선이다.
- 실제 주문·증권사 계좌 연결은 구현하지 않는다.
- 신호 → 리스크 검사 → 주문 제안 → 사용자 승인 → 모의 체결 순서를 지켜 확장한다.
- 기존 교육·분석·ML 화면과 API는 삭제하지 않는다. 새 정보 구조에서 재사용하거나 보존한다.

## UI 개편 — 퀀트 트레이딩 터미널 다크 테마 + 상단 메뉴바 + Inter 폰트

Phase 5 이후 두 단계로 진행한 UI 개편이다.

**1단계(핵심 화면만)**: Bloomberg/터미널풍 다크 테마를 대시보드·포트폴리오·전략 연구소에 먼저 적용. `styles.css` `:root`에 `--term-*` 토큰(배경 `#0a0e17`, 패널 `#12161f`, 강조색 amber `#f0b90b`, 상승 `#00e396`, 하락 `#ff3b5c`, 숫자용 모노스페이스)을 추가하고, `body.theme-terminal` 클래스(당시 `app.js`의 `TERMINAL_THEME_VIEWS` 배열로 제어)로 콘텐츠 영역만 부분 적용했다. `:root`의 라이트 토큰 자체는 그대로 뒀다.

**2단계(전체 확장 + 상단 메뉴바 + 폰트)**: "나머지도 전체 변경" 요청에 따라 범위를 전체 앱으로 넓혔다.

- **다크 테마를 전역 기본값으로 승격**: `:root`의 `--bg`/`--surface`/`--text`/`--primary` 등 라이트 토큰 값 자체를 다크 팔레트로 바꿨다(`--term-*`는 인라인 스타일에서 직접 참조할 별칭으로 유지). 이제 `.card`/`.metric-box`/`.param-input` 등 토큰 기반 공통 클래스를 쓰는 화면은 자동으로 다크가 된다. `body.theme-terminal` 스코프는 이제 사실상 중복(같은 값)이지만 충돌은 없어 그대로 남겨 뒀다 — 나중에 정리 가능.
- **인라인 하드코딩 색상 정리**: 자동화 스크립트로 `styles.css` 전체(총 400여 줄)와 `companyFinancial.js`/`dartCompanySearch.js`/`financialKnowledge.js`/`groupNetwork.js`/`investmentTree.js`/`kospiExcluded.js`/`technicalChart.js`/`valuation.js`의 인라인 스타일에서 하드코딩된 흰색·연한 배경(`#fff`, `#f8fafc` 등)과 어두운 텍스트(`#0f172a`, `#334155` 등)를 토큰으로 치환했다. 파스텔 배지 배경(`#eff6ff` 등)은 반투명 `rgba()`로 바꿔 다크에서도 배지처럼 보이게 했다. 인증 모달·엔터프라이즈 모달·플로팅 챗봇도 다크로 전환.
- **상승/하락 색상 통일 (2026-09-03, 한국 관행으로 재변경)**: 이 절에서 처음엔 초록=상승·빨강=하락(Bloomberg/크립토 터미널 관행)으로 통일했으나, 이후 사용자 요청으로 **빨강=상승(`--up: #f0304a`)·파랑=하락(`--down: #3b82f6`)** 한국 증시 관행으로 다시 통일했다. `--green`/`--red`(성공·실패 등 일반 의미)와는 별개로 `:root`에 `--up`/`--up-bg`/`--down`/`--down-bg` 전용 토큰을 신설하고, `--term-up`/`--term-down`도 같은 값으로 맞췄다. `styles.css`의 `.is-up`/`.is-down`/`.is-profit`/`.is-loss` 계열 규칙(시세 행, 등락률, 보유자산 손익, 추세 강의 예제 등 17곳)을 이 토큰으로 교체했고, 등락과 무관한 `.risk-metric.is-warn/.is-danger`·`.holdings-status.is-success/.is-error`·상관계수 스케일 색은 건드리지 않았다. `home.js`/`worldMarkets.js`/`assetClasses.js`/`todayGainers.js`/`volumeCloud.js`/`sectorCloud.js`는 원래부터 한국 관행(빨강/파랑) 하드코딩 색이라 변경하지 않았다. `technicalChart.js`(캔들스틱·이평선·RSI 등 40여 곳)는 반대로 서구식(`#107c10`/`#c50f1f`)이 박혀 있어 `#f0304a`/`#3b82f6` **리터럴 hex**로 교체했다 — 이 파일은 `ApexCandleBuilder`가 색상값을 ApexCharts/SVG 렌더링 옵션에 그대로 넘기는데, CSS `var()` 커스텀 프로퍼티는 그런 렌더링 컨텍스트에서 안정적으로 해석되지 않을 수 있어 토큰 참조 대신 리터럴 값을 썼다. 전략 성과(수익률) 색상도 같은 논리로 맞췄다: `strategyList.js`의 CAGR, `backtest.js`/`quant.js`의 지표 카드(순이익 등 부호 기반 색상)를 `var(--up)`/`var(--down)`로 교체. 반면 `valuation.js`의 FCF/DCF/EVA 교육용 설명 색상, 매수/매도 신호 색(`BUY_SIGNAL_COLOR` 등), 전략 타입 배지, 상태 배지(대기·승인·거절), 포트폴리오 카드 강조색은 "주가 상승/하락" 표시가 아니라 별개 의미라 판단해 손대지 않았다.
- **좌측 사이드바 → 상단 메뉴바(인베스팅닷컴 스타일)**: `index.html`의 `<aside id="sidebar">` 아코디언 메뉴를 제거하고, 상단바 바로 아래 `<nav id="mainnav">` 가로 메뉴 + 호버 드롭다운으로 교체했다. 데스크톱은 `:hover`/`:focus-within`으로 드롭다운이 열리고, 모바일(≤1024px)은 햄버거 버튼으로 여는 세로 아코디언 패널로 전환된다(`app/frontend/js/sidebarUI.js`의 `toggleMainnav`/`openMainnav`/`closeMainnav`).
  - **메뉴 우선순위**: 대시보드 → **포트폴리오** → **전략 연구소** → 종목 탐색 → 리서치 자료실 → 설정·운영 (기존엔 종목 탐색·전략 연구소가 포트폴리오보다 앞이었다 — 핵심 화면인 포트폴리오·전략을 앞으로 옮김). 각 드롭다운 내부도 실제 데이터 항목(예: "실제 가격 백테스트", "내 전략")을 가상/LEAN 항목보다 위로 재배치.
  - `data-view`를 가진 링크는 여전히 `nav-item` 클래스를 쓰므로 `app.js`의 활성 링크 하이라이트·`_setActiveNavSections` 로직은 거의 그대로 재사용했다(대상 셀렉터만 `.mainnav-group[data-group="..."]`로 변경).
  - `.sidebar`/`--sidebar-w`/`toggleNav`/`toggleSidebar` 등 옛 코드는 제거했다. `pages/*.html`(정적 페이지, `youtube.html` 1개)이 쓰던 `partials/sidebar-nav.html` include는 그 파일 자체가 이미 존재하지 않아(빌드 스크립트도 없음) 이번 변경 전부터 깨져 있던 상태라 손대지 않았다.
- **폰트**: Google Fonts에서 `Inter`(라틴/숫자, 가중치 400~800)와 `JetBrains Mono`(가격·지표 숫자용 고정폭)를 로드해 `:root`의 폰트 스택 맨 앞에 추가했다(`"Inter", "Pretendard", ...`). 한글은 영향 없이 계속 Pretendard가 렌더링한다.
- **알려진 한계**: 자동화 치환이라 일부 화면(공분산·상관계수·샤프비율·몬테카를로 설명 모달 등 `portfolioCombination.js`/`portfolioGuide.js`/`portfolioSimulation.js`의 헬프 모달)은 픽셀 단위로 손보지 않았고, 일부 저채도 계열 색만 남아 있을 수 있다. 이 환경엔 브라우저가 없어 실제 렌더링을 눈으로 확인하지 못했다 — CSS 문법 검증과 서빙 확인까지만 함.
- **버그 수정 (드롭다운 클릭 안 됨)**: `.mainnav-inner`에 `overflow-x: auto; overflow-y: visible;`을 같이 줬는데, CSS 스펙상 두 축의 overflow 값이 다르면(하나라도 `visible`이 아니면) 브라우저가 둘 다 강제로 clip 처리한다 — `visible`을 명시했어도 무시되고 드롭다운이 잘리며 클릭이 막혔다. `overflow: visible;` 한 값으로 통일해 해결(가로 스크롤 폴백은 포기 — 어차피 1024px 이하는 별도 아코디언 레이아웃이라 문제 없음). 사용자가 실제 브라우저에서 정상 동작을 확인함.

## 현재까지 완료된 작업

### 브랜드와 정보 구조

- 서비스명: `JY investing`, 부제: `PERSONAL QUANT`
- 좌측 메뉴를 다음 6개로 개편했다.

```text
대시보드 / 종목 탐색 / 전략 연구소 / 포트폴리오 / 리서치 자료실 / 설정·운영
```

- 대시보드에는 시장 상태, 오늘 확인할 항목, 시스템 상태, 주요 시장 차트가 있다.
- 상단 방문자 수·Swagger 상태처럼 투자 판단에 불필요한 요소는 숨겼다.

### 개인 포트폴리오

- 로그인 사용자별 MongoDB 저장 API: `GET /api/portfolio`, `PUT /api/portfolio`
- 입력 필드: `cash_krw`, `ticker`, `name`, `quantity`, `average_price`, `currency(KRW/USD)`
- API 구현: `app/backend/routers/portfolio.py`
- 라우터 등록: `app/backend/main.py`
- 화면: `포트폴리오 → 내 보유자산`, 구현 파일 `app/frontend/js/views/portfolioHoldings.js`
- 인증 토큰이 필요한 요청을 공통으로 처리하도록 `authRequest`, `isAuthenticated`를 `app/frontend/js/auth.js`에서 export했다.

### 평가와 인사이트

- `POST /api/market/snapshot`의 Yahoo Finance 지연 시세를 프런트엔드에서 호출해 평가한다.
- KRW 자산은 그대로, USD 자산은 `KRW=X` 현재 환율로 원화 환산한다.
- 평가금액, 평가손익, 수익률, 자산별 비중, KRW/USD 통화 노출, 최대 비중, 집중도(낮음/보통/높음)를 표시한다.
- 모든 보유 종목 시세가 조회된 경우에만 집중도와 대시보드 평가금액을 확정 표시한다. 일부 시세가 없으면 장부 기준 금액을 유지한다.
- 이 평가는 현재 환율로 원화 환산한 값이다. USD 매수 당시 환율을 저장하지 않았으므로 원화 기준 손익에는 현재 환율 기준 환산이 포함된다.

### Phase 3: 실제 보유자산 리스크 분석 (완료)

- `GET /api/portfolio/valuation` — 저장된 보유 종목을 서버가 Yahoo Finance 지연 시세로 평가한다. 프런트엔드가 하던 개별 종목 조회를 서버로 옮기고, 20초 TTL 인메모리 캐시로 동일 종목 중복 조회를 줄인다(`_fetch_quote_cached`). 종목별 현재가·전일 대비·평가금액·평가손익·시세 시각과, 조회 실패 종목의 실패 사유를 함께 반환한다.
- `GET /api/portfolio/risk?period=1y` — 보유 종목의 실제 일별 종가 이력(Yahoo Finance, 기간 3mo~5y)으로 95% Historical VaR·CVaR, 연환산 변동성, 최대 낙폭(MDD), 최대 보유 비중, 통화 노출을 계산한다.
  - 결합 비중은 `/valuation`과 동일한 현재 지연 시세 기준 원화 환산 비중을 쓰고, 각 종목의 일간 수익률은 **현지통화 기준**을 그대로 사용한다(과거 USD/KRW 환율 이력은 반영하지 않음 — 1차 버전 명시 사항).
  - 종목별 가격 이력이 60거래일 미만이거나 조회에 실패하면 리스크 계산에서 제외하고 사유를 `excluded`에 담는다(`stage: "valuation"`은 시세 조회 실패, `"history"`는 가격 이력 부족·실패).
  - 가격 이력을 확인한 자산의 비중이 60% 미만이거나 공통 거래일이 20일 미만이면 `available: false`와 사유만 반환하고 전체 리스크 수치는 만들지 않는다.
  - 구현: `app/backend/routers/portfolio.py`의 `_fetch_quote_cached`, `_compute_valuation`, `_composition`, `get_portfolio_valuation`, `get_portfolio_risk`.
- 화면: `포트폴리오 → 실제 보유자산 리스크`, 구현 파일 `app/frontend/js/views/portfolioRisk.js`. 데이터 출처(실제 가격 이력 vs 지연 시세)와 로컬통화 기준 가정을 화면 상단에 명시하고, 확률 기반 가상 시뮬레이션인 기존 `리스크 분석(VaR)` 화면과 명확히 구분하도록 문구를 넣었다.
- 알려진 제한: 종목별 비중은 "현재" 평가금액 기준으로 고정한 뒤 과거 구간에 그대로 적용한 값이다(과거 실제 보유 수량 변화는 반영하지 않음). USD 자산의 역사적 환율 변동 리스크는 이번 버전에 포함하지 않았다.

### Phase 4: 전략과 포트폴리오 연결 (완료)

- 가상 GBM 백테스트(`app/backend/routers/quant.py`)와 실제 가격 백테스트를 **별도 라우터**로 API 수준에서 분리했다: `app/backend/routers/strategy.py`, prefix `/api/strategy`.
- `POST /api/strategy/backtest` (인증 불필요, 미저장 미리보기) — Yahoo Finance 실제 일별 종가로 MA 크로스오버 전략을 백테스트한다. 거래비용·슬리피지(bp)를 회전 시마다 차감하고, 벤치마크(기본값 Buy & Hold)와 나란히 CAGR·Sharpe·**Sortino**·MDD·승률·총수익률을 반환한다. 응답에 `data_source: "real_price"`를 명시해 가상 백테스트와 프로그램적으로 구분한다.
- `POST /api/strategy` (인증) — 실제 가격/가상 GBM/LEAN 사전 실행 결과를 "전략" 단위로 저장한다(`strategies` 컬렉션, 사용자당 N개 문서). 종목, 기간, 이동평균, 거래비용, 슬리피지, 벤치마크, 리밸런싱 안내, 파라미터 버전(`version`/`parent_id`)을 메타데이터로 남긴다. 이미지(base64 PNG)는 저장하지 않고 수치 스냅샷만 보관한다(문서 크기 절약).
- `GET /api/strategy`, `GET /api/strategy/{id}`, `DELETE /api/strategy/{id}` — 목록·상세·삭제. `type="lean"`은 스냅샷을 저장하지 않고 조회 시마다 LEAN 결과 파일을 그대로 다시 읽는다(불변 파일이라 캐시 불일치 위험이 없음).
- `POST /api/strategy/{id}/proposals` (인증) — **`type="real_backtest"`에서만 허용**. 저장된 종목의 최신 시세로 현재 MA 상태를 다시 계산해 `order_proposals`에 대기 중 주문 제안(매수/매도, 근거, 기준가)을 만든다. 가상·LEAN 전략에는 실제 신호가 없으므로 제안을 만들지 않는다.
- `POST /api/strategy/proposals/{id}/approve`(바디: `quantity`)/`reject` (인증) — 승인 시 최신 시세로 `paper_fills`에 모의 체결을 기록한다. **`portfolios`(실제 보유자산 장부)는 절대 변경하지 않는다** — 모의 체결이 실제 장부처럼 보이는 것을 막는 안전장치.
- `GET /api/strategy/fills` (인증) — 사용자의 전체 모의 체결 내역.
- 화면: `전략 연구소 → 실제 가격 백테스트`(`app/frontend/js/views/strategyBacktest.js`), `전략 연구소 → 내 전략`(`app/frontend/js/views/strategyList.js`, 목록·상세·주문 제안 승인/거절·모의 체결 내역). 기존 `전략 백테스트`(`backtest.js`, 가상)와 `LEAN 리포트`(`quant.js`)에도 "전략으로 저장" 버튼을 추가해 세 가지 출처를 모두 "내 전략" 하나로 모을 수 있게 했다.
- 알려진 제한: 주문 제안은 수량을 자동 계산하지 않으며 승인 시 사용자가 직접 입력한다. 실제 가격 백테스트의 벤치마크 비교는 각 종목의 현지통화 기준 수익률이며 환율 이력은 반영하지 않는다.
- 사전 발견 버그 수정: `quant.py`가 `io`/`base64`/한글 폰트 설정/RSI 계산 함수를 import 없이 사용해 5개 엔드포인트가 호출 시 `NameError`였던 문제, `backtest.js`/`pipeline.js`가 보내는 파라미터명이 백엔드 스키마와 달라 일부 값(변동성, 거래일 수, RF 트리 수 등)이 무시되던 문제를 함께 고쳤다.

### 내 보유자산 종목 자동완성

- 기존 `GET /api/home/chart-search`(홈 화면 차트 검색용, DART_API_KEY 불필요)를 재사용해 `포트폴리오 → 내 보유자산`의 종목명 입력에 검색 자동완성을 붙였다. 결과 선택 시 티커·종목명·통화(KS/KQ 접미사는 KRW, 그 외 USD)를 자동으로 채운다. 티커 입력란은 그대로 남겨 자동완성이 안 되는 종목은 직접 입력 가능. 구현: `app/frontend/js/views/portfolioHoldings.js`, `styles.css`(`.holdings-ticker-search`, `.holdings-search-results`).

### Phase 5: 운영 안정화 (완료)

1. **API 테스트 (완료)** — `tests/`(pytest, 실제 MongoDB·실제 Yahoo Finance 대상, 모킹 없음). `pytest.ini`에서 `asyncio_default_fixture_loop_scope = session`으로 강제(모듈 전역 Motor 클라이언트 하나를 재사용하는 `db.py` 구조상, 테스트마다 이벤트 루프가 바뀌면 `RuntimeError: Event loop is closed`가 나기 때문 — Motor+pytest-asyncio의 알려진 함정).
   - `test_auth_required.py` — 로그인 없는 포트폴리오/전략 API 접근 401 (백테스트 미리보기는 인증 불필요임도 확인)
   - `test_user_isolation.py` — 포트폴리오·전략이 사용자 간 분리되는지 (다른 사용자는 목록에 안 보이고 직접 접근 시 404)
   - `test_input_validation.py` — 중복 티커·잘못된 티커·잘못된 기간이 422
   - `test_no_exaggeration.py` — 존재하지 않는 티커 보유 시 평가금액·리스크 수치를 만들어내지 않고 `available:false`+제외 목록으로 응답
   - `test_paper_trading_safety.py` — 가상/LEAN 전략은 주문 제안 생성 차단(422), 승인된 모의 체결이 실제 `portfolios` 컬렉션을 절대 변경하지 않음, 중복 승인 방지(409)
   - 실행: `requirements-test.txt`(pytest/pytest-asyncio/httpx, 배포 이미지엔 미포함)를 이미지 위에 임시 설치해 실행. `MONGODB_DB=jy_investing_test`로 개발용 DB와 분리.
     ```bash
     docker run --rm --network jy-investing-desktop_default \
       -v "$(pwd)":/app -w /app \
       -e MONGODB_URL=mongodb://mongo:27017 -e MONGODB_DB=jy_investing_test \
       jy-investing-backend:desktop \
       bash -c "pip install -q -r requirements-test.txt && python -m pytest tests/ -v"
     ```
2. **TimescaleDB 이전 설계 (완료)** — 상세 설계는 `docs/TIMESERIES_MIGRATION.md`. 요지: MongoDB는 그대로 두고 일별 OHLCV만 TimescaleDB `price_bars` 하이퍼테이블로 이전, opt-in 오버레이(`docker-compose.timescale.yml`, 미생성)로 분리해 기본 경량 데스크톱 구성에는 영향 없게 한다. 캐시-후-영속 조회 패턴(`get_or_fetch_price_history`)으로 `portfolio.py`/`strategy.py`의 `yf.download` 직접 호출을 대체할 예정. **설계만 완료, 실제 TimescaleDB 컨테이너·코드는 아직 구현하지 않음** — 새 DB 서비스를 도입하는 인프라 결정이라 사용자 승인 후 진행.
3. **scheduler/worker 분리 (완료)** — `app/backend/scheduler.py`. Celery/Redis 같은 별도 워커 컨테이너 대신 APScheduler(`AsyncIOScheduler`) 기반 인프로세스 작업으로 구현해 `docker-compose.desktop.yml`의 경량 구성을 그대로 유지했다.
   - 5분 주기로 모든 사용자의 보유 종목 + `real_backtest` 전략 종목을 모아(`collect_active_tickers`) `quote_cache.py`의 공유 TTL 캐시를 백그라운드에서 미리 채운다(`refresh_active_quote_cache`). 블로킹 yfinance 호출은 `asyncio.to_thread`로 감싸 이벤트 루프를 막지 않는다.
   - **리팩터링**: 기존에 `portfolio.py`에만 있던 시세 캐시 로직(`_fetch_quote`/`_fetch_quote_cached`)과 `strategy.py`가 별도로 갖고 있던 캐시 없는 사본을 `app/backend/quote_cache.py`(공유 모듈, `db.py`/`auth.py`처럼 여러 라우터가 함께 씀)로 통합했다. `strategy.py`의 주문 승인(`approve_proposal`)도 이제 같은 캐시를 써서 스케줄러가 미리 데운 시세를 재사용할 수 있다.
   - `main.py`의 `@app.on_event("startup"/"shutdown")`에서 시작·종료. `apscheduler`가 없으면(미설치) 조용히 건너뛰고 기존 요청-시점 조회 경로로 정상 동작 — 스케줄러는 최적화일 뿐 필수 의존성이 아니다.
   - `requirements.desktop.txt`에 `apscheduler` 추가. 재빌드 후 실행 중인 컨테이너에서 스케줄러가 실제로 등록·기동됨을 확인, 리팩터링 후 전체 API 테스트(20개) 재통과 확인.
4. **프런트엔드 실제/지연/가상 데이터 표시 일관화 (완료)** — 공통 배너 헬퍼 `app/frontend/js/views/dataSourceNote.js`(`dataSourceNote(kind, customText?)`, kind: `real`/`dart`/`synthetic`)를 만들어 `cloudResourceCard()`와 같은 재사용 패턴으로 22개 화면에 삽입했다.
   - 이미 자체적으로 명확히 표시하던 화면(포트폴리오·전략·리스크·LEAN·백테스트 등)은 그대로 두고, 표시가 없던 화면만 대상으로 함.
   - 가상/합성 데이터(ML·DL·NLP 실습 11개 화면 — CNN/LSTM/Transformer/MLP/SVM/KMeans/RandomForest/선형회귀/텍스트분류/교차검증/Decision Boundary): "학습용 합성 데이터" 배너.
   - 실제 데이터(기업 파이낸셜 분석, 거시경제 실시간, 포트폴리오 조합, 섹터·거래량 클라우드, KOSPI 제외 분석, 산업 경쟁력 분석): "실제 데이터(지연 가능)" 배너.
   - OpenDART 기반 화면(상장기업 검색, 지역·종사자수 조회, 그룹사 네트워크, DART 재무 AI 분석): 전용 문구 + AI 요약이 있는 화면은 "AI 생성 요약은 참고용" 문구 추가.
   - `financialStatement.js`(기업 펀더멘탈 분석)는 실제 데이터가 아니라 고정 예시 이미지를 쓰는 학습 자료임을 확인하고 그에 맞는 전용 문구를 달았다(다른 화면과 혼동되지 않도록).
   - 재빌드 후 실행 중인 컨테이너에서 정적 파일 서빙과 배너 삽입을 curl로 직접 확인함.

### Docker Desktop

- 개인 데스크톱용 경량 Compose: `docker-compose.desktop.yml`
- 경량 이미지: `Dockerfile.desktop`, `requirements.desktop.txt`
- MongoDB + FastAPI만 기동하며, Ollama/Qdrant/Meilisearch와 대용량 Torch·Diffusers는 제외했다.
- 실행 안내: `docs/DESKTOP_SETUP.md`

```bash
docker compose -f docker-compose.desktop.yml up --build -d
```

## 다음 작업 — 우선순위 순서

### Phase 5: 운영 안정화 (완료)

4개 항목 모두 끝났다(상세는 위 "완료된 작업" 절).

1. ~~가격 시계열과 백테스트 결과를 PostgreSQL/TimescaleDB로 이전하는 방안을 설계한다.~~ (설계 완료 — `docs/TIMESERIES_MIGRATION.md`. **실제 TimescaleDB 컨테이너·코드는 미구현** — opt-in 인프라 도입은 사용자 승인 후 별도 진행)
2. ~~데이터 갱신 작업을 scheduler/worker로 분리한다.~~ (완료 — `app/backend/scheduler.py`)
3. ~~API 테스트를 추가한다.~~ (완료 — `tests/`)
4. ~~프런트엔드에는 실제/지연/가상 데이터, 출처, 최종 갱신 시각을 일관되게 표시한다.~~ (완료)

### 이후 로드맵 — 문서화만 되어 있고 착수 여부는 미정

`docs/JY_INVESTING_DEVELOPMENT.md`의 "우선순위 5번"에 "충분한 검증 뒤 증권사 계좌 조회, 최종적으로 승인 기반 주문 연동을 검토한다"는 문구가 있다. 다만 이 문서 상단의 "구현 시 반드시 지킬 것"에 **"실제 주문·증권사 계좌 연결은 구현하지 않는다"**고 명시돼 있으므로, Phase 5 다음에 자동으로 이어지는 확정 단계가 아니라 나중에 사용자가 명시적으로 요청할 때만 다시 검토할 별도 논의 대상이다. 지금 커밋된 로드맵상 "완료"는 Phase 5까지다.

## 구현 시 반드시 지킬 것

- API 키, 비밀번호, 계좌번호, 주문 토큰을 코드·문서·Git에 넣지 않는다.
- 투자 성과에 총수익률만 표시하지 않는다. CAGR, MDD, Sharpe/Sortino, 비용 반영 여부를 함께 다룬다.
- 현재 `quant.py`의 일부 백테스트·포트폴리오 값은 고정 가정 또는 시뮬레이션이다. 실거래 성과처럼 표현하지 않는다.
- Yahoo Finance 시세는 지연되거나 실패할 수 있다. 조회 실패를 0원 또는 정상 데이터처럼 처리하지 않는다.
- `docker-compose.prod.yml`은 운영용이다. 로컬 개인 실행은 `docker-compose.desktop.yml`을 우선 사용한다.
- 기존 사용자 변경 사항이 있을 수 있으므로 `git reset --hard`, 무단 삭제, 기존 라우트 제거를 하지 않는다.

## 주요 파일 지도

```text
app/backend/routers/portfolio.py        사용자별 보유자산 저장 API
app/backend/routers/strategy.py         전략 저장·실제 가격 백테스트·주문 제안·모의 체결 API
app/backend/routers/quant.py            가상 GBM 백테스트·포트폴리오·리스크 (교육용, 실제 데이터 아님)
app/backend/routers/lean.py             LEAN 사전 실행 백테스트 결과 서빙
app/backend/routers/auth.py             인증·세션
app/backend/main.py                     API 라우터 등록, 시장 시세 API
app/backend/quote_cache.py              공유 시세 TTL 캐시 (portfolio.py·strategy.py·scheduler.py가 함께 씀)
app/backend/scheduler.py                백그라운드 시세 캐시 예열 (APScheduler, 인프로세스)
tests/                                  pytest API 테스트 (실제 MongoDB·Yahoo Finance 대상)
app/frontend/js/views/portfolioHoldings.js  평가·비중·집중도 UI 계산, 종목명 자동완성
app/frontend/js/views/portfolioRisk.js  실제 보유자산 VaR·CVaR·변동성·MDD UI
app/frontend/js/views/strategyBacktest.js  실제 가격 백테스트 UI
app/frontend/js/views/strategyList.js   내 전략 목록·상세·주문 제안·모의 체결 UI
app/frontend/js/views/dataSourceNote.js 실제/지연/가상 데이터 공통 안내 배너
app/frontend/js/views/home.js           대시보드 요약 카드
app/frontend/js/auth.js                 인증 요청 공통 함수
app/frontend/js/app.js                  SPA 라우트 등록
app/frontend/index.html                 브랜드·사이드바 메뉴
app/frontend/styles.css                 JY investing UI 스타일
docker-compose.desktop.yml              경량 Docker Desktop 구성
run-jy-investing.bat                    Windows 더블클릭 실행기(Docker 자동 기동 + 브라우저 오픈)
stop-jy-investing.bat                   Windows 더블클릭 종료기
docs/DESKTOP_SETUP.md                   데스크톱 실행 방법
```

## 검증 상태와 알려진 제한

- 통과: `python3 -m py_compile` 대상 백엔드 파일, `git diff --check`, Docker Compose 구성 검증, HTML 파싱, JavaScript 기본 괄호 구조 검사.
- 현재 작업 트리는 아직 커밋되지 않았다. 기존 수정 파일과 새 파일을 함께 검토해 하나의 의미 있는 커밋으로 만들 것을 권장한다.
- 이 환경에서는 Docker 데몬 접근 승인을 받아 기동을 시도했지만, 레지스트리 이미지 다운로드가 완료되지 않아 실제 `/api/health`까지 확인하지 못했다.
- 로컬에 Node.js와 Python 의존성 패키지가 설치되어 있지 않아, 브라우저 E2E와 FastAPI 런타임 테스트는 Docker 빌드 후 수행해야 한다.
