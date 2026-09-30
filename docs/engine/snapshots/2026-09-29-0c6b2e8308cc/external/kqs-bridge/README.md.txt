# kqs-bridge — korea-quant-system 읽기 전용 상태 브리지 (1단계)

`Ubuntu-26.04` 배포판 안, 저장소 **밖**(`/home/gram/kqs-bridge/`)에 사는 작은 HTTP 서비스.
korea-quant-system 의 로그·CSV·DB 를 **읽기만** 해서 JSON 으로 내주고, 같은 서비스가
한국어 대시보드(`/`)를 제공한다. 홈페이지(IV-TEST, `Ubuntu` 배포판)와는 2단계에서
프록시/페이지로 연결한다 — 두 배포판은 같은 WSL2 VM 이라 `localhost` 포트를 공유한다.

## 원칙

- 표준 라이브러리만(시스템 `python3`). 저장소 `.venv` 는 건드리지 않는다.
- 저장소·DB·로그에 **쓰지 않는다**: sqlite 는 `file:…?mode=ro` + `pragma query_only`, 나머지는 읽기.
- 파이프라인 스크립트·스케줄러·사전등록 문서를 수정하지 않는다(5번째 단계 추가 없음 — 파일을 실시간으로 읽는다).
- 바인드는 기본 `127.0.0.1:18100` (같은 PC 안에서만 접근). 외부 CDN·외부 요청 없음.
- 어떤 스케줄러/systemd 에도 **등록하지 않았다**(등록 방식은 결정 사항 ④).

## 실행

```bash
/home/gram/kqs-bridge/start.sh     # 백그라운드 시작 (setsid + nohup, 로그 bridge.log, pid bridge.pid)
/home/gram/kqs-bridge/status.sh    # 프로세스 · 포트 · /api/health
/home/gram/kqs-bridge/stop.sh      # 정지
python3 bridge.py --once pipeline  # 서버 없이 JSON 한 번 출력 (all|health|scheduler|pipeline|db|paper|benchmark|forward|hashlock)
```

Windows(PowerShell/실행창)에서 한 줄:

```
wsl -d Ubuntu-26.04 -- bash -lc '/home/gram/kqs-bridge/start.sh'
```

브라우저: <http://localhost:18100/>

환경변수: `KQS_BRIDGE_BIND`(기본 127.0.0.1), `KQS_BRIDGE_PORT`(기본 18100), `KQS_REPO`, `KQS_HOME`.

### 접근 경로 (2026-09-16 확인)

| 어디서 | 주소 | 결과 |
|---|---|---|
| Windows 브라우저·PowerShell | `http://localhost:18100/` | 200 (WSL2 localhost 포워딩) |
| `Ubuntu` 배포판(IV-TEST) | `http://127.0.0.1:18100/` | 200 (같은 VM, 네트워크 공유) |
| `investment_backend` 컨테이너(host 네트워크) | `http://127.0.0.1:18100/` | 200 → 2단계 프록시에 네트워크 변경 불필요 |
| `jy_investing_app`(:8000, bridge 네트워크) | `http://host.docker.internal:18100/` | 미확인 — `extra_hosts` 필요 |

### 수명(지속성)

WSL2 는 배포판 안에 살아있는 프로세스가 하나도 없으면 배포판을 내린다. `start.sh` 는 `setsid nohup` 으로
브리지를 띄우므로 실행한 터미널이 닫혀도 브리지가 남고, 브리지가 살아 있는 동안 배포판도 유지된다.
PC 재부팅 뒤에는 다시 `start.sh` 를 실행해야 한다(자동 시작 등록 = 결정 사항 ④).
확인: `status.sh` 또는 Windows 에서 `Invoke-WebRequest http://localhost:18100/api/health -UseBasicParsing`.

## 엔드포인트

| 경로 | 내용 | 캐시 |
|---|---|---|
| `/` | 대시보드 (dashboard.html, 60초 자동 새로고침, 수치 가리기 토글) | — |
| `/api/health` | 브리지 자체 상태, 감시 파일 존재 여부 | 없음 |
| `/api/scheduler` | Windows 작업 스케줄러 `KoreaQuantDailyPipeline` (PowerShell `Get-ScheduledTaskInfo`, 읽기 전용) | 120초 |
| `/api/pipeline` | `data/pipeline_run.log` 파싱 — 마지막 실행 요약(단계별 rc, DB before/after, 추천, 러너, 갭 진단, 경고), 이력, 반복 트리거 skip | 20초 |
| `/api/db` | `data/krx_daily.sqlite3` (mode=ro) 최신/직전/최초 거래일, 최신일 행 수(시장별), `collection_log` 요약 | 20초 |
| `/api/paper` | `bot_candidate_audit.csv`(AsOf 별 요약·선정 이력), `bot_active_positions.csv`, `bot_trade_log.csv` | 20초 |
| `/api/benchmark` | `benchmark_forward.csv` 심볼별 요약 + 최근 행 | 30초 |
| `/api/forward` | `/home/gram/pullback_v2_forward_runs.log` · `pullback_v4_forward_runs.log` 마지막 블록/이력 + 사전등록 v2·v4 §9 추기 목록 | 30초 |
| `/api/hashlock` | 사전등록 v1~v4 해시 표 + D-36 hashlock 문서를 실제 파일 sha256 과 재대조 | 300초 |
| `/api/all` | 위 전부 | — |

## 대시보드 패널

① 스케줄러 · 오늘의 파이프라인 ② DB ③ 평균회귀 모의투자 ④ 벤치마크 ⑤ Pullback 전향 관측 v2/v4
⑥ 해시 잠금 재검증 ⑦ 파이프라인 실행 이력 + 마지막 블록 원문.

"성과·가격 수치 가리기" 토글을 켜면 가격·수익률·거래대금 칸만 흐리게 처리한다(건수·날짜·플래그는 그대로).

## 파일

- `bridge.py` — 서버 + 수집기(파서) 전부
- `dashboard.html` — 정적 대시보드(외부 자원 없음)
- `start.sh` / `stop.sh` / `status.sh`
- `bridge.log`, `bridge.pid` — 실행 시 생성

## 다음 단계(승인 후)

- 2단계: IV-TEST 에 `routers/kqs.py`(→ `http://127.0.0.1:18100/api/*` 프록시) + `pages/kqs.html` + 사이드바 항목, 이미지 재빌드.
  `investment_backend` 는 host 네트워크라 그대로 접근 가능, `:8000` 앱은 `extra_hosts: host.docker.internal:host-gateway` 필요.
- 3단계: 라벨 붙은 SANDBOX 전략조건 실행기(`/home/gram/kqs-sandbox/<ts>/`), 라이브·사전등록 규칙 무접촉.
