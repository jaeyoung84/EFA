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

## 다른 사람과 공유하기 (Docker Hub 이미지)

소스 코드를 보내지 않고도 실행할 수 있다. `jaeyoungsong/jy-investing:latest`로 이미지를 이미 올려 두었으므로, 상대방은 아래 4개 파일만 있으면 된다.

```text
docker-compose.share.yml
run-jy-investing-shared.bat
stop-jy-investing-shared.bat
.env.desktop.example   (DART 검색을 쓰려면 .env.desktop으로 복사해 키를 채운다)
```

받는 사람이 할 일:

1. [Docker Desktop](https://www.docker.com/products/docker-desktop/)을 설치한다(Windows/Mac 공통, 회원가입 불필요).
2. 위 4개 파일을 아무 폴더에나 함께 둔다. 저장소를 통째로 받을 필요는 없다.
3. `run-jy-investing-shared.bat`을 더블클릭한다. Docker Desktop이 꺼져 있으면 자동으로 켜고, `jaeyoungsong/jy-investing:latest` 이미지를 내려받아(최초 1회, 이미지 용량 때문에 몇 분 걸릴 수 있다) 실행한 뒤 브라우저를 연다.
4. `http://localhost:8000`에서 새로 회원가입한다.

**데이터는 완전히 분리된다.** 같은 서버에 접속하는 것이 아니라, 각자 자기 컴퓨터에서 같은 이미지를 독립적으로 실행하는 구조다. 내 계정·보유자산·전략이 상대방에게 보이지 않고, 반대도 마찬가지다. MongoDB 데이터는 `docker-compose.share.yml`이 만드는 로컬 볼륨에만 저장된다.

끌 때는 `stop-jy-investing-shared.bat`을 더블클릭한다(데이터는 유지된다).

**업데이트를 다시 받으려면**(개발자가 이미지를 새로 올린 뒤): `docker compose -f docker-compose.share.yml pull && docker compose -f docker-compose.share.yml up -d`

**직접 이미지를 최신으로 올리려면**(개발자용):
```bash
docker compose -f docker-compose.desktop.yml build backend
docker tag jy-investing-backend:desktop jaeyoungsong/jy-investing:latest
docker push jaeyoungsong/jy-investing:latest
```

## 전체 리서치 스택

문서 AI, Qdrant, Meilisearch, 로컬 Ollama 모델까지 쓰려면 경량 Compose 대신 기존 구성을 사용한다.

```bash
docker compose up --build -d
docker compose --profile ollama-init run --rm ollama-init
```

`docker-compose.desktop.yml`은 개인 포트폴리오·시장 시세·백테스트 화면을 우선 실행하는 경량 구성이다. 시세는 Yahoo Finance 지연 시세이며, 주문을 증권사로 전송하지 않는다.

경량 구성은 딥러닝 이미지 생성과 CNN/LSTM/Transformer 실습용의 대용량 패키지를 설치하지 않는다. 이 기능까지 모두 필요할 때만 전체 리서치 스택을 사용한다.
