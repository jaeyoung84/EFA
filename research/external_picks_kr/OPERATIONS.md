# KR-PRO-CONSENSUS-v1 매달 운영 절차

기준: 매월 1일 리밸런싱 = 그달 첫 KRX 거래일 시가 진입. 첫 달 2026-11(11/2 월요일 시가).

1. **사용자(매달 1일, 첫 거래일 09:00 전)**: 사이트 '외부 추천 기록'에 두 명단을 올린다.
   출처는 정확히 `InvestingPro 한국 저평가 가치` / `InvestingPro 한국 대장주 가치`, 기록 날짜 = 그달 1일. CSV 또는 화면 표 붙여넣기.
   서버 수신 시각(created_at)이 진입 판정의 증거다. 09:00을 넘기면 다음 거래일 시가 진입(지연 기록).
2. **명단 내보내기(서버, SSM)**: 백엔드 컨테이너에서 해당 사용자의 그달 두 출처 문서를
   `[{source, as_of, created_at(UTC ISO), raw_sha256, items:[{ticker}]}]` JSON으로 내보내 S3 연구 내보내기 → 로컬
   `research/external_picks_kr/private/exports/<월>.json`(비공개, Git 제외).
3. **가격 구간**: KR-LV-v2와 같은 `export_window.py`(시작일 2025-08-01 고정)로 KRX 일봉 구간을 받는다.
4. **확정(진입일 전)**: `python forward.py freeze --month <월> --lists private/exports/<월>.json --krx <구간 DB>`
   → `private/forward/<월>/snapshot.json`(비공개) + `ledger_hashes.json`(해시·종목 수만) → **ledger_hashes.json을 바로 커밋·푸시**.
5. **평가(매달, 보유 달이 끝난 뒤)**: `python forward.py evaluate --krx <구간 DB>` → `FORWARD_RESULT.json`(집계만) 커밋.
6. **판정**: 2028-10 보유분의 교체 시가가 생긴 뒤 `python forward.py judge --krx <구간 DB>`(한 번, 2028-11-15까지).

명단 원문·종목별 결과는 커밋하지 않는다(`.gitignore`). 비공개 폴더는 별도 백업(S3 연구 내보내기)에 둔다.
