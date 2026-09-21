# 통합 퀀트 플랫폼 아키텍처

이 저장소는 `IV-TEST`의 교육·포트폴리오·전략 기능을 사용자 애플리케이션으로 유지하고,
`edumgt/domain-rag-lab`에서 검증한 도메인 RAG 설계를 검색 계층에 통합한 퀀트 시스템
기본 베이스다. 두 FastAPI 애플리케이션을 그대로 중복 실행하지 않고, 현재 API 계약과
화면을 유지하면서 필요한 엔진만 이식했다.

## 실행 구조

```text
브라우저 SPA
    │
    ▼
FastAPI (:8000)
    ├─ 퀀트 분석 / 실제 가격 백테스트 / LEAN 결과
    ├─ 포트폴리오 / 전략 / 모의 체결(실계좌와 분리)
    ├─ MongoDB ─ 사용자·전략·포트폴리오
    └─ 금융 RAG
         ├─ Qdrant ─ 의미 기반 청크 검색
         ├─ Meilisearch ─ 정확한 용어·종목·문서 검색
         └─ RRF ─ 두 순위를 결합하고 출처와 함께 응답
```

## 통합 원칙

- Qdrant와 Meilisearch 중 하나가 일시적으로 실패해도 벡터 검색으로 안전하게 폴백한다.
- RAG 생성 모델에는 검색된 원문만 전달하며 매수·매도 권유를 생성하지 않는다.
- 전략 주문은 제안과 모의 체결까지만 지원하고 실제 포트폴리오 장부를 변경하지 않는다.
- 기본 Compose는 CPU·메모리를 많이 쓰는 Ollama를 시작하지 않는다. 로컬 생성 AI가
  필요할 때만 `ai` 프로필을 사용한다.
- Compose의 API 이미지는 외부 MongoDB를 사용하므로 MongoDB 서버를 중복 내장하지 않는
  `Dockerfile.service`로 빌드한다. 단일 PC 배포용 all-in-one 이미지는 기존 구성을 유지한다.
- PostgreSQL/Redis는 향후 시계열 팩터 저장소나 작업 큐가 실제로 필요해질 때 도입한다.
  현재 기능과 중복되는 인프라를 먼저 추가하지 않는다.

## 개발 실행

```bash
# 앱, MongoDB, Qdrant, Meilisearch와 초기 검색 색인
docker compose --profile init up --build -d

# 선택: 로컬 Ollama와 모델 설치
docker compose --profile ai up -d ollama
docker compose --profile ollama-init run --rm ollama-init

# 문서 변경 뒤 벡터·키워드 색인 갱신 (`init` 최초 실행에는 둘 다 포함됨)
docker compose --profile tools run --rm docs-index
docker compose --profile init run --rm search-index
```

RAG API의 기본 `search_mode`는 `hybrid`다. Meilisearch가 준비되지 않았으면 응답의
`search_mode`가 `vector`로 내려가므로 운영 모니터링에서도 폴백 여부를 확인할 수 있다.

## 다음 확장 순서

1. 시장 데이터 수집기를 별도 작업 프로세스로 분리하고 원본/정제 데이터의 기준일을 기록한다.
2. 팩터 계산과 전략 신호를 버전 관리하고 look-ahead bias 검증을 자동화한다.
3. 워크포워드·거래비용·슬리피지 검증을 통과한 전략만 모의투자 후보로 승격한다.
4. 브로커 연동은 별도 실행 서비스와 승인 정책을 두며 웹 API가 주문 키를 직접 보유하지 않게 한다.

이 프로젝트의 결과는 교육·분석 보조용이며 실제 투자 성과를 보장하지 않는다.
