#!/bin/bash
# mongo:7 공식 이미지가 최초 기동 시(빈 /data/db) 이 폴더를
# /docker-entrypoint-initdb.d 로 마운트하면 자동 실행합니다.
# ./data/*.json 에 담긴 퀴즈 문제·본인 제출 답안을 MONGO_INITDB_DATABASE에 그대로 적재합니다.
set -e

DB="${MONGO_INITDB_DATABASE:-jy_investing}"
# 공식 mongo 엔트리포인트는 이 스크립트를 `. "$f"`(source)로 실행하므로
# $0은 이 파일 경로가 아니라 상위 엔트리포인트 경로를 가리킵니다. 절대경로를 씁니다.
DIR="/docker-entrypoint-initdb.d/data"

for file in "$DIR"/*.json; do
  collection="$(basename "$file" .json)"
  echo "[mongo-init] ${collection} -> ${DB}"
  mongoimport --db "$DB" --collection "$collection" --file "$file"
done
