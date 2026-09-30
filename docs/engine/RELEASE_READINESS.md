# EINT-010 — 로컬 릴리스 준비와 미커밋 정리

2026-09-29 기준. **로컬 안전 보완은 검증했지만 자동배포 설정 변경은 승인 대기다.**
AWS 배포·운영 정상 판정·커밋·push를 수행하지 않았다.

## 완료한 범위

1. `scripts/test_database_safety.py`는 표준 라이브러리만 사용하며 DB에 접속하지 않는다.
   `tests/conftest.py`가 앱을 import하기 전과 각 삭제 직전에 검증한다.
   허용 DB는 `jy_investing_test` 또는 `jy_investing_test_<소문자 식별자>`다.
   인증정보 없는 단일 로컬 URI만 허용하며 `directConnection=true`로 replica 재탐색을 막는다.
   외부 주소·운영 DB·환경 변경·다른 DB 핸들은 예외로 중단한다.
2. KQS CI에 EINT-008·009를 포함한 백엔드 테스트 61개와 독립 MongoDB 서비스를 추가했다.
   Python 3.12/3.14 matrix이며 로컬 실행 검증은 Python 3.14만 수행했다.
   최소 의존성 목록은 `scripts/tests/requirements.kqs-backend.txt`다.
   기존 홈페이지 import에 필요한 시장 캘린더·NumPy/Pandas/SciPy는 포함하지만
   Torch·브로커·데스크톱 전체 환경은 설치하지 않는다.
3. 홈페이지 상태 mount는 worker와 같은 절대 호스트 경로, 읽기 전용, 자동 디렉터리 생성 금지다.
4. 배포 사전 검사는 기존 `.env` 존재, 추천 DB 존재, 읽기 전용 mount,
   Compose 프로젝트 및 Mongo/Qdrant/Meilisearch의 **현재** named volume을 확인한다.
   Docker inspect만 사용한다. 이름을 추정해 컨테이너를 시작하거나 볼륨을 만들지 않는다.
   이 검사 자체도 AWS에서는 실행하지 않았다.

## 보류된 자동배포 변경

안전 검토가 `.github/workflows/deploy.yml` 변경을 차단했다.
이 파일은 main push 후 SSH 재시작·재색인으로 이어져 운영 부작용에 대한 별도 승인이 필요하다.
승인 전에는 원본 파일을 유지한다. 현재 남은 문제는 다음과 같다.

- 경로 `/opt/investment-analysis`와 실제 운영 기록 `/opt/investing-app`의 차이.
- KQS 회귀 테스트가 이미지 빌드/배포의 필수 조건이 아님.
- 기존 Compose 프로젝트와 데이터 볼륨의 식별자 보존이 검증되지 않음.
- GitHub secret으로 만든 파일이 기존 backend `.env`를 덮어씀.

승인 후에는 **파일 변경만** 하고 기존 운영 식별자를 조회해 확인한다.
파일 승인과 AWS 재배포 승인은 별개다. 현재 단계에서는 어느 서비스도 재시작하지 않는다.

## 읽기 전용 확인 명령

저장소 루트에서(PyYAML이 설치된 Python 사용):

```bash
python scripts/check_kqs_release.py
python scripts/check_kqs_release.py --require-clean --require-deployment-ready
python -m unittest discover -s scripts/tests -p 'test_database_safety.py' -v
python -m unittest discover -s scripts/tests -p 'test_kqs_release_safety.py' -v
python -m unittest discover -s scripts/tests -p 'test_kqs_deployment_preflight.py' -v
```

첫 검사는 로컬 안전 구성과 문제 목록을 출력한다.
두 번째 검사는 **현재 실패해야 정상**이다: 필수 코드가 아직 미추적이고 작업 트리가 더티하며
자동배포 설정이 승인 대기다. 기본 검사의 통과를 배포 준비 완료로 해석하지 않는다.

백엔드 pytest는 전용 테스트 MongoDB가 있을 때만 실행한다.
운영 DB명을 환경변수로 지정해 우회하지 않는다. 테스트 초기화/종료 시 허용된 테스트 DB만 비운다.
이번 검증은 별도 MongoDB 컨테이너의 임시 메모리 저장소·임의 로컬 포트에서 수행했다.
실제 회원정보·수동 판단 원장·실험/manifest를 건드리지 않았다.

## 미커밋 기준선

`check_kqs_release.py`는 Git 상태를 파일 단위로 펼쳐 다음 그룹으로 **조회·분류**한다.
stage/commit/push는 하지 않는다.

- 엔진 및 불변 근거자료: `engine/`, `docs/engine/`, `AGENTS.md`.
- 홈페이지 KQS: 추천 계약/API·수동 원장/데이터셋 화면 및 문서.
- 테스트·CI·릴리스: 격리 테스트, workflow, Docker/infra 및 검사 도구.
- 기타 기존 변경: 레버리지 DCA 등 이번 단계 밖의 변경. 분리하고 그대로 보존한다.

필수 신규 파일의 미추적 여부와 추적 중인 `.env`·DB·키도 검사한다.
사용자 승인으로 별도 커밋을 준비할 때 파일별 diff를 확인하고,
엔진/자료 → 홈페이지 KQS → 테스트·CI 순으로 의미 단위를 나눌 수 있다.
불변 자료는 수정하지 말고 최초 추가로 보관한다. 원시 데이터·DB·인증키는 포함하지 않는다.
현재 AWS 배포물과 Git HEAD가 같다고 단정할 수 없으므로 다음 배포 전 출처를 고정해야 한다.

## 재사용 근거와 검증 한계

- EINT-007A 엔진 0.6.0/revision, EINT-008 FIFO 회귀, EINT-009 고정 manifest/holdout 규약을 재사용했다.
- D-28/D-44에 따라 기존 엔진 자동 원장과 사용자 수동 원장은 합치지 않는다.
- D-20/D-26/D-36의 과거 연구·소진된 실제 holdout은 재평가하지 않았다.
  러너 가드 테스트는 가짜 API와 합성 표본만 사용한다.
- [검증 기록](verification/EINT-010.json)에 실행 환경·새 파일 해시·통과 수·승인 보류를 남긴다.
- 현재 회귀 통과는 전략 성과·AWS 정상 운영·전체 백업을 증명하지 않는다.
  기존 1,735개 원시 자료는 목록/해시만 있고, 개인 MongoDB 원장 백업은 별도 후속 단계다.
- 다음 EINT-011은 PC 브리지 의존 상태/후보 화면을 AWS native 추천 흐름과 일치시키는 단계다.
  이번에는 해당 API/화면을 수정하지 않았다.
