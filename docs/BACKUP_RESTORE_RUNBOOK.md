# MongoDB 백업·복구 런북

이 절차는 개인 퀀트 시스템의 MongoDB 논리 백업과 복구 가능 여부를 확인한다. 운영 DB를
직접 복원하거나 덮어쓰지 않는다.

## 사전 확인

- 서비스가 healthy인지 확인한다.
- `.env.desktop`의 `MONGODB_DB`가 현재 운영 DB명과 같은지 확인한다.
- `backups/`는 Git에서 제외된다. 백업 아카이브에는 계정·전략·감사 기록이 들어갈 수 있으므로
  암호화된 별도 저장소로 보관한다.

```bash
docker compose --env-file .env.desktop ps
```

## 백업 생성

```bash
scripts/mongo_backup.sh .env.desktop
sha256sum -c backups/<생성된-파일>.sha256
```

백업 스크립트는 `mongodump` 압축 아카이브와 SHA-256 무결성 파일을 만든다. 생성 뒤에는
보관 정책에 따라 오프사이트·암호화 저장소로 복사한다. 애플리케이션은 자동 업로드하지 않는다.

## 복구 훈련

```bash
scripts/mongo_restore_drill.sh backups/<생성된-파일>.archive.gz .env.desktop
```

스크립트는 시간표시가 붙은 임시 DB에만 `mongorestore`를 실행하고, 컬렉션이 하나 이상
복원됐는지 확인한 뒤 임시 DB를 삭제한다. 운영 DB 이름을 `--drop` 대상으로 사용하지 않는다.

## 장애 시 운영 순서

1. `.env.desktop`에서 `EXECUTION_GLOBAL_KILL_SWITCH=true`로 바꾸고 백엔드를 재기동해 모의 주문을 중지한다.
2. 감사 원장과 주간 운영 리포트를 내보낸다.
3. 최신 백업의 SHA-256을 검증하고, 먼저 위 복구 훈련으로 아카이브를 확인한다.
4. 실제 운영 DB 복구는 별도 승인·보관된 절차에서만 수행한다. 이 런북과 스크립트는 운영 DB 복구를 자동화하지 않는다.
5. 데이터 정합성·감사 원장·전략 승인 상태를 확인한 후에만 킬스위치를 해제한다.
