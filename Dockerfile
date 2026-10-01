FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash \
      curl \
      fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

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

CMD ["uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
