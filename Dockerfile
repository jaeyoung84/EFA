# 파이썬 3.12 + Debian trixie로 고정(보안 패치는 계속 받는다). 운영 이미지와 같은 조합이다.
FROM python:3.12-slim-trixie

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash \
      curl \
      fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock.txt ./
# requirements.txt의 패키지를 requirements.lock.txt의 정확한 버전으로 설치한다(배포마다 같은 버전, 2026-10-02 httpx 누락 같은 사고 예방).
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt -c requirements.lock.txt \
    && pip check

COPY engine/kqs/ ./engine/kqs/
RUN pip install --no-deps ./engine/kqs
COPY app/ ./app/
COPY docs/ ./docs/
# 저장된 LEAN 전략 카드가 읽는 사전 실행 결과(routers/strategy.py).
COPY hyundai-results/ ./hyundai-results/
COPY lean-results/ ./lean-results/

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=10 \
    CMD curl --fail http://localhost:8000/api/health || exit 1

# nginx가 넘긴 실제 접속자 IP·https 여부(X-Forwarded-*)를 Docker 내부망에서 온 요청에만 신뢰한다.
CMD ["uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1,172.16.0.0/12"]
