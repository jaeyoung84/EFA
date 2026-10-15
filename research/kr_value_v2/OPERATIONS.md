# KR-LV-v2 매달 운영 절차

형성일 = 매월 마지막 거래일. 명단은 형성일 장 마감 뒤 ~ 다음 달 첫 거래일 개장 전에 만든다.

1. **가격 구간 추출(서버 호스트)**: `export_window.py /srv/kqs/market/krx_daily.sqlite3 <out> 2025-08-01` (시작일 고정: 첫 형성의 12개월 이력 + 이후 전 기간, 평가에도 같은 파일 사용)
   → gzip, sha256, `s3://…/research-exports/kr-value-v2/<stamp>/`. 형성일 종가가 수집됐는지 먼저 확인(장 마감 뒤 수집 작업).
2. **재무 갱신(서버 백엔드 컨테이너)**: 형성일 시총 상위 300개(보통주) 이름 목록 → `collect_dart.py names.json kr_fund.sqlite3 <올해−3> <올해>`
   → 로컬에서 `make_gaps.py`로 순이익 빈 칸 목록 → 컨테이너에서 `fill_gaps.py gaps.csv kr_fill.sqlite3` → 둘 다 S3로.
3. **명단(로컬)**: 세 파일을 한 폴더에 내려받아 해시 확인 뒤
   `python forward.py pick --krx <window> --fund <dir>/kr_fund.sqlite3 --fill <dir>/kr_fill.sqlite3 --form <형성일>`
   → `ledger/<형성월>.json` 생성 → **바로 git 커밋·푸시**(첫 거래일 개장 전 시각 증거).
4. **사이트 기록(선택)**: 외부 추천 명단 기록기에 출처 `KR-LV-v2(우리 모형)`, 기준일 = 다음 달 첫 거래일로 20종목 입력.
   같은 달 InvestingPro 명단도 출처 `InvestingPro 한국 저평가 가치`로 입력.
5. **평가(매달, 보유 달이 끝난 뒤)**: 최신 가격 구간으로 `python forward.py evaluate --krx <window>` → `FORWARD_RESULT.json` 커밋.
   누적 초과가 −15%p 이하이면 조기 중단(REJECT). 12개월 중간 보고, 24개월(보유 2028-10까지) 판정.

서버 작업은 SSM으로 하며 1시간 넘는 일은 컨테이너 안 nohup 백그라운드로 돌리고 로그를 확인한다. 끝나면 서버 임시 파일을 지운다.
