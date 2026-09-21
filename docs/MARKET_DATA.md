# 시장 데이터 계층

일봉 OHLCV는 `market_ohlcv` 컬렉션에 저장한다. 키는
`ticker + interval + timestamp`이며 같은 거래일을 다시 수집하면 최신 검증값으로
upsert한다. 각 행에는 공급자, 공급자 버전, 조정 여부, 수집 시각, 데이터셋 버전과
무결성 fingerprint를 기록한다.

`market_data_runs`에는 수집 실행별 수신·통과·거부 봉 수와 품질 결과를 남긴다.
OHLC 관계가 깨졌거나 중복·비정상 시각·음수 거래량인 봉은 저장하지 않는다.
Yahoo가 함께 제공하는 배당과 주식분할은 `market_corporate_actions`에 별도 저장한다.
일봉의 `adjusted=true`는 공급자의 수정주가 기준임을 뜻하며, 기업행동 원본 이벤트와
분리해 보존하므로 이후 백테스트에서 조정 방식을 재현할 수 있다.

## 공급자와 장애 대응

기본 순서는 `yahoo,stooq,fred`다. Stooq는 미국 일반 주식 보조 공급자이며 한국 종목이나
지수는 `STOOQ_SYMBOL_MAP`으로 명시 매핑해야 한다. 모든 외부 공급자가 실패하면
요청 범위의 마지막 검증 통과 데이터를 `source: cached`,
`quality.status: last_verified_cache`로 반환한다. 저장 데이터가 전혀 없을 때만 화면은
기존의 명시적 시뮬레이션 폴백을 사용한다.

## 수집과 조회

```bash
# 실행 중인 backend 컨테이너에서 대표 시장 1년치 수집
docker exec investment_backend python /app/scripts/refresh_market_data.py

# 특정 티커/기간 지정
docker exec -e MARKET_DATA_REFRESH_TICKERS=005930.KS,^KS11 \
  -e MARKET_DATA_REFRESH_DAYS=730 \
  investment_backend python /app/scripts/refresh_market_data.py

# HTTP API로 최대 10개, 최대 10년치 수집
curl -X POST http://localhost:18000/api/market/data/refresh \
  -H 'Content-Type: application/json' \
  -d '{"tickers":["^KS11","005930.KS"],"days":370}'

# 저장본·출처·품질·버전 조회
curl 'http://localhost:18000/api/market/data-status?ticker=%5EKS11'
```

백테스트에서는 반드시 실행 결과의 `dataset_version`, `source`, `adjusted` 값을 함께
기록해야 한다. 배당·액면분할·수정주가의 공급자 간 차이는 다음 단계의 기업행동
정규화 작업 전까지 동일 출처·동일 조정 옵션 안에서만 비교한다.

## 14단계 품질 모니터

`/api/market/data-status`의 `quality_monitor`는 저장된 일봉을 변경하지 않고 최근성,
평일 공백, 절대 일간 수익률 30% 이상 구간을 점검한다. 평일 공백과 급등락은 거래소
휴일·분할·배당 때문에 정상일 수 있으므로 자동 삭제나 자동 주문 차단을 하지 않으며,
검토 대상으로만 표시한다. 모의 계좌는 최근 1개월 MDD가 10% 이상이거나 기간 수익률이
-10% 이하일 때 성과 점검 알림을 생성하지만, 이 알림도 주문을 실행하지 않는다.

## 25~26단계 운영 자동화와 일일 리포트

평일 `18:25` KST에 `MARKET_DATA_CORE_TICKERS`(기본 KOSPI·KOSDAQ·S&P 500·Nasdaq·원/달러)와
보유·전략·모의체결 종목을 합쳐 최근 `MARKET_DATA_REFRESH_DAYS`(기본 370일) 일봉을 갱신한다.
실행 이력은 `market_data_refresh_jobs`, 경고 전이 이력은 `market_data_alerts`에 보존한다.

수집 실패, 마지막 검증 캐시 폴백, 거부 봉이 포함된 품질 결과, 핵심 지수의 2영업일 초과 최신성
지연은 경고로 남는다. 다음 정상 실행에서 조건이 해소되면 경고를 `resolved`로 전환하지만 이력은
삭제하지 않는다. 이 과정은 주문 실행·모의 체결·실제 포트폴리오 원장을 변경하지 않는다.

`GET /api/market/operations-report?days=7`은 일별 수집 결과, 활성·최근 경고, 핵심 지수 기준일을
읽기 전용으로 제공한다. 로그인한 사용자의 **내 전략 → 주간 운영 리포트**에도 시세 품질 경고와
최근 자동 수집 요약이 함께 표시된다.

## 27단계 거부 봉 비율과 공급자 교차검증

`MARKET_DATA_REVIEW_REJECTION_PCT`(기본 1%) 이하의 거부 봉은 저장 전에 제외하고
`passed_with_minor_rejections`으로 실행 이력에만 남긴다. 이 수준은 이미 검증된 나머지 봉의
시세 표시나 주문 원장을 막지 않는다.

비율이 임계치를 초과하면 다음 설정 공급자를 자동으로 시도한다. 보조 공급자가 더 깨끗한
데이터를 반환하면 그 결과를 저장하며, 공급자 매핑 부재·장애 등으로 교차검증하지 못하면
검증된 1차 봉을 보존하되 `review_required`와 공급자 시도 이력을 남긴다. 따라서 데이터가
사라지지 않으며, 경고는 사람이 원인과 공급자 매핑을 점검할 수 있게 한다.

## 28단계 KRW/USD 독립 교차검증

기본 공급자 체인에는 `fred`가 포함되며, `FRED_SERIES_MAP=KRW=X=DEXKOUS`로 Fed H.10의
KRW/USD 일일 New York noon fixing을 사용한다. 이는 Yahoo 종가의 대체 OHLC가 아니라 독립
확인값이다. Yahoo에서 거부 봉 비율이 임계치를 넘었을 때 두 소스의 겹치는 날짜 종가를 비교하고,
최소 20일·`FX_CROSSCHECK_MAX_DEVIATION_PCT`(기본 3%) 이내면 Yahoo의 유효 OHLC는 유지한 채
`passed_with_cross_verification`으로 기록한다.

Fed H.10 fixing은 시차가 있을 수 있으므로 가장 최근 Yahoo 일봉을 덮어쓰지 않는다. 교차검증이
실패하거나 겹치는 일자가 부족한 경우에만 기존 `review_required` 경고를 유지한다.

## 29단계 공급자 품질 추세

수집 실행마다 최근 7일 `market_data_runs`의 공급자 시도를 집계해 `market_provider_health_snapshots`에
KST 날짜별로 저장한다. 실행 시도가 최소 `MARKET_DATA_PROVIDER_MIN_ATTEMPTS`(기본 3회)에 도달한 뒤,
오류율이 `MARKET_DATA_PROVIDER_MAX_ERROR_RATE_PCT`(기본 20%) 또는 거부 봉 비율이
`MARKET_DATA_PROVIDER_MAX_REJECTION_RATE_PCT`(기본 5%) 이상이면 `provider_degraded` 경고를 자동으로
생성한다. 다음 정상 실행에서 임계치 아래로 내려가면 기존 경고는 `resolved` 이력으로 전환된다.

Stooq처럼 해당 티커의 심볼 매핑이 없는 공급자 시도는 `not_applicable`로 별도 집계한다. 이는
지원 범위 밖의 조합이므로 오류율·품질 저하 경고의 분모와 분자에서 제외한다.

`/api/market/operations-report`의 `provider_health`와 `provider_health_history`는 공급자별 시도 수,
성공·오류·교차검증 수, 가중 거부 봉 비율과 최근 일일 스냅샷을 읽기 전용으로 제공한다.

## 30단계 적응형 공급자 라우팅과 재시도 대기

수집 요청은 티커 호환성을 먼저 확인해 매핑이 없는 공급자를 호출하지 않는다. 실제 네트워크·데이터
오류는 `market_provider_backoff`에 실패 연속 횟수와 재시도 가능 시각을 기록하고,
`MARKET_DATA_PROVIDER_BACKOFF_BASE_MINUTES`(기본 5분)에서 두 배씩 늘려
`MARKET_DATA_PROVIDER_BACKOFF_MAX_MINUTES`(기본 120분)까지 대기한다.

대기 중인 공급자는 다음 요청에서 건너뛰며, 최근 주간 품질이 저하된 공급자는 같은 티커를 지원하는
건강한 공급자 뒤로 보낸다. 보류·우선순위 결정은 각 실행의 `provider_routing`에, 현재 대기 상태는
`/api/market/operations-report`의 `provider_routing`에 남는다. 공급자를 모두 보류한 경우에도
마지막 검증 캐시를 사용하며, 주문·원장은 변경하지 않는다.

## 31단계 응답 시간 측정과 성능 우선순위

각 실제 공급자 호출은 `latency_ms`를 실행 이력에 남긴다. 최근 수집 이력은 공급자별 평균과
P95 응답 시간을 계산하며, 최소 표본 수를 만족한 뒤 평균이
`MARKET_DATA_PROVIDER_SLOW_LATENCY_MS`(기본 5,000ms) 이상인 공급자는 느린 상태로 표시한다.

호환·품질·재시도 대기가 같은 조건이면 더 빠른 정상 공급자를 먼저 시도한다. FRED처럼
교차검증 전용인 소스는 빠르더라도 기본 OHLCV 공급자보다 먼저 선택하지 않는다. 운영 리포트의
공급자 행에는 평균/P95와 지연 상태가 표시된다.

## 32단계 지연 악화 추세 경고

당일 평균 지연은 과거 일일 스냅샷 기준선과 비교한다. 기본적으로 최근 7일 중 최소 3개
스냅샷이 있을 때 기준선 대비 75% 이상 늘면 `provider_latency_regression` 경고를 남긴다.
`MARKET_DATA_PROVIDER_LATENCY_BASELINE_DAYS`,
`MARKET_DATA_PROVIDER_LATENCY_MIN_BASELINE_SNAPSHOTS`,
`MARKET_DATA_PROVIDER_LATENCY_REGRESSION_PCT`로 조정할 수 있다.

이 경고는 절대 5초 한도와 독립적이라, 아직 느림 상태가 아니어도 급격한 성능 저하를 조기에
보여 준다. 같은 날 스냅샷은 기준선에서 제외하므로 재실행으로 기준선이 오염되지 않으며,
경고는 운영 검토용일 뿐 자동 주문·포트폴리오 변경을 만들지 않는다.
