# 엔진 통합 근거자료 — 먼저 읽을 문서

2026-09-29 재검토 기준. **과거 연구 재실행은 0회**다.
근거 보관(EINT-001), 실행 패키지(EINT-002), 추천 게시/API 연결(EINT-003), KRX 세션 정책(EINT-004),
시장 입력 완전성 검증·게시 게이트(EINT-005), 단일 writer 게시 worker(EINT-006), PC 독립 운영 준비(EINT-007)와 그 배포·보완(EINT-007A)을 구분한다.

## 현재 진행 — EINT-019(3개 아이템 추천의 전향 성과 관측)

- 매일 파이프라인이 v2 게시 추천마다 다음 거래일 시가 기준 1·5·10·20거래일 수익률·시장 ETF 대비·최대 낙폭을
  `state/recommendation-outcomes.json`에 쓴다. 웹 "매매 기록·연구 → 성과" 탭에 표시. 관측일 뿐, 청산 규칙 모의·비용·체결 없음.
- 아이템·기간별 20건 미만은 "표본 부족(판단 보류)". 이 숫자로 전략을 채택·기각하려면 별도 사전등록이 필요하다.

## 직전 — EINT-017(3개 아이템 전략 추천 + Trend Pullback v4 이식)

- 사용자 운영 방침: Bollinger-RSI·RSI-OBV·Trend Pullback v4를 나란히 게시하고 아이템별 최대 5개, 각 아이템의 기존 순서,
  아이템 간 중복 허용, 매도 신호는 사용자 보유 종목만. 검증된 성과 개선이 아니며 v4는 D-43 "불통과 힌트" 상태 그대로다.
- v4는 보관 원본에서 이식했고 실제 DB로 관측 #5(521거래·trades/equity 해시)를 완전히 재현했다(구현 동치 확인, 소진 창).
- 게시 계약 `kqs-recommendations/v2`(아이템별 순위·청산 모니터). 웹은 v1·v2를 모두 읽는다. 상세: CHANGES.md, verification/EINT-017.json.

## 직전 — EINT-014·015(학습 준비도·개인 데이터 보호 로컬 검증 완료)

- EINT-014는 v3 후보의 판단 레이블 분포, 날짜 수·범위·집중도, 구간별 다양성, 20일 레이블과 추천시점 출처를 읽기 전용으로 검사한다. 기준 미달이면 새 동결을 차단한다.
- EINT-015는 기본 미동의, 명시적 동의/철회 이력, 동의 없는 v3 동결·내보내기·holdout·실험·오프라인 러너 차단을 추가했다. 동의는 오프라인 학습 데이터 사용만 뜻하며 추천·주문·자동체결·실거래 권한은 만들지 않는다.
- 철회는 이후 사용을 즉시 막지만 기존 manifest/holdout/실험 이력은 감사 자료로 보존한다. 보존기한·물리 삭제는 자동화하지 않았다.
- KQS 관련 API 회귀 **98개**, scripts **71개**, Node 렌더링 3개와 문법 검사가 통과했다. 실제 사용자 원장, 모델 학습, holdout 개봉, 실사용 Mongo 백업/복구, AWS 변경은 아직 수행하지 않았다.
- [학습 피처 출처](LEARNING_FEATURE_PROVENANCE.md), [학습 데이터 보호·복구 범위](LEARNING_DATA_PROTECTION.md). 이후 배포는 릴리스 구성·운영 사전검사와 분리해 검증한다.

## 직전 — EINT-012(수동 판단·거래 원장 로컬 검증 완료)

- 거래 시각/거래일과 서버 기록 시각을 구분하고 사용자 입력·기본값·과거 미상 출처를 표시한다.
  UTC 저장/KST 표시이며 기존 개인 기록을 다시 쓰거나 실제 시장 체결로 간주하지 않는다.
- 동일 사용자·요청 ID·입력 재시도는 최초 결과를 재생한다. 다른 입력의 같은 ID는 409다.
  사용자별 DB 잠금 안에서 수량/시간을 검증해 동시 초과 매도를 사전 차단한다.
- 기존 FIFO·추천/청산 분리·manifest v2는 유지한다. 과거 역순·불일치·조회 한도는 새 거래 전에 중단한다.
  화면 ID는 폼 메모리에만 남으며 장애 잠금은 운영자 확인 후 복구해야 한다. 감사 쓰기는 best-effort다.
- 웹/API **89 + scripts 66 = 155개 통과**, Node renderer 세 검사·문법·엔진 무결성 검사 통과.
  엔진 0.6.0 / EINT-007A, DCA·스케줄·기존 deploy workflow는 그대로다.
- [수동 원장 계약·복구·한계](MANUAL_RECORDS.md), [EINT-012 검증](verification/EINT-012.json).
  AWS 배포/운영 DB 변경/실제 주문·자동체결/과거 연구 재실행은 없다.
  당시 다음 계획 EINT-013은 후속 단계에서 별도 진행했다.

## 직전 — EINT-011·011A(로컬 화면/API·후보 시세 조회 연결 검증 완료)

- 상태·후보·검토 근거를 기본 native 게시 SQLite 기준으로 통일했다. PC 브리지 자동 대체는 없다.
- 실제 추천 이유·버전·기준일·만료·게시 ID를 표시한다. 유효 추천 0건과 오류를 구분하고,
  게시 유효성을 타이머/프로세스 건강으로 오인하지 않는다.
- 예전 bridge 검토/큐/성과 자료는 보존하고 native 이력은 별도 네 컬렉션을 사용한다.
  현재 개인 메모는 게시 ID까지 일치해야 표시한다. 자동 모의 포지션 대조는 미사용으로 표시한다.
- 웹/API 65 + scripts 66 + 엔진 합성 101 = **232개 통과**, Node 렌더링 두 검사도 통과했다.
  과거 연구·실제 holdout·AWS 실행·사용자 실제 원장 변경은 없다.
- EINT-011A 사용자 승인 후 KQS 후보 시세 조회를 native/bridge source와 일치시켰다.
  import 두 줄과 조회 한 줄만 바꾸고 DCA 코드·파일·주기·작업 등록은 그대로 보존했다.
  관련 웹/API 69 + scripts 66 = **135개 통과**. 실제 시세 수집/운영 스케줄은 실행하지 않았다.
- [화면·이력 계약과 한계](NATIVE_VIEWS.md), [EINT-011 당시 검증](verification/EINT-011.json),
  [EINT-011A 승인 후 검증](verification/EINT-011A.json).
  당시 엔진 0.6.0 / EINT-007A와 다음 계획 EINT-012를 기록한 결과다.

## 직전 — EINT-010(로컬 안전 보완 완료, 자동배포 설정 승인 대기)

- 테스트 DB를 앱 import 전과 삭제 직전에 검증한다. 운영 DB명·외부 URI·접속 대상 변경은 삭제 전에 차단한다.
- KQS 수동 원장·불변 데이터셋 테스트를 Python 3.12/3.14의 전용 MongoDB CI에 연결했다.
- 홈페이지의 KQS 상태 mount는 worker와 같은 절대 경로이며 읽기 전용이다. 잘못된 경로를 자동 생성하지 않는다.
- 기존 환경파일·Compose 프로젝트·데이터 볼륨을 확인하는 **읽기 전용** 배포 사전 검사를 추가했다.
  임시 파일·가짜 Docker 회귀만 수행했으며 AWS에서는 실행하지 않았다.
- 기존 `.github/workflows/deploy.yml` 변경은 안전 검토에서 차단되어 **승인 전까지 원본을 유지**한다.
  향후 main push 시 재시작을 유발할 수 있어 별도 승인 후 파일을 수정하고 운영 식별자를 확인해야 한다.
- [릴리스 준비와 한계](RELEASE_READINESS.md), [이번 검증](verification/EINT-010.json).
  실제 AWS 변경·배포·커밋·push·과거 연구 재실행은 없고 엔진 0.6.0 / EINT-007A는 그대로다.

## 직전 — EINT-008·009(완료·배포 기록은 해당 검증 자료 참조)

- EINT-008: 만료된 추천 보유분의 수동 매도·부분매도와 FIFO lot 기록([검증](verification/EINT-008.json)).
- EINT-009: 수동 판단 데이터셋 불변 manifest, 사전등록 후 1회 holdout 개봉, manifest 연결 실험 기록
  ([규약](../KQS_HUMAN_DECISION_DATASET.md#eint-009-불변-데이터셋-manifest와-학습holdout-규약), [검증](verification/EINT-009.json)).
- 엔진 코드는 바뀌지 않았으므로 엔진 revision은 [EINT-007A](../../engine/kqs/revisions/EINT-007A.json) 그대로다.
  [변경 이력](CHANGES.md). 주문·자동 모의체결·실거래는 없다.

## 직전 — EINT-007A(AWS 배포 완료, 병행 관찰 중)

- [독립 실행 패키지](../../engine/kqs/README.md) **0.6.0**이 EC2(t3.small)에서 systemd 타이머로 동작한다:
  08:30/10:30 KST `kqs-daily-pipeline`, 11:30 KST `kqs-backup`(비공개 S3), 매시 `kqs-pipeline-status`.
  홈페이지는 https://efa-quant.com (2026-10-01부터, 이전 http://3.38.170.158:8801). [운영 절차](OPERATIONS.md), [검증 근거](verification/EINT-007A.json).

## 어디에 무엇이 있는가

| 자료 | 역할 |
| --- | --- |
| [REVIEW.md](REVIEW.md) | 최신 결정 해석, 발견한 위험, 통합 순서 |
| [evidence_registry.json](evidence_registry.json) | D-1~D-44 및 K-5, 총 45개 결정의 원문 위치/해시/최신 해석 |
| [source_manifest.json](source_manifest.json) | 1,902개 자료의 출처·유무·해시, 당시 환경/운영 관측 |
| [snapshots/](snapshots/) | 소스·테스트·사전등록·외부 러너·문서 등 167개 실행되지 않는 원문 사본 |
| [CHANGES.md](CHANGES.md) | 이후 변경의 재사용 근거/검증/한계를 추가하는 이력 |
| ../../AGENTS.md | 개발 전후 확인해야 할 지침 |

보관 기준선: `Ubuntu-26.04:/home/gram/korea-quant-system`, 브랜치
`claude/reversion-exit-redesign`, commit `0c6b2e8308cc783ea5c4a50c18ff213203631ba2`.
원본 dirty 상태, staged/unstaged 패치도 별도로 보존했다. 현재 홈페이지 검토 코드도
같은 보관본의 `homepage/`에 남겼다. 원본 엔진 Git 상태는 수집 전후 동일했다.

## 확인 명령

저장소 루트 `/home/gram/IV-TEST`에서:

```bash
python3 scripts/check_engine_evidence.py
python3 scripts/check_engine_evidence.py --topic "랭킹"
python3 scripts/check_engine_evidence.py --topic "index-tsmom"
python3 -m unittest discover -s scripts/tests -p 'test_engine_evidence.py' -v
```

검사 도구는 보관 파일/원본 바이트 해시, 결정 색인/참조/최신 규칙 자료 연결을 확인한다.
6개 혼합 CRLF 파일은 원래 줄바꿈 위치를 기록해 원본 바이트를 복원 검증한다.
엔진을 import하거나 실행하지 않으며 DB/네트워크에도 접근하지 않는다.
새 도구의 격리 테스트15개와 보관본167개/결정45개 무결성 검사가 통과했다.
예전 테스트 수치는 **당시 문서의 보고값**이고, 이 검사 성공은 현재 전략 성과 보증이 아니다.
CI의 `engine-evidence.yml`도 같은 검사만 수행한다.

```bash
python3 scripts/check_engine_evidence.py --require-full-backup
```

위 명령은 현재 실패하는 것이 정상이다. DB·원시 데이터·기존 보고서 등 1,735개 자료는
**해시/유무 목록만 있으며 실제 비공개 백업은 아직 하지 않았다.** 누락과 미복사를
구분하려는 검사이며, 목록이 있다는 이유로 전체 이전/복구 가능이라고 표시하지 않는다.
원본 9개 CSV/xls는 현재 존재하고 사전등록 해시 9/9가 일치하지만 Git에 복사하지 않았다.

## 중복 연구를 막는 방법

1. 관련 결정을 검색하고 최신 개정 및 실제 산출물 근거를 확인한다.
2. 기각/소진 결과는 재사용한다. 바꿀 기능과 새 테스트가 필요한 이유를 먼저 기록한다.
3. 변경된 기능은 합성/격리 데이터로 회귀 검증한다. 기존 연구 러너를 실행하지 않는다.
4. 변경 후 새 버전·데이터 지문·검증 결과·한계를 `CHANGES.md`에 추가한다.
5. 새 연구라면 별도 사전등록과 독립/새 전향 표본부터 확보한다.

이 기록과 CI는 실수 방지를 돕지만 모든 연구 실행을 기술적으로 차단하지는 않는다.
EINT-009에서 고정 manifest·1회 holdout 개봉 규약을 추가했다.
추천 시점 특징의 지문 범위와 사후 판단 정보의 분리는 추가 보완 항목이다.
