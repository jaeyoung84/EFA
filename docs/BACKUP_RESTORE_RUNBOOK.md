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

## 운영 서버(EC2) 사이트 DB 일일 백업

- 대상: `investment_mongo` 컨테이너의 `investment_db`(회원·판단 기록·포트폴리오·관심종목·노트). KQS 엔진 상태는
  별도 `kqs-backup.timer`가 백업한다.
- 실행: `app-mongo-backup.timer`가 매일 04:10 KST에 `infra/ops/app-mongo-backup.sh`를 돌린다. mongodump 압축본과
  SHA-256을 만들고, 같은 Mongo의 임시 DB(`restore_check_*`)에 복원해 컬렉션 수·회원 수가 같은지 확인한 뒤 임시 DB를
  지운다. 실패하면 `kqs-alert@`로 알린다.
- 보관: 서버 `/srv/app-backups/<시각>/`에 최근 14개, S3 `APP_BACKUP_S3_URI`(비공개·SSE)에 업로드.
- 수동 실행: `sudo systemctl start app-mongo-backup.service && journalctl -u app-mongo-backup.service -n 20`
- 복구(승인 후에만): 아카이브 SHA-256 확인 → 임시 DB로 복원해 내용 확인 →
  `docker exec -i investment_mongo mongorestore --archive --gzip --nsInclude 'investment_db.*' --drop < <아카이브>`.
  `--drop`은 운영 DB를 덮어쓰므로 백엔드를 멈추고, 복구 직전 상태를 한 번 더 백업한 뒤에만 쓴다.
- 설치(2026-10-02): 서버에 스크립트·타이머 설치, `/srv/kqs/secrets/ops.env`에
  `APP_BACKUP_S3_URI=s3://kqs-state-125022369601/app-backups/mongo` 추가. 첫 실행: 컬렉션 58, 문서 5,778, 복구 확인 ok,
  S3 업로드 확인. S3 수명주기 `expire-app-backups`(app-backups/, 30일) 추가 — 기존 규칙(backups/ 30일, 이전 버전 7일)은 유지.

## 서버 디스크 스냅샷(EBS)

- AWS DLM 정책 `policy-0bd72951562c6cf52`: 태그 `EfaBackup=daily`가 붙은 볼륨(서버 루트 `vol-0a3277601c845ab80`)을
  매일 18:30 UTC(03:30 KST)에 스냅샷, 최근 7개 보관. 디스크 전체(엔진 시장 데이터·설정·Mongo 볼륨 포함)를 되살리는 2차 수단이다.
- 복구: 스냅샷에서 새 볼륨을 만들어 임시 인스턴스에 붙여 확인하거나, 승인 후 루트 볼륨을 교체한다. 실행 중 스냅샷이라
  Mongo는 충돌 직후 상태와 같으므로 DB는 위 일일 백업을 우선 쓴다.
