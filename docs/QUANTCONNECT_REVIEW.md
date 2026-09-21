# QuantConnect 공식 저장소 도입 검토

검토 기준일: 2026-09-10

## 결론

현재 프로젝트는 이미 `quantconnect/lean:latest` 공식 엔진 이미지로 삼성전자·현대자동차
백테스트를 실행한다. 따라서 수 GB 규모의 `QuantConnect/Lean` 소스를 다시 복제하지 않고,
로컬 프로젝트 생성·백테스트·최적화 워크플로우를 제공하는 공식 `QuantConnect/lean-cli`만
별도 개발 도구로 도입했다. 버전은 재현성을 위해 `1.0.227`로 고정했다.

```bash
bash scripts/setup_lean_cli.sh
.venv-lean/bin/lean --version
```

CLI 환경은 웹앱 Python 의존성과 분리된 `.venv-lean`에 설치된다. `lean.json`, `.lean`,
가상환경은 Git에서 제외한다. API 토큰을 저장소 파일이나 명령행에 넣지 않는다.

## 저장소별 판단

| 공식 저장소 | 판단 | 이유 |
| --- | --- | --- |
| `QuantConnect/Lean` | 기존 이미지 사용 유지 | 이미 두 독립 Compose가 공식 엔진을 사용한다. 전체 소스 벤더링은 중복과 업데이트 부담이 크다. |
| `QuantConnect/lean-cli` | 도입 | 프로젝트 스캐폴딩, 로컬 백테스트, 리서치, 최적화 흐름을 표준화한다. |
| `QuantConnect/Research` | 지금은 미도입 | 예제 노트북은 유용하지만 미국 데이터 중심이고 현재 리서치·백테스트 기능과 상당 부분 중복된다. 필요한 노트북만 출처와 함께 선별 이식한다. |
| `QuantConnect/Documentation` | 링크 참조 | 실행 의존성이 아니므로 복제하지 않는다. |
| `HandsOnAITradingBook` | 미도입 | 교육 예제 묶음이며 현재 운영 베이스에 직접 필요한 런타임 코드가 아니다. |

## 안전 경계

- `lean live`와 `lean cloud` 명령은 현재 운영 절차에 포함하지 않는다.
- `lean login`은 사용자 명시 승인과 별도 Secret Manager가 준비되기 전에는 실행하지 않는다.
- QuantConnect 데이터셋은 코드의 Apache-2.0 라이선스와 별개일 수 있으므로 자동 반입하지 않는다.
- Algorithm Framework의 Alpha/Portfolio/Risk/Execution 분리는 16단계 전략 포트폴리오 설계에
  참고하되, 현재 모의 주문 안전장치를 우회하는 코드는 연결하지 않는다.
