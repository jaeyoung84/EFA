# 퀀트 플랫폼 1단계 기준선

기준일: 2026-09-10 (Asia/Seoul)
Git 기준 커밋: `18f15d4` (`main`, 작업트리 변경 사항 포함)

## 합격 기준

- Docker Compose 구성이 유효하고 backend, MongoDB, Qdrant, Meilisearch가 healthy 상태다.
- 전체 pytest 테스트가 실패 없이 통과한다.
- 회원가입, 로그인, 인증된 포트폴리오 조회가 성공한다.
- KOSPI 일봉과 KOSPI/NASDAQ/USD-KRW 스냅샷이 실제 데이터로 조회된다.
- Qdrant 컬렉션과 Meilisearch 색인에 연결된다.
- 패치 공백 오류가 없다.

## 확정 결과

| 항목 | 결과 |
|---|---|
| 전체 pytest | 28 passed, 12 warnings, 8.03초 |
| backend | healthy, `/api/health` 200 |
| MongoDB | ping 및 임시 쓰기/삭제 성공 |
| KOSPI 일봉 | 87개 봉, `is_simulated=false` (거래일에 따라 봉 수 변동) |
| 시장 스냅샷 | KOSPI, NASDAQ, USD/KRW 모두 `status=ok` |
| 인증 | 임시 회원가입·로그인 성공 후 계정/세션 삭제 |
| 포트폴리오 | 사용자 인증 및 격리 테스트 통과 |
| RAG | Qdrant green/501 points, Meilisearch available |
| 패치 검사 | `git diff --check` 통과 |

현재 경고 12건은 FastAPI `on_event` 폐기 예정 API와 Pydantic v2의 `dict()` 폐기 예정
API다. 기능 실패는 아니지만 2단계 작업 전후에 제거할 기술 부채로 관리한다.

## 재현 명령

```bash
APP_PORT=18000 docker compose config --quiet
APP_PORT=18000 docker compose up -d
docker compose ps
docker exec investment_backend python /app/scripts/baseline_smoke.py
```

전체 테스트는 운영 DB와 분리된 DB 이름으로 실행한다.

```bash
docker run --rm --network host \
  -e MONGODB_URL=mongodb://127.0.0.1:27017 \
  -e MONGODB_DB=jy_investing_baseline_test \
  -e QDRANT_URL=http://127.0.0.1:6333 \
  -e MEILI_URL=http://127.0.0.1:7700 \
  -e PYTHONPATH=/deps:/workspace \
  -v "$PWD:/workspace:ro" \
  -v /tmp/ivtest-pytest-site:/deps:ro \
  -w /workspace --entrypoint python \
  jaeyoungsong/jy-investing:latest \
  -m pytest -q -p no:cacheprovider
```

`baseline_smoke.py`는 임시 사용자를 만들고 회원가입·로그인·포트폴리오 조회를 확인한
뒤 해당 사용자, 세션과 포트폴리오를 항상 삭제한다.
