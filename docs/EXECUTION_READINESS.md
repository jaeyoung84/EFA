# 실거래 연동 준비 및 운영 점검표

현재 시스템은 **모의 주문 전용**이다. `broker_adapter.py`는 증권사별 구현을 넣기 위한
경계만 정의하고, 기본 어댑터는 모든 실거래 주문을 거부한다. API 키를 환경변수에 넣어도
현재 배포본에서는 실거래가 전송되지 않는다.

## 12단계: 샌드박스와 감사 원장

`BROKER_ADAPTER=sandbox` 및 `BROKER_SANDBOX_ENABLED=true`를 함께 설정하면 로컬
샌드박스 어댑터를 점검할 수 있다. 이 어댑터는 외부 네트워크·증권사 계좌·API 키를
사용하지 않으며, 생성하는 `sandbox-*` 주문 ID는 테스트용이다. `EXECUTION_MODE=sandbox`
에서만 상태 화면에 준비됨으로 표시되고, 실거래 가능 상태로 승격될 수 없다.

모의 주문 권한 변경, 주문 제안 승인·거절은 `execution_audit_events`에 사용자별 SHA-256
해시 체인으로 추가된다. `GET /api/strategy/execution-audit`는 본인 기록과 검증 결과를
읽기 전용으로 제공한다. 이 원장은 애플리케이션 수준의 변조 감지 장치이며, 운영 실거래
전에는 별도 권한의 WORM/외부 로그 저장소로 복제해야 한다.

현재 해시 체인 쓰기는 단일 백엔드 프로세스 기준으로 직렬화된다. 다중 워커 또는 실제
운영 환경에서는 MongoDB 트랜잭션이 가능한 복제본 구성과 외부 append-only 보관소를
도입한 뒤에만 실거래 검토 대상으로 삼는다.

## 13단계: 운영 통제

`BROKER_SECRET_SOURCE=external_reference`는 Secret Manager의 참조명이 준비됐는지만
확인하는 상태 값이다. `BROKER_*_REF`의 원문은 API에 반환하지 않으며, 이 프로젝트가
Vault·AWS·GCP 등의 Secret Manager에 직접 접속하지도 않는다. 실제 제공자를 선택한 뒤
배포 플랫폼의 런타임 주입으로 `BROKER_API_KEY` 등을 공급해야 한다.

`EXECUTION_OPERATOR_EMAILS`에는 운영자 이메일을 쉼표로 설정할 수 있다. 등록 운영자는
시크릿 교체·WORM 보관·브로커 샌드박스 검토 요청을 만들고, 작성자를 제외한 두 명의
별도 운영자가 승인할 수 있다. 승인 결과는 `approved_for_review`일 뿐이고, 실거래 권한이나
주문 전송 기능은 절대 만들지 않는다.

`GET /api/strategy/execution-audit/export`는 SHA-256 헤더가 포함된 NDJSON을 제공한다.
운영자는 이 파일과 해시를 조직의 WORM/append-only 보관소에 업로드해 보관해야 한다.

## 안전 기본값

| 환경변수 | 기본값 | 의미 |
| --- | --- | --- |
| `EXECUTION_MODE` | `paper` | 실행 모드 |
| `EXECUTION_GLOBAL_KILL_SWITCH` | `false` | 모든 주문 제출 중지 |
| `LIVE_TRADING_ENABLED` | `false` | 실거래 기능 의도적 활성화 여부 |
| `LIVE_TRADING_KILL_SWITCH` | `true` | 실거래 제출 차단 |

`BROKER_API_KEY`, `BROKER_API_SECRET`, `BROKER_ACCOUNT_ID`는 서버 환경에서만 읽으며,
상태 API·리포트·로그에 원문을 반환하지 않는다. `.env`는 저장소에 커밋하지 않는다.

## 실거래 어댑터 도입 전 필수 점검

1. 증권사별 `BrokerAdapter` 구현에 주문 idempotency·주문 조회·취소·부분체결 처리를 넣는다.
2. 키는 Secret Manager 또는 런타임 주입을 사용하고, 개발용 `.env`와 분리한다.
3. 전역 킬스위치와 사용자별 권한이 모두 작동하는지 샌드박스에서 검증한다.
4. 주문 한도·장 운영시간·재시도·중복주문 방지·장애 복구 절차를 독립 테스트한다.
5. 외부 감사 로그와 운영자 승인 절차를 마련한 뒤 별도 배포 환경에서만 live를 검토한다.

사용자는 `PUT /api/strategy/execution-permission`으로 자신의 **모의 주문** 제출만 끌 수 있다.
이 API는 실거래 권한을 만들지 않는다. `GET /api/strategy/execution-status`는 비밀값 없이
현재 권한·킬스위치·어댑터 준비 상태를 반환한다.
