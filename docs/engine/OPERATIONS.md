# EINT-007 / 007A — AWS PC 독립 운영(2026-09-29 배포 완료)

아래 배포 절차는 2026-09-29에 1~8단계까지 실행했고 9단계(병행 관찰)가 진행 중이다(EINT-007A).
재배포·복구 시에도 같은 순서를 따른다.
주문·자동 모의체결·실거래·개인 원장 쓰기는 어느 단계에도 없다. 원본 `Ubuntu-26.04`의
기존 파이프라인(08:20 KST, 자동 모의체결 포함 `daily_picks_cli`)은 건드리지 않는다.

## 백엔드 자동 배포 — GitHub Actions → SSM (2026-09-30부터)

main에 푸시하면 `.github/workflows/deploy.yml`이 다음 순서로 실행한다. SSH 포트·장기 AWS 키·레지스트리 토큰은 쓰지 않는다.

1. `kqs-checks`: KQS·수동 원장·학습 데이터 회귀와 **레버리지 DCA·NYSE 캘린더 회귀**(격리 Mongo, 운영 DB 미접근).
2. `build`: 통과한 커밋의 추적 파일만 `git archive`로 묶어 비공개 S3 `investing-app-deploy-…/gha/<sha>/`에 올린다(14일 뒤 자동 삭제).
3. `deploy`: GitHub OIDC로 `github-actions-investing-deploy` 역할을 받아 EC2에 SSM 명령을 보낸다.
   서버는 `/opt/investing-app-releases/<sha>`에 풀고 같은 커밋의 `infra/ops/deploy-backend-release.sh`를 실행한다:
   읽기 전용 사전검사(`kqs-deployment-preflight.sh`) → 서버에서 이미지 빌드(기존 pip 캐시 재사용) → **백엔드만** 교체 →
   2분 안에 `/api/health`가 정상이 아니면 직전 이미지·compose로 자동 복구 → `DEPLOYED_COMMIT` 기록 → 이 앱 이미지만 정리(현재·직전 유지).

- 권한: 역할은 `repo:jaeyoung84@320470030/IV-TEST@1355568379:environment:production`(GitHub immutable subject, 소유자·저장소 ID 고정)만 받을 수 있고, `production` 환경은 main 브랜치만 허용한다.
  권한은 S3 `gha/*` 업로드, 이 인스턴스·`AWS-RunShellScript`에 대한 SendCommand, 명령 결과 조회뿐이다.
- 이 배포는 Mongo/Qdrant/Meilisearch 볼륨, `app/backend/.env`, KQS 상태 DB, systemd 타이머, 엔진 worker 이미지를 건드리지 않는다.
  엔진 코드·타이머·`infra/systemd` 변경은 이 절 아래의 수동 절차로 따로 반영한다.
- 이미지를 GitHub에서 빌드하지 않는 이유: 백엔드 이미지가 3.66GB이고 서버 여유 공간이 약 6GB라, 레이어를 공유하지 않는
  외부 이미지를 받으면 디스크가 부족하다. 서버 빌드는 변경된 레이어만 다시 만든다.
- 확인: GitHub Actions의 `Build and Deploy` 실행 기록, 서버 `/opt/investing-app/DEPLOYED_COMMIT`.

## EINT-010 로컬 보완 — 운영에 아직 적용하지 않음

- 현재 자동배포 workflow는 `/opt/investment-analysis`를 사용하지만 아래 AWS 운영 기록은
  `/opt/investing-app` 기준이다. 승인 없이 경로 이동·재배포하지 않는다.
- 로컬 `docker-compose.prod.yml`의 홈페이지 상태 mount는
  `${KQS_STATE_DIR:-/opt/investing-app/state/kqs}` → `/app/state/kqs`(읽기 전용)이다.
  `create_host_path: false`로 잘못된 경로에 빈 추천 저장소를 만들지 않는다.
- 신규 `infra/ops/kqs-deployment-preflight.sh`는 기존 환경파일·추천 DB·읽기 전용 mount와
  현재 Compose 프로젝트/Mongo·Qdrant·Meilisearch 볼륨의 이름을 **조회만** 한다.
  비표준 볼륨·프로젝트·경로이면 종료하며, 새 볼륨 생성이나 자동 마이그레이션을 하지 않는다.
- 사전 검사는 임시 파일·가짜 Docker로만 검증했다. 현재 AWS 프로젝트/볼륨 식별자는 재확인 대상이다.
  기존 자동배포 workflow 연결은 승인 대기이며 이 문서의 과거 배포 기록을 새 검증으로 간주하지 않는다.
- [릴리스 준비 점검](RELEASE_READINESS.md), [EINT-010 검증](verification/EINT-010.json).

## EINT-011 로컬 화면 보완 — 운영에 아직 적용하지 않음

상태·후보·패킷 화면의 기본 경로를 유효한 native 게시 읽기로 통일했다.
추천 0건은 정상 게시이며 누락·만료·손상은 오류다. 게시 유효성만으로
systemd 타이머/프로세스 건강을 판정하지 않는다. 자동 모의 포지션 대조는 미사용이다.

검토/패킷/일별 큐/후보 성과 이력은 네 `kqs_native_candidate_*` 컬렉션으로 분리한다.
기존 bridge 컬렉션·개인 수동 원장은 그대로 보존한다. 향후 MongoDB 비공개 백업은
기존 자료와 네 native 컬렉션을 모두 포함해야 한다. 실제 백업/복원은 이번에 수행하지 않았다.

EINT-011A 사용자 승인 후 `scheduler.py`의 KQS 조회 경로만 보완했다.
기본 snapshot은 native 후보 큐, 명시적 bridge는 legacy 큐만 시세 수집 대상에 포함한다.
DCA 코드·주기·작업 등록과 기존 보유/전략/개인 판단 수집 대상은 변경하지 않았다.
로컬 격리 회귀로 대상 편입을 검증했으며 실제 시세 요청·운영 스케줄은 실행하지 않았다.
AWS에 코드를 올리거나 서비스를 재시작하지 않았다.

[화면·이력 계약과 한계](NATIVE_VIEWS.md), [EINT-011 당시 검증](verification/EINT-011.json),
[EINT-011A 승인 후 검증](verification/EINT-011A.json).

## 구성

```text
[EC2 t3.small, systemd timer]
08:30/10:30 KST  kqs-daily-pipeline  ─ KRX Open API(인증키) → 시장 DB(/srv/kqs/market)
                                      → coverage manifest → 품질 run(불변) → 단일 writer 게시
                                      → 게시 DB·시도 로그·실행 로그(/opt/investing-app/state/kqs)
11:30 KST        kqs-backup          ─ SQLite 온라인 백업 → 복원 검사 → (설정 시) 비공개 S3 업로드
매시              kqs-pipeline-status ─ 기대 세션이 게시·유효한지 → 실패 시 kqs-alert(journal/SNS)
홈페이지 backend ─ /app/state/kqs 를 읽기 전용 mount, 게시 DB만 읽음
```

| 경로(호스트) | 소유 | 내용 |
| --- | --- | --- |
| `/srv/kqs/market/krx_daily.sqlite3` | UID 10001, 0700 dir | 시장 DB(WAL). **디렉터리가 쓰기 가능해야** 읽기 전용 연결도 동작 |
| `/opt/investing-app/state/kqs/` | UID 10001 | 품질·게시·시도·실행 로그 DB, coverage, status JSON |
| `/srv/kqs/backups/` | UID 10001, 0700 | 최근 2개 로컬 백업(S3에 30일, 이전 버전 7일) |
| `/srv/kqs/secrets/krx.env` | root:10001 0640 | `KRX_AUTH_KEY=...`(D-31 형식). Git·이미지·로그에 넣지 않는다 |
| `/srv/kqs/secrets/ops.env` | root 0600 | `KQS_BACKUP_S3_URI`, `KQS_ALERT_SNS_TOPIC_ARN`(선택) |

## 실데이터 근거(읽기 전용 확인, 2026-09-29)

- 원본 DB: 2016-08-29~2026-09-28, 2,471세션, 1.4GB, WAL. 스키마는 이관 수집기와 동일.
- 세션마다 KOSPI 약 28~30, KOSDAQ 약 85개가 시가·고가·저가·거래량·거래대금 0, 종가·시총 양수
  (거래정지·무거래). EINT-005 규칙은 이를 모두 거부했으므로 EINT-007에서 규칙을 수정했다.
  새 규칙: 2026년 180세션 498,897행 오탐 0건. 2026-09-28 실제 세션 품질 run `ready`.
- `collection_log` 행 수=저장 행 수(2025~ 908건 전부). 세션 간 변동 최대 KOSPI ±2, KOSDAQ ±3.
  coverage 밴드는 직전 세션 대비 1%(최소 5)이며 초과 시 공급자 이상으로 차단한다.
- 원본 수집기는 평일 휴장일에도 호출해 0행을 기록했다. 이관 수집기는 검토된 캘린더 세션만
  호출하고, 세션인데 0행이면 실패(`collect_failed`)로 남기고 재시도 시각에 다시 시도한다.

## 웹 접속 경로(2026-10-01~)

- 주소 **https://efa-quant.com**. Cloudflare(프록시) → EC2 nginx 443(Cloudflare Origin 인증서 `/etc/ssl/cert.pem`)
  → `127.0.0.1:8801` → backend 컨테이너 8000. 80은 https로 이동만 한다.
- 보안그룹은 80·443을 Cloudflare IPv4 대역에만 연다. 8801은 서버 내부에서만 열린다(`docker-compose.prod.yml`).
- nginx 설정 원본은 `infra/nginx/efa-quant.conf`. Cloudflare가 넘긴 실제 접속자 IP(`CF-Connecting-IP`)를 Cloudflare 대역에서
  온 요청에만 인정한다. 대역은 https://www.cloudflare.com/ips/ 와 보안그룹을 함께 갱신한다.
- 배포·엔진 점검은 서버 안에서 `http://127.0.0.1:8801`로 한다.

## 배포 결과(2026-09-29)

- EC2 `i-0dc146c28dca4089b` t3.small, Elastic IP **3.39.188.147**(2026-10-01부터 고정, `eipalloc-01671e6a4dfe14c4e`; 이전 유동 IP 3.38.170.158), 2GB swap
  (`/swapfile`, swappiness 10). swap 없이 첫 실행이 OOM(exit 137)으로 종료됐다 — 제거하지 않는다.
- S3 `s3://kqs-state-125022369601/`: `initial/`(초기 DB 사본), `backups/<UTC 시각>/`. ops.env에 업로드 URI.
- 인증키: SSM `/kqs/krx-auth-key` → `/srv/kqs/secrets/krx.env`. 키 교체 시 파라미터 갱신 후 파일 재생성.
- KRX Open API는 키 하나에 서비스(API)별 이용 승인이 따로 있다. 2026-10-03부터 '유가증권 일별매매정보'
  (`sto/stk_bydd_trd`)만 `401 Unauthorized API Call`을 돌려 매일 수집이 `collect_failed`로 멈췄다(코스닥·ETF는 같은 키로 정상).
  이 경우 KRX Open API 사이트에서 해당 서비스 이용 신청·승인을 다시 받는다. 진단: 서버에서 세 엔드포인트를 같은 키로
  호출해 응답 코드를 비교한다(키 값은 출력하지 않는다).
- 타이머: `systemctl list-timers 'kqs-*'`. 로그: `journalctl -u kqs-daily-pipeline.service`.
- SNS 경보 미설정: `ops.env`에 `KQS_ALERT_SNS_TOPIC_ARN`을 넣고 역할에 `sns:Publish`를 추가하면 외부 알림.

## 배포 절차

1. **EC2 t3.micro → t3.small** 증설(중지·타입 변경·시작, 공인 IP 변경 가능). 메모리 2GB.
2. **비공개 S3 버킷**: 퍼블릭 액세스 차단, 버전 관리, SSE, 수명주기(예: 90일). 인스턴스 역할에
   해당 prefix의 `s3:PutObject`/`GetObject`/`ListBucket`만 추가. (선택) SNS 토픽과 `sns:Publish`.
3. **KRX 인증키**: SSM Parameter Store SecureString에 저장 → 인스턴스에서 `krx.env`(0640)로 기록.
   키 값은 명령 기록·로그·티켓에 남기지 않는다.
4. **초기 시장 DB 1회 이전**(`Ubuntu-26.04`에서):
   원본 파이프라인이 쓰지 않는 시각에 SQLite 온라인 백업으로 사본 생성 → `PRAGMA integrity_check`
   → SHA-256 기록 → 비공개 S3 업로드 → EC2에서 내려받아 SHA-256 대조·integrity_check →
   `/srv/kqs/market/krx_daily.sqlite3`(UID 10001). **원본 DB는 삭제하지 않는다.**
5. **배포 tarball에 `engine/`을 포함**한다(backend 이미지가 `engine/kqs`를 설치). `.venv`,
   `build/`, `state/`는 제외한다. backend 재빌드 후 `state/kqs`가 읽기 전용 mount인지 확인.
6. worker 이미지 빌드: `docker compose -f infra/docker/docker-compose.engine.yml --profile engine build`.
7. 수동 1회 실행 → 결과·게시 확인 → `kqs-pipeline-status` 확인 → `kqs-backup` 1회 → 업로드/복원 확인.
8. `infra/systemd/*.service|*.timer`를 `/etc/systemd/system/`에 설치, `infra/ops/*.sh`는 저장소 경로 그대로 사용,
   `systemctl enable --now kqs-daily-pipeline.timer kqs-backup.timer kqs-health.timer`.
9. **병행 관찰**: 원본 파이프라인과 며칠간 같은 세션의 품질·추천 결과를 비교한다. 원본 중단·삭제는
   별도 결정이며, 원시 자료 1,735개의 비공개 백업·복구 검증 전에는 원본을 폐기하지 않는다.

## 종료 코드와 상태

- `kqs-daily-pipeline`: 0 published/already_published, 1 collect_failed/coverage_rejected/
  quality_rejected/blocked_quality/publish_failed/failed, 3 writer_busy(성공 처리), 2 인자·키 오류.
- `kqs-pipeline-status`: 0 정상(기대 세션 게시·유효, 또는 공개 기대 후 6시간 유예 이내), 1 경보.
- 모든 실행은 `pipeline-runs.sqlite3`(불변)와 `pipeline-status.json`(최신)에 남는다.

## 한계

- coverage 기대 종목 수는 KRX 응답 행 수다. 저장 누락은 잡지만 공급자 자체의 소폭 누락은 못 잡는다.
- 품질 규칙은 행 형식·일관성 검사다. 가격 정정, 수정주가, 조용한 값 변화는 증명하지 않는다.
- flock 잠금과 SQLite는 단일 호스트 로컬 디스크 전제다(EFS/NFS·다중 인스턴스 불가).
- EINT-029부터 공개 기대 시각은 당일 22:00 KST(v3)이고, 게시는 다음 거래일 22:00까지 유효하다(금요일 밤 게시 →
  월요일 밤 새 게시까지 유지, 새 게시가 끝나기까지 몇 분은 빈다). EINT-028에서는 21:30·24시간 상한이었다.
- EINT-028부터 예약 실행은 22:00 → 06:00 → 08:30이다. KRX 반영이 늦으면
  22:00 실행은 `collect_failed`(빈 응답은 저장 안 함)이고 06:00·08:30에서 다시 받는다. 게시된 뒤의 시도는
  `already_published`로 KRX 호출 없이 끝난다. 상태 경보 유예는 12시간(21:30+12h=09:30 이후 경보), 그 안의
  수집 실패는 화면 경고를 띄우지 않는다. (EINT-027까지는 06:00 기대·08:30/10:30 실행이었다.)
- 2026·2027 캘린더가 있다(2027은 KRX 공식 일정 공표 전 검토본, [KRX_CALENDAR](KRX_CALENDAR.md) 참고). 12월에 공식 일정과 대조하고, 2028 캘린더는 2027년 안에 등록해야 한다. 캘린더 변경은 엔진 worker 이미지도 다시 빌드해야 한다(백엔드 자동 배포만으로는 부족).
- 경보는 SNS 설정 시에만 외부로 나간다. 미설정이면 journal에만 남는다.
