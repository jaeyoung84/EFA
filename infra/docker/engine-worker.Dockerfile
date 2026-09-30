# KQS engine worker: collect -> verify -> single-writer publish, backup and health check.
# Build context is the repository root. Contains no keys, market data or state.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

WORKDIR /opt/kqs
COPY engine/kqs/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY engine/kqs/ ./engine-src/
RUN pip install --no-cache-dir --no-deps ./engine-src \
    && rm -rf ./engine-src \
    && pip check

# Unprivileged writer. Host directories mounted at /var/lib/kqs must be owned by UID 10001.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin kqs
USER 10001
WORKDIR /var/lib/kqs

ENTRYPOINT ["kqs-daily-pipeline"]
CMD ["--state-dir", "/var/lib/kqs/state", "--market-db", "/var/lib/kqs/market/krx_daily.sqlite3", \
     "--env-file", "/run/kqs/krx.env"]
