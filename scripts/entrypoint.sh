#!/bin/bash
set -e

# MongoDB 데이터 디렉터리 및 로그 디렉터리 생성 및 권한 부여
mkdir -p /data/db /var/log/mongodb
chown -R mongodb:mongodb /data/db /var/log/mongodb 2>/dev/null || true

USE_INTERNAL_MONGO=true
if [ -n "$MONGODB_URL" ] && [ "$MONGODB_URL" != "mongodb://localhost:27017" ] && [ "$MONGODB_URL" != "mongodb://127.0.0.1:27017" ]; then
    USE_INTERNAL_MONGO=false
fi

if [ "$USE_INTERNAL_MONGO" = "true" ]; then
    echo "=========================================================="
    echo " [JY-INVESTING] Starting embedded MongoDB 7.0 daemon...   "
    echo "=========================================================="
    su -s /bin/sh mongodb -c "mongod --dbpath /data/db --logpath /var/log/mongodb/mongod.log --bind_ip_all --fork" 2>/dev/null || mongod --dbpath /data/db --logpath /var/log/mongodb/mongod.log --bind_ip_all --fork
    
    python3 -c "
import socket, time, sys
for _ in range(30):
    try:
        with socket.create_connection(('127.0.0.1', 27017), timeout=1):
            sys.exit(0)
    except OSError:
        time.sleep(0.5)
sys.exit(1)
"
    echo " [JY-INVESTING] Embedded MongoDB 7.0 is online and ready! "
    export MONGODB_URL="mongodb://127.0.0.1:27017"
else
    echo "=========================================================="
    echo " [JY-INVESTING] Using external MongoDB: $MONGODB_URL      "
    echo "=========================================================="
fi

cleanup() {
    echo "=== [JY-INVESTING] Stopping services... ==="
    if [ "$USE_INTERNAL_MONGO" = "true" ]; then
        mongod --dbpath /data/db --shutdown 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGTERM SIGINT

echo "=== [JY-INVESTING] Starting FastAPI backend on port 8000... ==="
uvicorn app.backend.main:app --host 0.0.0.0 --port 8000 &
PID=$!
wait $PID
