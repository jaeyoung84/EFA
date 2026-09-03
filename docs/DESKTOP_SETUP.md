# JY investing 데스크톱 실행

## 더블클릭으로 실행 (Windows)

저장소 루트의 `run-jy-investing.bat`을 더블클릭한다. Docker Desktop이 꺼져 있으면 자동으로 켜고, 컨테이너를 빌드·실행한 뒤 서버가 준비되면 브라우저를 자동으로 연다. 끌 때는 `stop-jy-investing.bat`을 더블클릭한다(저장된 계정·포트폴리오는 유지된다).

## 빠른 실행 (터미널)

Docker Desktop을 실행한 뒤 저장소 루트에서 아래 명령을 실행한다.

```bash
docker compose -f docker-compose.desktop.yml up --build -d
```

브라우저에서 <http://localhost:8000>을 연다. 처음에는 회원가입을 하고, `포트폴리오 → 내 보유자산`에서 현금과 보유 종목을 저장한다.

## 상태 확인

```bash
docker compose -f docker-compose.desktop.yml ps
docker compose -f docker-compose.desktop.yml logs -f backend
```

정상 동작 시 <http://localhost:8000/api/health>는 `{"status":"ok"}`를 반환한다.

## 데이터 보존과 종료

```bash
docker compose -f docker-compose.desktop.yml down
```

이 명령은 MongoDB 볼륨을 유지한다. 회원 계정과 포트폴리오도 유지된다.

완전히 초기화하려면 다음 명령을 사용한다. 이 작업은 저장한 계정과 포트폴리오를 삭제한다.

```bash
docker compose -f docker-compose.desktop.yml down -v
```

## 전체 리서치 스택

문서 AI, Qdrant, Meilisearch, 로컬 Ollama 모델까지 쓰려면 경량 Compose 대신 기존 구성을 사용한다.

```bash
docker compose up --build -d
docker compose --profile ollama-init run --rm ollama-init
```

`docker-compose.desktop.yml`은 개인 포트폴리오·시장 시세·백테스트 화면을 우선 실행하는 경량 구성이다. 시세는 Yahoo Finance 지연 시세이며, 주문을 증권사로 전송하지 않는다.

경량 구성은 딥러닝 이미지 생성과 CNN/LSTM/Transformer 실습용의 대용량 패키지를 설치하지 않는다. 이 기능까지 모두 필요할 때만 전체 리서치 스택을 사용한다.
