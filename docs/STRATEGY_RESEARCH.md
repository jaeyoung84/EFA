# 전략 연구 환경

`POST /api/strategy/research`는 MA 후보 조합을 시간 순서대로 평가한다. 로그인한
사용자의 결과만 `strategy_research_runs`에 저장한다.

## 과적합 방지 순서

1. 전체 가격 이력을 훈련 50%, 검증 25%, 최종 테스트 25%로 시간 순서대로 분리한다.
2. 후보 조합의 Sharpe(동률이면 총수익률)를 검증 구간에서 비교한다.
3. 선택된 조합은 한 번도 선택에 쓰지 않은 최종 테스트 구간에서만 평가한다.
4. 확장형 워크포워드는 과거 검증 블록에서 매번 조합을 다시 고르고 뒤의 테스트 블록을 평가한다.

실행 예시:

```bash
curl -X POST http://localhost:18000/api/strategy/research \
  -H 'Authorization: Bearer <로그인 토큰>' \
  -H 'Content-Type: application/json' \
  -d '{"ticker":"005930.KS","period":"5y","fast_candidates":[5,10,20],"slow_candidates":[40,60,90],"walk_forward_folds":3}'
```

응답의 `selected.final_test`는 최종 평가 결과이며 파라미터 선택 근거로 사용하면 안 된다.
`sensitivity`는 각 조합의 훈련·검증 성과, `walk_forward`는 각 시점의 테스트 성과다.
결과는 단일 종목·일봉·롱/현금 MA 연구용이며 투자 권유나 실거래 신호가 아니다.

## 36~39단계 시점 검증·팩터 조건·후보 판정

연구 결과는 `point_in_time_audit`에 팩터 관측 시점(t일 종가), 다음 시가 체결 규칙,
최종 테스트의 선택 제외 여부와 입력 지문을 기록한다. `factor_filter_enabled=true`로
20일 수익률 하한(`min_return_20d`)과 20일 실현변동성 상한(`max_realized_volatility_20d`)을
고정 조건으로 줄 수 있으며, 이 값은 파라미터 탐색 대상이 아니다.

`cost_stress`는 같은 최종 테스트를 0.5배·1배·2배 비용으로 재평가하고,
`paper_candidate_assessment`는 최종 테스트 20일 이상, 양의 Sharpe·수익률, 2배 비용에서의
양의 수익률, 시점 검증 통과 여부를 함께 확인한다. `paper_candidate`는 연구 검토용 표식일
뿐 모의 주문·실거래 권한·전략 승인을 자동으로 만들지 않는다.
