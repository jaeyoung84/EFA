# EINT-004 — KRX 거래 세션과 추천 최신성

이 정책은 추천 게시 시의 KRX **주식** 거래일/자료 기준일을 검증한다. 주문·자동 모의체결,
시장 데이터 수집, AWS 스케줄러를 만들지 않는다. 추천 계산 결과가 실제로 최신 데이터인지
수집기와 대조하는 기능도 다음 단계다.

## 기준과 범위

패키지의 [2026 캘린더](../../engine/kqs/src/kqs_publication/calendars/krx_equity_2026.json)는
KRX의 거래일 규칙(평일, 공휴일, 근로자의 날, 연말 휴장 및 시장이 별도로 정한 휴장)을
출처로 하며, 검토한 연도별 휴장 예외를 명시한다. [KRX 거래 규칙](https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp)과
[KRX 증시일정](https://global.krx.co.kr/contents/GLB/05/0501/0501060000/GLB0501060000T3.jsp)을
참조했다. 새해/임시 휴장은 새 파일·SHA·successor manifest·테스트를 추가해야 한다.
알 수 없는 연도는 평일로 추정하지 않고 게시/조회 모두 실패한다.

2026 추석의 마지막 거래일은 9월 23일, 휴장은 24·25일, 다음 거래일은 28일로 고정했다.
이 사실은 코드·회귀 테스트에 같이 기록되어 단순 월~금 계산으로 대체되지 않는다.

`06:00 KST`는 **이 시스템의 보수적인 데이터 공개 기대 시각**이다. KRX가 이 시각에
특정 DB/공급자를 갱신한다는 보증이 아니다. 따라서 worker는 실제 가격 DB의 기준일을
별도로 검증해야 하며, 수집 성공 증명은 하지 않는다.

## 게시 조건

native 추천은 다음을 전부 만족해야 한다.

1. `data_as_of`가 해당 연도 검토 캘린더의 세션이다.
2. 생성 시점에 공개 기대 시각이 지난 세션 중 가장 최신 세션과 정확히 같다.
3. payload에 `calendar_id`, 설치된 캘린더 raw SHA-256, 정책 ID, 기대 시각을 남긴다.
4. 만료는 생성 후 24시간 이내이면서, 다음 세션 자료의 공개 기대 시각보다 이르다.
5. 홈페이지 reader도 같은 설치 캘린더 SHA를 재확인한다. 누락/변조/미지원 연도는 fail-closed다.

따라서 9월 28일에는 9월 23일 자료를 잠시 게시할 수 있지만, 9월 29일 06:00 KST 이후에는
9월 28일 자료가 아니면 게시할 수 없다. 이 경우도 데이터가 실제로 도착했는지는 아직
별도 collector 품질 검증이 필요하다.

EINT-003 형식의 native snapshot에는 캘린더 metadata가 없으므로 EINT-004 reader에서
의도적으로 거부된다. 안전한 재게시가 필요하지만, 이번 단계에서는 실제 snapshot을 만들거나
기존 DB를 바꾸지 않았다.

## 확인

```bash
python3 engine/kqs/tools/check_revision.py
PYTHONPATH=engine/kqs/src:engine/kqs/tests \
  python3 -m unittest discover -s engine/kqs/tests -p 'test_publication.py' -v
```

검증은 합성 가격·임시 SQLite만 사용한다. 특히 휴장 24·25일, 재개 28일, 자료 공개
경계, 오래된 기준일, 만료 상한, calendar SHA 변조를 검사한다.

## 다음 단계

1. KRX 캘린더 연도 갱신 절차와 공식 일정 수집/검토·승인 기록.
2. 별도 collector의 가격/ETF 완전성·기준일·공표 지연 관측과 immutable run 기록.
3. 그 뒤에만 단일 writer worker, 영속 저장/백업, AWS PC 독립 실행을 별도 승인·배포 검증으로 진행.

캘린더는 전략 성과, 실제 시세 정확성, 수집기 건강, 자동매매 준비 상태를 보증하지 않는다.
