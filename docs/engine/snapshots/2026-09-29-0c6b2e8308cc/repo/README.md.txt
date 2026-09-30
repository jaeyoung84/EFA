# Korea Quant System

한국 주식 일봉 데이터를 대상으로 하는 연구·백테스트 우선 프로젝트입니다. 첫 MVP는
FinanceDataReader 또는 수집한 KRX DB에서 가격을 읽고, vectorbt로 일봉 전략을 검증합니다. 실거래 주문은
의도적으로 포함하지 않았습니다.

## 기준 버전

현재 `0.1.0`은 프로젝트의 첫 번째 연구 기준 버전입니다. KRX 6자리 종목코드 검증,
OHLCV 정규화, 다음 거래일 시가 체결을 가정한 장·단기 이동평균 교차 백테스트, 거래 수수료와
슬리피지 반영, HTML 보고서 생성을 포함합니다. 실거래 및 주문 전송 기능은 포함하지 않습니다.

## 범위

- 대상: KRX 상장 주식 및 ETF
- 주기: 일봉
- 데이터: FinanceDataReader의 NAVER 가격 어댑터(교체 가능)
- 엔진: vectorbt
- 전략: SMA, 볼린저·RSI, Donchian·ATR, MACD·장기 추세, 캔들·거래량
- 체결 가정: 오늘 종가로 계산한 신호를 다음 거래일에 반영
- 기본 비용: 수수료와 슬리피지를 각각 설정
- 체결 용량: 직전 거래일 거래대금의 기본 1% 이내로 매수 수량을 제한하고 초과분은
  부분 체결하며, 위험 축소를 위한 매도 주문은 용량 제한 없이 전량 청산

## 설치

Python 3.11 이상이 필요합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## 실행

삼성전자(005930)의 2020년 이후 데이터를 사용한 예시입니다.

```powershell
korea-quant --symbol 005930 --start 2020-01-01
```

여러 종목에 동일한 전략 조건을 한 번에 적용할 수도 있습니다.

```powershell
korea-quant --symbols 005930 000660 005380 035420 105560 --start 2020-01-01
```

위 코드는 삼성전자, SK하이닉스, 현대차, NAVER, KB금융의 종목별 HTML·CSV와
전체 비교표인 `reports/summary.html`, `reports/summary.csv`를 생성합니다.

HTML과 CSV 보고서는 기본적으로 `reports/005930.html`, `reports/005930.csv`에 생성됩니다.
보고서에는 CAGR, MDD, Sharpe ratio, 총수익률, 벤치마크 수익률, 거래 횟수, 승률,
수수료·슬리피지, 월별 수익률과 자산곡선이 포함됩니다. 기본 벤치마크는 해당 종목의
매수·보유이며, 다른 KRX 종목이나 ETF는 `--benchmark 069500`처럼 지정할 수 있습니다.
내려받은 일봉은
`.cache/market-data`에 종목별로 저장되며, 이후 실행에서는 캐시 범위 밖의 날짜만 추가로
조회합니다. `--cache-dir`로 저장 위치를 바꿀 수 있습니다.

### KRX DB 차트와 백테스트

수집한 SQLite에서 종목명 또는 코드를 검색할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.cli `
  --krx-db data\krx_smoke_test.sqlite3 `
  --search 삼성
```

한 종목의 통합 보고서는 다음처럼 생성합니다. 현재 테스트 DB처럼 데이터가 짧을 때는 5일/20일
이동평균을 사용하고, 장기 데이터가 준비되면 기본 20일/60일을 사용할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.cli `
  --krx-db data\krx_smoke_test.sqlite3 `
  --symbol 005930 `
  --start 2026-07-01 `
  --end 2026-08-27 `
  --fast 5 `
  --slow 20 `
  --report reports\005930_krx.html
```

HTML에는 일봉 캔들, 거래량, 이동평균, 다음 거래일 시가 기준 진입·청산, 자산곡선 및 월별
수익률이 포함됩니다. CSV에는 요약지표, 일봉, 신호, 자산곡선과 월별 수익률이 함께 저장됩니다.
KRX 원시 일봉은 기업행동 조정 전 가격이므로 보고서에도 미조정 가격임을 표시합니다.

### 전략 선택

기본 `sma` 외에 `bollinger-rsi`를 선택할 수 있습니다. 볼린저밴드 하단 이하이면서 RSI가
과매도 기준 이하일 때 진입 조건을 관찰하고, 중심선 도달 또는 RSI 회복 시 청산 조건을
관찰합니다. 두 주문 모두 조건을 관찰한 다음 거래일 시가에 체결됩니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.cli `
  --krx-db data\krx_smoke_test.sqlite3 `
  --symbol 005930 `
  --start 2026-07-01 `
  --end 2026-08-27 `
  --strategy bollinger-rsi `
  --bb-window 20 `
  --bb-std 2 `
  --rsi-window 14 `
  --rsi-oversold 30 `
  --rsi-exit 50 `
  --report reports\005930_bollinger_rsi.html
```

보고서의 볼린저밴드와 RSI 패널은 매매 판단에 사용된 값을 그대로 표시합니다. 짧은 데이터는
초기 지표 계산 구간이 대부분을 차지하므로 실제 비교에는 최소 3년 이상의 일봉을 권장합니다.

Donchian·ATR 추세 전략은 이전 20일 고가를 종가로 돌파하면 진입하고, 이전 10일 저가 또는
진입 후 최고 종가에서 3 ATR만큼 내려온 추적손절선에 도달하면 청산합니다. 채널 계산에서
당일 고가·저가는 제외하며 모든 주문은 다음 거래일 시가에 반영합니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.cli `
  --krx-db data\krx_smoke_test.sqlite3 `
  --symbol 005930 `
  --start 2026-07-01 `
  --end 2026-08-27 `
  --strategy donchian-atr `
  --donchian-entry 20 `
  --donchian-exit 10 `
  --atr-window 14 `
  --atr-multiplier 3 `
  --report reports\005930_donchian_atr.html
```

MACD·장기 추세 전략은 기본 MACD 12·26·9의 상향 교차가 발생하고 종가가 200일 단순이동평균
위에 있을 때만 진입합니다. MACD 하향 교차 또는 장기선 이탈 시 청산하며 주문은 다음 거래일
시가에 반영됩니다. 실제 연구에는 기본 200일선을 사용하고, 짧은 시험 DB의 기능 확인에만
`--trend-window 20`처럼 낮은 값을 사용합니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.cli `
  --krx-db data\krx_smoke_test.sqlite3 `
  --symbol 005930 `
  --start 2026-07-01 `
  --end 2026-08-27 `
  --strategy macd-trend `
  --macd-fast 12 `
  --macd-slow 26 `
  --macd-signal 9 `
  --trend-window 20 `
  --report reports\005930_macd_trend.html
```

캔들·거래량 전략은 상승 장악형 또는 양봉 망치형이 나오고 현재 거래량이 이전 20일 평균의
1.5배 이상일 때 진입합니다. 거래량이 확인된 하락 장악형은 청산 신호로 사용합니다. 이전
거래량 평균은 당일 거래량을 제외하므로 급증한 당일 거래량이 기준 자체를 높이지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.cli `
  --krx-db data\krx_smoke_test.sqlite3 `
  --symbol 005930 `
  --start 2026-07-01 `
  --end 2026-08-27 `
  --strategy candle-volume `
  --volume-window 20 `
  --volume-multiplier 1.5 `
  --hammer-shadow-ratio 2 `
  --report reports\005930_candle_volume.html
```

### 코스피·코스닥 횡단면 모멘텀

월말 종가 기준 과거 126거래일 수익률이 양수인 종목을 계산합니다. 최근 20거래일 평균
거래대금 상위 후보군에서 모멘텀 상위 20종목을 골라 다음 거래일 시가에 동일 비중으로
리밸런싱합니다. 조회 기간에 존재했던 모든 종목 행을 사용하므로 현재 상장 종목만으로
과거 유니버스를 다시 만들지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.momentum_cli `
  --krx-db data\krx_daily.sqlite3 `
  --start 2020-01-01 `
  --end 2026-08-27 `
  --market ALL `
  --universe KOSPI200 `
  --lookback 126 `
  --liquidity-window 20 `
  --top 20 `
  --report reports\momentum.html
```

거래일 데이터 사이에 14일을 초과하는 공백이 있으면 잘못된 다음 거래일 체결을 막기 위해
실행을 중단합니다. 장기 백테스트 전에 해당 기간의 KRX 수집이 연속적으로 완료됐는지
`krx-data status`와 수집 로그를 확인해야 합니다. KRX 원시 가격에는 기업행동 조정과
상장폐지 최종 정산 정보가 없으므로 장기 성과 해석 시 별도 보완이 필요합니다.

`--universe`를 지정하면 각 월말에 당시 알려진 구성종목만 선택 후보가 됩니다. 편입 전 가격
이력은 모멘텀 계산에 사용할 수 있지만 편입 효력 발생 전에는 매수 후보가 될 수 없습니다.
보고서에는 같은 시점별 구성종목을 다음 거래일 시가에 동일가중하는 비용 반영 벤치마크와
전략·벤치마크의 목표 회전율이 함께 표시됩니다.

### RSI·OBV 교차검증

RSI가 과매도 기준 30을 상향 돌파하고 OBV가 상승하면서 9일 OBV 이동평균 위에 있을 때
진입합니다. RSI가 중립 수준인 50에 도달하면 청산합니다 — 볼린저·RSI가 중간밴드를
목표로 하는 것과 같은 기준으로, 되돌림이 완료된 지점입니다.

예전 청산 규칙은 `RSI 70 이상 + OBV 약화`였고 두 조건 모두 잘못돼 있었습니다. OBV 약화
조건은 느슨한 OR라 하락·보합일이면 거의 항상 참이어서 실질적으로 구속력이 없었고, 남은
`RSI 70`은 30에서 회복한 종목에게 반대쪽 극단까지 갈 것을 요구했습니다. 도달하지 못하면
청산 자체가 일어나지 않아 10년 표본에서 포지션의 9.4%가 끝까지 청산되지 않은 채 평균
233세션을 점유했습니다. 중립 청산으로 바꾸면 평균 보유가 18세션으로 줄고 하위 5% 손실이
−61%에서 −22%로 축소되며 보유 세션당 수익률이 약 4배가 됩니다. 대안으로 가격 손절을
5~20% 구간에서 검증했으나 모든 폭에서 기대값이 음수가 되어 채택하지 않았습니다. 하락을
사는 진입 규칙과 하락에 파는 손절이 서로 모순되기 때문입니다.

차트에는 RSI와 OBV를 별도 패널로 표시하고 모든 주문은 다음 거래일 시가에
반영합니다. OBV는 실제 투자주체별 자금 흐름이 아니라 종가 방향으로 부호를 붙인 누적
거래량이라는 점에 유의해야 합니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.cli `
  --krx-db data\krx_smoke_test.sqlite3 `
  --symbol 005930 `
  --start 2026-07-01 `
  --end 2026-08-27 `
  --strategy rsi-obv `
  --rsi-window 14 `
  --rsi-oversold 30 `
  --rsi-obv-exit 50 `
  --obv-signal 9 `
  --report reports\005930_rsi_obv.html
```

### EMA30·구조적 저항선 돌파

WSL의 EMA30 원본 아이디어를 백테스트용으로 이식한 전략입니다. 30일 EMA 위에서 과거에
최소 두 번 확인된 구조적 저항선을 강한 양봉으로 상향 돌파하면 진입합니다. 강한 양봉은
몸통이 이전 20일 평균 몸통의 1.5배 이상이고 종가가 당일 고가·저가 범위의 상단 25% 안에
있는 봉으로 정의합니다. 저항 가격은 기본 ±0.5% 범위의 확정 고점을 군집화합니다.

진입 시점에 이미 확정된 가장 최근 구조적 저점을 손절선으로 고정합니다. 종가가 이 손절선
또는 EMA 아래로 내려가면 다음 거래일 시가에 청산합니다. 일봉 데이터에서는 장중 손절가
체결을 보장할 수 없으므로 갭 하락 위험이 수익률에 그대로 반영됩니다.

원본의 좌우 캔들 고점 탐색을 그대로 전체 배열에 적용하면 미래 캔들이 과거 신호에 섞일 수
있습니다. 이 버전은 오른쪽 캔들이 마감된 날짜에 고점을 확정하고 그 시점까지 알려진 고점만
사용합니다. 신호는 그 다음 거래일 시가에 체결됩니다. 원본의 KB API 키, `.env`, 임시 가격과
주문 주석은 가져오지 않았으며 실거래 주문은 비활성 상태입니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.cli `
  --krx-db data\krx_smoke_test.sqlite3 `
  --symbol 213420 `
  --start 2026-07-01 `
  --end 2026-08-27 `
  --strategy ema30-resistance `
  --ema-window 30 `
  --resistance-threshold 0.005 `
  --resistance-touches 2 `
  --strong-body-lookback 20 `
  --strong-body-multiplier 1.5 `
  --minimum-close-position 0.75 `
  --report reports\213420_ema30_resistance.html
```

## KRX 전 종목 일봉 수집

KRX Open API의 유가증권·코스닥 일별매매정보를 날짜별로 받아 SQLite에 UPSERT합니다.
두 API의 활용신청 승인을 받은 뒤 인증키는 환경변수로만 설정합니다.

```powershell
$env:KRX_AUTH_KEY = "발급받은 인증키"
krx-data backfill --start 2010-01-04 --end 2026-08-28
```

중단돼도 같은 명령으로 이어받을 수 있습니다. 이후 장 마감 데이터가 확정되면 최근 10일을
다시 받아 누락과 정정을 보완합니다. 고유키 UPSERT 방식이라 중복 행은 생기지 않습니다.

```powershell
krx-data update --lookback-days 10
krx-data status
```

기본 DB는 `data/krx_daily.sqlite3`입니다. 거래정지일의 원본 0 가격은 DB에 보존하고,
차트용 조회에서는 종가 기준의 평평한 캔들로 반환합니다.

### KRX 데이터 품질검사

감사 명령은 SQLite를 읽기 전용으로 열고 다음 항목을 검사합니다.

- 날짜별 KOSPI·KOSDAQ 중 한 시장 누락
- 수집 로그의 행 수와 실제 저장 행 수 불일치
- 수집 로그 없이 저장된 가격 묶음
- 중복 날짜·종목 키와 잘못된 6자리 종목코드
- 종가·거래량 및 OHLC 범위 이상
- 가격 거래일 사이 14일 초과 공백

수집이 끝난 뒤 JSON 보고서를 저장합니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.krx_cli `
  --db data\krx_daily.sqlite3 `
  audit `
  --output reports\krx_audit.json `
  --sample-limit 20
```

출력의 `healthy`가 `true`이고 모든 `issue_counts`가 0이면 기본 정합성 검사를 통과한
것입니다. 자동 작업에서는 문제가 있을 때 종료코드 1을 반환하는 `--strict`를 추가할 수
있습니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.krx_cli `
  --db data\krx_daily.sqlite3 `
  audit `
  --strict
```

수집 중 실행하면 현재 처리 중인 날짜에서 한 시장만 완료된 상태가 잠시 표시될 수 있으므로
최종 판정은 전체 수집이 끝난 뒤 실행합니다. 이 검사는 기업행동 조정가격이나 상장폐지
정산가격의 경제적 정확성까지 보장하지는 않습니다.

### 시점별 지수 유니버스

KRX 무료 Open API는 지수 시세는 제공하지만 구성종목 이력은 제공하지 않습니다. 승인된
구성종목 파일을 확보한 뒤, 스냅샷별 CSV를 적재합니다. 기본 종목코드 열은 `Symbol`,
`symbol`, `종목코드`, `단축코드`, `ISU_CD` 중에서 자동 인식하며 문자열로 읽어 앞자리 0을
보존합니다.

```powershell
krx-data --db data\krx_daily.sqlite3 universe-import `
  --universe KOSPI200 `
  --effective-date 2024-06-14 `
  --available-date 2024-06-10 `
  --source "KRX licensed constituent file" `
  --file data\universes\kospi200_20240614.csv
```

`effective-date`는 구성종목이 실제 적용되는 날짜, `available-date`는 해당 파일을 연구자가
알 수 있게 된 날짜입니다. 조회 시 두 조건을 모두 만족하는 가장 최근 스냅샷만 선택하므로
나중에 확보한 과거 파일이 이전 백테스트 의사결정에 섞이지 않습니다.

```powershell
krx-data --db data\krx_daily.sqlite3 universe-show `
  --universe KOSPI200 `
  --as-of 2024-06-14
```

### 전체 단일 종목 전략 비교

한 종목의 동일한 일봉·기간·초기자금·수수료·슬리피지 조건에서 현재 구현된 모든 전략을
실행합니다. `summary.html`에서 전략별 CAGR, MDD, 샤프지수, 총수익률, 거래 횟수, 승률,
수수료, 진입·청산 신호 수를 비교하며 전략 이름을 누르면 상세 차트가 열립니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.comparison_cli `
  --krx-db data\krx_daily.sqlite3 `
  --symbol 213420 `
  --start 2021-08-30 `
  --end 2026-08-27 `
  --cash 10000000 `
  --fee 0.00015 `
  --slippage 0.0005 `
  --output-dir reports\comparison\213420
```

현재 비교 대상은 SMA, 볼린저·RSI, Donchian·ATR, MACD·장기 추세, 캔들·거래량,
RSI·OBV, EMA30·구조 돌파입니다. 종합표의 높은 성과는 전략 선택의 결론이 아니며 이후
표본 외 검증과 파라미터 민감도 검사를 통과해야 합니다.

### EMA30 전체 종목 후보 검색

코스피·코스닥의 최신 종목을 EMA30 구조 돌파 전략으로 검사합니다. 전략 계산 전에 데이터
길이와 최근 20일 평균 거래대금으로 먼저 걸러 계산량을 줄입니다. 결과는 오늘 신호, 오늘
진입·청산 예정, 보유 상태, 저항선 3% 이내 접근, 신호 없음, 유동성·데이터 부족 및 오래된
종목으로 분류합니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.scanner_cli `
  --krx-db data\krx_daily.sqlite3 `
  --start 2025-01-01 `
  --end 2026-08-27 `
  --market ALL `
  --minimum-trading-value 1000000000 `
  --liquidity-window 20 `
  --near-percent 0.03 `
  --account 10000000 `
  --risk 0.01 `
  --output reports\ema30_scan.html
```

`suggested_shares`는 계좌의 1% 허용손실과 구조적 손절선까지의 가격 차이로 계산한 연구용
수량이며 주문이 아닙니다. 다음 거래일 실제 시가는 알 수 없으므로 당일 신호 후보의 현재
종가를 임시 진입 참고가로 사용합니다. 갭 상승·하락 후에는 실제 위험이 달라질 수 있습니다.

### 공통 신호 후보 우선순위

최신 거래일 종가까지 관측된 공통 매수 신호를 다음 거래일 검토 후보로 변환합니다. 최근
20일 평균 거래대금으로 먼저 거른 뒤 벤치마크 대비 63일·126일 상대강도와 252일 고점
근접도로 정렬합니다. 이는 종목을 **우선 검토**하기 위한 기능이며 자동주문이나 수익
예측이 아닙니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.candidate_scanner_cli `
  --krx-db data\krx_daily.sqlite3 `
  --start 2021-08-30 `
  --end 2026-08-27 `
  --kospi-benchmark 069500 `
  --kosdaq-benchmark 229200 `
  --minimum-trading-value 5000000000 `
  --minimum-signals 2 `
  --output reports\confluence_candidates.html
```

장 마감 후 추천은 다음 명령으로 확인합니다.

```powershell
korea-quant-daily-picks `
  --krx-db data\krx_daily.sqlite3 `
  --max-picks 3 `
  --minimum-average-trading-value 5000000000 `
  --kospi-tier 200 `
  --kosdaq-tier 150
```

`--max-picks`(기본 3, 최대 3)는 스윙+장기보유를 합산한 당일 추천 종목 수 상한입니다.
신호가 상한보다 적으면 그보다 적게 추천하며(0건도 가능), 카테고리별로 따로 채우지
않습니다 — 카테고리별 상한을 두면 하루 최대 6종목까지 나갈 수 있어 "TOP3 초과 종목선출
금지" 기준이 깨지기 때문입니다. 상한은 채워야 할 목표가 아닙니다. 검증 통과 신호가 0개면
추천하지 않고, 1개 또는 2개만 있어도 그 수만 추천합니다. 3개를 초과한 날에는 시가총액
순위처럼 **신호 시점에 알 수 있는 결정적 기준**으로만 최대 3개를 고릅니다.

추천은 확인된 8개 전략의 confluence 조합이 아니라, 각자 고유한 반전(mean-reversion)
청산 로직을 유지한 **두 개의 독립 전략**에서 나옵니다. confluence로 결합하면 모든
구성 전략에 공통 EMA30 이탈 청산이 강제로 적용되는데, 반전 진입은 이미 EMA30 아래에서
발생하는 경우가 많아 진입 직후(평균 1거래일) 청산되어 버립니다. 2021-08-30~2026-08-27
데이터로 확인한 결과입니다.

| 구분 | 전략 | 청산 조건 | 평균 보유 | KOSPI 승률 | KOSDAQ 승률 |
|---|---|---|---|---|---|
| 스윙 | 볼린저·RSI 반전 | 중단선 회귀 또는 RSI 50 회복 | 약 3~4주 | 71%(n=440) | 67%(n=340) |
| 장기보유 | RSI·OBV 반등 | RSI 과매수(70+) + OBV 약화 | 약 6~9개월 | 75%(n=72) | 64%(n=74) |

두 후보군 모두 KOSPI200·KOSDAQ150 벤치마크 유니버스를 대상으로 하며, **시장별로 각각**
시가총액 상위 `--kospi-tier`(기본 200)·`--kosdaq-tier`(기본 150) 이내 종목으로
제한합니다(KOSPI·KOSDAQ 순위를 합쳐서 매기면 KOSPI 대형주가 KOSDAQ 종목을 전부
밀어내므로 반드시 시장별로 분리 계산). 실제 KOSPI200·KOSDAQ150 공식 편입 이력은
수집하지 않았으므로 시가총액 순위를 시점 정확한 근사치로 사용합니다. 위 표의 승률은
상위 100위 기준으로 측정한 값이라 이 확대 이전 수치입니다. 출력 가격은 실제 체결가가
아닌 신호일 종가입니다.

추천 봇은 브로커 주문을 실행하지 않습니다. `BUY_SIGNAL`은 다음 거래일이 수집될 때
거래 가능한 경우에만 `BUY_FILLED`로 전환됩니다. 다음 거래일이 거래정지 또는 상한가
잠김이면 백테스트와 동일하게 `BUY_CANCELLED`로 기록하고 주문을 이월하지 않습니다.
각 전략 고유의 청산 조건에서 나온 `SELL_SIGNAL`은 거래정지·하한가 잠김이 해제된 최초
거래 가능일에 `SELL_FILLED`로 전환되며 모두 해당 거래일 시가의 **모의체결**입니다. 모든
이벤트와 포지션 기록에는 `SignalType`(`swing`/`long_term`)이 함께 저장되어 청산 감시가
포지션별로 올바른 조건을 사용합니다. 같은 날짜·이벤트 종류·종목코드는 한 번만
기록됩니다. `GrossReturnPct`는 시가 기준 단순 수익률이고, `NetReturnPct`는
`--fee-rate`, `--sell-tax-rate`, `--slippage-rate`를 반영합니다. 기존 추천 종가만
저장된 포지션은 `LEGACY_REFERENCE`로 보존하지만 모의체결 수익률에서는 제외합니다.
`SignalType` 도입 전의 기존 모의 포지션은 `legacy_confluence`로 변환해 종전 EMA30
이탈 기준으로 계속 감시합니다.

confluence(8개 전략 조합) 기반 스캐너와 백테스트 CLI들은 연구용으로 계속 사용할 수
있지만, 위 이유로 실거래 추천 경로에서는 사용하지 않습니다.


### 반전 전략 목표 검증

일일 반전 후보를 채택하기 전에는 전체 기간의 높은 승률만 보지 않고, 순차적 표본 외 구간의
청산 완료 거래로 목표를 판정합니다. 기본 목표는 비용(수수료·매도세·슬리피지) 반영 승률
60% 이상, 손익비(Profit Factor) 1.30 이상, 중앙값 순수익률 0% 초과, 그리고 표본 외 구간
중 순이익으로 끝난 구간의 비율 60% 이상입니다. 스윙은 최소 100건, 장기보유는 최소 30건의
청산 완료 거래가 필요합니다. KOSPI·KOSDAQ과 합산 결과를 각각 보며, 목표 판정 근거는
표본 외 거래뿐입니다.

거래당 평균 순수익률은 보고는 하되 판정 기준이 아닙니다. 시간 분모가 없어 자본을 오래
묶어둘수록 유리하게 나오기 때문입니다(233세션 동안 +6.77%가 17.8세션 동안 +1.99%보다
높게 나오지만, 자본 효율로는 네 배 나쁩니다). 손익비만으로도 부족합니다 — 폐기된 rsi-obv
청산 규칙의 손익비는 1.600으로 현행 1.562보다 높았고, 두 규칙을 실제로 갈라놓은 것은
순이익 구간 비율(48.5% 대 71.4%)이었습니다.

최종 성공 목표는 비용 반영 **연간 실제 포트폴리오 수익률(CAGR) 20% 이상**입니다. 이는
종목별 수익률을 더한 값이나 거래당 수익률이 아니라, 스윙+장기보유 신호가 하나의 자본
계좌에서 하루 최대 3개·동일 종목 중복 없이 실행됐을 때의 수익률입니다. 거래 단위 목표를
통과해도 이 배포 CAGR이 20% 미만이면 최종 성공이 아닙니다. 후보가 몰린 날에는 미래
수익률이나 사후 정렬이 아닌 신호 시점의 결정적 순위만 사용합니다. 근거·재측정 조건은
DECISIONS.md의 D-7과 D-9에 있습니다.

~~~powershell
korea-quant-reversion-validation `
  --krx-db data\krx_daily.sqlite3 `
  --start 2021-08-30 `
  --end 2026-08-27 `
  --training-sessions 252 `
  --testing-sessions 63 `
  --kospi-tier 200 `
  --kosdaq-tier 150 `
  --minimum-win-rate 0.60 `
  --minimum-profit-factor 1.30 `
  --minimum-profitable-window-share 0.60 `
  --report reports\reversion_validation.html
~~~

전략군은 --profile reversion(평균회귀·승률형), --profile breakout(추세·돌파 수익률형), --profile pullback(추세 눌림목 혼합형), --profile all로 분리해 검증합니다. 서로 다른 보유 구조의 결과를 합산해 목표를 판단하지 않습니다.

이 검증은 기본적으로 보고서 이름의 checkpoints 폴더에 종목 50개 단위 결과를 즉시 저장합니다. 중단된 경우 같은 옵션으로 다시 실행하면 완료된 묶음을 재사용하며, 다른 조건에는 새 --checkpoint-dir을 지정해야 합니다. 고정된 조건의 연구 결과를 보고할 뿐, 미래 수익이나 실제 주문을 보장하지 않습니다.

### 후보 랭킹 사후 검증

랭킹 자체를 채택하기 전, 과거 공통 신호일의 상위 후보(`top 10`)가 나머지 후보보다 이후
5·20·60거래일 수익률에서 우위였는지 검증합니다. 신호·순위는 전일 종가까지의 정보만 쓰고,
진입과 청산은 각각 다음 거래일 및 보유기간 종료일의 시가로 계산합니다. 수수료와 슬리피지는
왕복으로 반영합니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.candidate_validation_cli `
  --krx-db data\krx_daily.sqlite3 `
  --start 2021-08-30 `
  --end 2026-08-27 `
  --benchmark 069500 `
  --top-n 10 `
  --minimum-trading-value 5000000000 `
  --fee 0.00015 `
  --slippage 0.0005 `
  --output reports\candidate_ranking_validation.html
```

이미 생성한 `*_events.csv`가 있으면 `--events-csv`를 사용해 공통 신호를 다시 계산하지 않고
`--top-n` 값만 빠르게 바꿔 비교할 수 있습니다.

`--require-volume`은 공통 신호에 캔들·거래량 또는 RSI·OBV 계열 확인을 반드시 포함시켜,
가격 신호만 겹친 후보를 제외합니다.

### 변동성 수축·거래량 돌파

수축이 전 거래일까지 확인된 뒤, 당일 종가가 이전 20일 고점을 돌파하고 거래량이 이전 20일
평균의 1.5배 이상일 때 다음 거래일 시가에 진입합니다. 종가가 EMA30 아래로 내려가면 다음
거래일 시가에 청산합니다. 이는 공통 매수 신호 엔진에서 변동성·거래량 확인 역할을 맡는
독립 전략입니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.cli `
  --krx-db data\krx_daily.sqlite3 `
  --symbol 213420 `
  --start 2021-08-30 `
  --end 2026-08-27 `
  --strategy volatility-squeeze `
  --ema-window 30 `
  --squeeze-band-window 20 `
  --squeeze-atr-window 14 `
  --squeeze-lookback 120 `
  --squeeze-percentile 0.2 `
  --squeeze-breakout-window 20 `
  --volume-window 20 `
  --volume-multiplier 1.5 `
  --fee 0.00015 `
  --slippage 0.0005 `
  --report reports\213420_volatility_squeeze.html
```

### 공통 매수 신호 v1

여러 전략의 다음 거래일 시가 주문 신호가 같은 날짜에 겹치는지를 계산합니다. 기본값은 최소
2개 전략의 동시 지지와 추세·돌파 계열 1개 이상입니다. `--confluence-require-volume`을 추가하면
캔들·거래량 또는 RSI·OBV 계열 신호도 최소 1개 필요합니다. 공통 신호가 많다고 자동으로 좋은
전략은 아니므로, 반드시 단일 전략·워크포워드 결과와 같은 비용 조건에서 비교합니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.cli `
  --krx-db data\krx_daily.sqlite3 `
  --symbol 213420 `
  --start 2021-08-30 `
  --end 2026-08-27 `
  --strategy confluence `
  --confluence-minimum-signals 2 `
  --cash 10000000 `
  --fee 0.00015 `
  --slippage 0.0005 `
  --report reports\213420_confluence_v1.html
```

### 워크포워드 표본 외 검증

한 기간의 백테스트 결과만으로 전략을 판단하지 않도록, 고정한 전략 파라미터를 순차적인
표본 외 구간에서 독립적으로 검증합니다. 기본값은 252거래일의 이력으로 지표를 계산하고,
그 다음 63거래일씩 평가합니다. 각 평가 구간은 현금과 포지션을 새로 시작하며, 미래 구간의
가격은 사용하지 않습니다. 이는 파라미터 최적화 기능이 아니라 전략 안정성 확인용입니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.walkforward_cli `
  --krx-db data\krx_daily.sqlite3 `
  --symbol 213420 `
  --start 2021-08-30 `
  --end 2026-08-27 `
  --strategy ema30-resistance `
  --training-sessions 252 `
  --testing-sessions 63 `
  --cash 10000000 `
  --fee 0.00015 `
  --slippage 0.0005 `
  --report reports\213420_ema30_walkforward.html
```

보고서의 각 행은 서로 독립된 표본 외 기간입니다. 여러 구간에서 손실·큰 낙폭·거래 부재가
반복되는 전략은 전체 기간 성과가 좋아도 신뢰하지 않습니다.

### 시장 추세 필터

선택적으로 대표 ETF의 종가가 장기 이동평균선 위에 있을 때만 신규 매수를 허용할 수 있습니다.
기본값은 없으며, 예를 들어 코스피 대형주 방향은 `069500`(KODEX 200)으로 확인할 수 있습니다.
시장 종가로 판단한 뒤 다음 거래일 시가에만 진입하므로 미래 정보를 사용하지 않습니다. 청산
신호는 시장 상태와 무관하게 그대로 실행됩니다.

기존 백테스트·전략 비교·워크포워드·민감도 검사 명령에 아래 두 옵션을 함께 추가할 수 있습니다.

```powershell
  --market-filter 069500 `
  --market-window 200
```

이 필터가 수익을 보장하는 것은 아닙니다. 필터를 켠 결과도 끄고 난 결과와 동일한 비용 조건으로
비교하고, 표본 외 구간별 낙폭과 거래 수 감소까지 함께 확인해야 합니다.

### ETF 일봉 수집

시장 추세 필터에 사용할 ETF가 기존 주식 DB에 없을 경우, ETF만 별도로 백필할 수 있습니다.
처음 실행하면 기존 코스피·코스닥 행을 보존한 채 DB 구조를 ETF 저장 가능 형태로 한 번 변환합니다.
먼저 하루치로 인증키와 API 응답을 확인한 뒤, 같은 명령의 날짜만 5년 범위로 늘립니다.

```powershell
# 현재 PowerShell 창에서만 키를 사용합니다. 실제 키 값을 채우세요.
$env:KRX_AUTH_KEY = "발급받은-KRX-인증키"

# 1) 하루치 ETF 수집 확인
.\.venv\Scripts\python.exe -m korea_quant.krx_cli `
  --db data\krx_daily.sqlite3 backfill `
  --start 2026-08-27 --end 2026-08-27 `
  --markets ETF --delay 0

# 2) 5년 ETF 백필 (중단해도 같은 명령으로 이어서 실행 가능)
.\.venv\Scripts\python.exe -m korea_quant.krx_cli `
  --db data\krx_daily.sqlite3 backfill `
  --start 2021-08-30 --end 2026-08-27 `
  --markets ETF --delay 0.1
```

완료 후 `069500`처럼 실제로 저장된 ETF 코드를 `--market-filter`에 넣어 기본 결과와 필터 결과를
같은 기간·비용 조건에서 비교합니다.

### EMA30 파라미터 민감도 검사

EMA 기간, 수평 저항선 군집 허용오차, 강한 양봉의 몸통 배수를 작은 범위에서 모두 조합해
비교합니다. 가장 높은 한 행을 고르는 최적화가 아니라, 이웃한 여러 값에서도 결과가 유지되는
안정 구간을 찾는 도구입니다. 안정 구간 후보는 반드시 위 워크포워드 검증으로 다시 확인합니다.

```powershell
.\.venv\Scripts\python.exe -m korea_quant.sensitivity_cli `
  --krx-db data\krx_daily.sqlite3 `
  --symbol 213420 `
  --start 2021-08-30 `
  --end 2026-08-27 `
  --ema-windows "25,30,35" `
  --resistance-thresholds "0.003,0.005,0.007" `
  --body-multipliers "1.25,1.5,1.75" `
  --fee 0.00015 `
  --slippage 0.0005 `
  --report reports\213420_ema30_sensitivity.html
```

기본값은 27개 조합이며, 한 번에 최대 125개까지만 허용합니다. 이는 우연히 맞는 값으로 과도하게
탐색하는 위험과 실행 시간을 함께 제한하기 위한 장치입니다.

## AWS 자동 수집 배포

AWS SAM 템플릿은 매주 월~금 `Asia/Seoul` 18:30에 Lambda를 실행합니다. Lambda는 최근
10일의 코스피·코스닥 데이터를 다시 받아 날짜·시장별 Parquet 파일로 S3에 덮어씁니다.
S3는 공개 접근을 차단하고, 수집 실패는 SQS Dead Letter Queue에 보관합니다.

필요한 로컬 도구는 AWS CLI, AWS SAM CLI, Docker Desktop입니다. 먼저 AWS 인증을 설정하고
KRX 키를 Parameter Store의 Standard SecureString으로 등록합니다.

```powershell
aws configure
aws ssm put-parameter `
  --name "/korea-quant/krx-auth-key" `
  --type "SecureString" `
  --value "$env:KRX_AUTH_KEY" `
  --overwrite
```

키 값이 비어 있지 않은지 확인한 뒤 이미지와 인프라를 배포합니다.

```powershell
sam build
sam deploy --guided
```

배포 출력의 `DataBucketName`을 확인한 후, 15분을 넘길 수 있는 최초 과거 백필은 로컬에서
S3로 직접 실행합니다. 완료된 Parquet 경로는 건너뛰므로 중단 후 같은 명령으로 재개됩니다.

```powershell
krx-s3-backfill --bucket "배포된-버킷명" --start 2010-01-04
```

Lambda에는 인증키 값이 아니라 `/korea-quant/krx-auth-key`라는 Parameter Store 경로만
전달됩니다. 실제 매매 또는 증권사 주문 기능은 포함하지 않습니다.

## 테스트

```powershell
pytest
ruff check .
```

## 다음 단계

1. 시점별 유니버스 기준 벤치마크 및 회전율 보고서 확장
2. 표본 외 및 워크포워드 검증
3. 충분한 검증 후 별도 모의매매 모듈 설계

> 백테스트는 미래 수익을 보장하지 않습니다. 데이터 편향, 거래비용, 세금 및 실제 체결 차이를
> 별도로 검증해야 합니다.


### 동결 기준선 전향 감사

일일 추천 실행 후에는 추천 규칙을 바꾸지 않고 후보·모의체결 연결 상태만 읽을 수 있습니다.
아래 명령은 D-26/D-27/D-28의 전향 표본용이며, 기준일은 2026-08-28보다 앞설 수 없습니다.

PowerShell: .\.venv\Scripts\python.exe -m korea_quant.prospective_audit_cli --since 2026-08-28 --report reports\prospective_audit.html

리포트는 선정 수, 매수 신호·체결·취소, 청산, 미청산 수와 실현 모의수익률만 서술적으로
표시합니다. 새 신호의 우열·전략 PASS/FAIL·랭킹은 판단하지 않으며, 보고서로 현재 추천
규칙을 재조정해서는 안 됩니다.
