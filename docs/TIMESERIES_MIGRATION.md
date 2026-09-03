# 가격 시계열 · 백테스트 결과의 TimescaleDB 이전 설계

Phase 5 항목 1(`docs/CLAUDE_HANDOFF.md` 참고)의 산출물이다. 이 문서는 **설계**이며, 아직 구현하지 않았다 — TimescaleDB는 개인 데스크톱 기본 구성(`docker-compose.desktop.yml`)의 경량 원칙(Qdrant·Meilisearch·Ollama도 의도적으로 뺀 구성)과 충돌하는 새 서비스이므로, 도입은 opt-in으로 하고 실제 적용 여부는 사용자 결정에 맡긴다.

## 왜 필요한가

현재 가격 데이터는 두 방식뿐이다.
1. **즉석 조회**: `portfolio.py`의 `/risk`, `strategy.py`의 `/backtest`·`/{id}/proposals`가 매 요청마다 `yfinance`로 과거 종가를 새로 받는다. 저장하지 않으므로 같은 종목·같은 기간을 여러 사용자가 요청해도 매번 Yahoo에 다시 묻는다.
2. **인메모리 TTL 캐시**: `portfolio.py`의 `_fetch_quote_cached`(20초)는 실시간 라스트 프라이스에만 쓰이고, 프로세스 재시작 시 사라진다.

문제:
- 백테스트·리스크 계산이 매번 네트워크 왕복에 걸리고, Yahoo가 일시적으로 느리거나 막히면 그대로 실패한다.
- 과거에 어떤 가격으로 계산했는지 감사 추적이 안 된다(전략 스냅샷에 지표만 남고 원천 가격 시계열은 안 남음).
- 동일 데이터를 여러 사용자·여러 요청이 반복 다운로드해 외부 API에 불필요한 부하를 준다.

`docs/JY_INVESTING_DEVELOPMENT.md`에 이미 "장기적으로는 PostgreSQL/TimescaleDB와 작업 스케줄러를 추가한다"고 방향이 잡혀 있다.

## 범위 — MongoDB를 대체하지 않는다

TimescaleDB는 **추가**되는 것이지 MongoDB를 대체하지 않는다.

| 데이터 | 저장소 | 이유 |
|---|---|---|
| 계정, 세션, 포트폴리오, 전략, 주문 제안, 모의 체결, 퀴즈 | MongoDB (그대로) | 사용자당 소량의 문서형 데이터, 이미 잘 동작 중 |
| **일별 OHLCV 가격 시계열** | **TimescaleDB (신규)** | 시간 축 범위 쿼리·압축·자동 파티셔닝이 필요한 전형적 시계열 데이터 |

## 스키마 설계

```sql
CREATE TABLE price_bars (
  ticker      TEXT NOT NULL,
  bar_date    DATE NOT NULL,
  open        DOUBLE PRECISION,
  high        DOUBLE PRECISION,
  low         DOUBLE PRECISION,
  close       DOUBLE PRECISION,
  volume      BIGINT,
  source      TEXT NOT NULL DEFAULT 'yahoo_finance',
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, bar_date)
);

SELECT create_hypertable('price_bars', 'bar_date',
  chunk_time_interval => INTERVAL '1 year', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS price_bars_ticker_date_idx
  ON price_bars (ticker, bar_date DESC);
```

- `fetched_at`을 남겨 "이 종가가 언제 받아온 것인지" 추적한다 — Phase 3부터 지켜온 "출처·갱신 시각 표시" 원칙을 시계열 캐시에도 그대로 적용.
- 압축·보존 정책(예: 6개월 지난 청크 압축)은 지금 튜닝하지 않는다. 실사용 데이터가 쌓인 뒤 결정하는 게 맞다(너무 이른 최적화).

## 캐시-후-영속 조회 패턴

```python
async def get_or_fetch_price_history(ticker: str, start: date, end: date) -> pd.DataFrame:
    cached = await query_price_bars(ticker, start, end)          # TimescaleDB에서 먼저 조회
    missing_ranges = find_gaps(cached, start, end)                 # 없는 구간만 계산
    for gap_start, gap_end in missing_ranges:
        fresh = yf.download(ticker, start=gap_start, end=gap_end)  # 빠진 구간만 Yahoo에서 채움
        await upsert_price_bars(ticker, fresh)
    return await query_price_bars(ticker, start, end)               # 합쳐서 반환
```

- `portfolio.py`의 `/risk`, `strategy.py`의 `/backtest`·주문 제안 신호 재계산이 `yf.download`를 직접 부르는 대신 이 함수를 쓰도록 바꾼다.
- `_fetch_quote`/`_fetch_quote_cached`(실시간 라스트 프라이스)는 그대로 둔다 — 이건 "지금 이 순간의 시세"라서 시계열 테이블 구조와 안 맞고, 기존 20초 TTL 캐시로 충분하다.

## 인프라 구성 (opt-in)

기본 `docker-compose.desktop.yml`에는 넣지 않는다. 기존에 이미 있는 `docker-compose.lean.yml` 같은 오버레이 파일 관례를 따라 별도 `docker-compose.timescale.yml`을 추가하고, 필요한 사람만 아래처럼 얹어서 쓰게 한다.

```bash
docker compose -f docker-compose.desktop.yml -f docker-compose.timescale.yml up --build -d
```

```yaml
# docker-compose.timescale.yml (설계 예시 — 아직 생성하지 않음)
services:
  timescaledb:
    image: timescale/timescaledb:latest-pg16
    container_name: jy_investing_timescale
    restart: unless-stopped
    environment:
      POSTGRES_DB: jy_investing_timeseries
      POSTGRES_PASSWORD_FILE: /run/secrets/timescale_password   # 평문 비밀번호를 compose 파일에 넣지 않는다
    volumes:
      - jy_investing_timescale_data:/var/lib/postgresql/data
  backend:
    environment:
      TIMESCALE_URL: postgresql://postgres@timescaledb:5432/jy_investing_timeseries
volumes:
  jy_investing_timescale_data:
```

## 백엔드 통합

- `app/backend/timeseries_db.py` 신규 — `db.py`와 같은 지연 초기화 싱글턴 패턴. ORM 없이 `asyncpg`로 직접 SQL을 쓴다(이 코드베이스가 Motor도 ODM 없이 직접 쓰는 스타일과 일관되게).
- 연결 실패 시(TIMESCALE_URL 미설정 등) `get_or_fetch_price_history`가 캐시 없이 곧바로 `yf.download`로 폴백 — TimescaleDB를 안 띄운 사용자도 앱이 그대로 동작해야 한다(opt-in이므로 필수 의존성으로 만들면 안 됨).

## 하지 않는 것

- 포트폴리오·전략·주문·체결의 MongoDB → Postgres 이전 (문서형 데이터라 지금 구조가 더 적합)
- BI/리포팅 도구 도입
- 지금 시점에 실제 TimescaleDB 컨테이너 기동·코드 통합 (다음 단계에서 사용자 승인 후 진행)

## 다음 단계 (구현 시)

1. `docker-compose.timescale.yml` 작성 + `.env.desktop.example`에 `TIMESCALE_URL` 예시 추가.
2. `app/backend/timeseries_db.py` + 마이그레이션(테이블 생성) 스크립트.
3. `portfolio.py`/`strategy.py`의 `yf.download` 호출부를 `get_or_fetch_price_history`로 교체(폴백 유지).
4. 부하가 보이면 압축·보존 정책 추가.
