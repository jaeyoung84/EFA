# GitHub Codespaces 실행 및 웹페이지 공개 URL 공유 가이드

이 문서는 **GitHub Codespaces** 클라우드 환경에서 Docker Compose를 이용해 JY Investing 웹 애플리케이션을 1초 만에 구동하고, 생성된 웹페이지 주소(URL)를 시험관이나 외부 평가자에게 공유하는 방법을 안내합니다.

---

## 🚀 1. GitHub Codespaces 시작하기

1. 브라우저에서 GitHub 저장소로 이동합니다:
   👉 **https://github.com/jaeyoung84/IV-TEST**
2. 초록색 **`<> Code`** 버튼 클릭 ➔ **`Codespaces`** 탭 선택
3. **`Create codespace on main`** 클릭
4. 클라우드 개발 환경(VS Code 웹 에디터)이 브라우저에서 즉시 실행됩니다.

---

## ⚡ 2. 웹 애플리케이션 실행 (초경량 Docker Compose)

저장소의 `.devcontainer` 설정에 의해 Codespaces가 켜지면 백그라운드에서 자동으로 컨테이너가 실행됩니다.

만약 터미널에서 직접 실행하려면 아래 명령어 1줄만 입력하면 됩니다:

```bash
docker compose up -d
```

> **💡 가볍고 빠른 최적화 구성:**
> * 무거운 AI 모델(Ollama LLM), Qdrant(벡터DB), Meilisearch 등 리소스를 많이 차지하는 요소를 모두 정리하여 **Codespaces 무료 사양(2-Core 4GB RAM)에서도 5초 이내에 가볍게 실행**됩니다.
> * 내장 MongoDB 7.0과 **예시 시험 결과(30/30)**가 All-in-One 이미지(`jaeyoungsong/jy-investing:latest`)에 이미 내장되어 있어 추가 작업 없이 즉시 웹페이지에 반영됩니다.

---

## 🌐 3. 생성된 웹페이지 공개 경로(URL) 공유 방법

Codespaces에서 웹 서버가 실행되면 외부에서 누구나 접속할 수 있는 **고유 웹 URL**이 생성됩니다.

1. 화면 하단 패널에서 **`PORTS` (포트)** 탭을 클릭합니다.
2. `8000`번 포트 항목을 확인합니다:
   * **Port Visibility(공개 범위)**: `Public`으로 설정되어 있는지 확인합니다. (만약 `Private`으로 되어 있다면 우클릭 ➔ **`Port Visibility` ➔ `Public`** 선택)
3. **`Forwarded Address` (전달된 주소)** 열의 복사 아이콘(📋)을 클릭합니다.
   * 주소 형식: `https://<고유이름>-8000.app.github.dev`
4. **이 URL을 시험관이나 평가자에게 그대로 전달하시면 됩니다!**
   * 상대방은 별도의 도커 설치나 로그인 없이 스마트폰이나 PC 웹 브라우저에서 실시간으로 웹페이지를 볼 수 있습니다.

---

## 📝 4. 시험관 안내용 확인 경로

공유받은 주소(`https://...app.github.dev`)로 접속 후:
* **주식 어휘력 평가 시험 결과**: 상단 메뉴 **'리서치 자료실' ➔ '주식 어휘력 평가 시험'**
* **확인 내용**: 예시 제출자의 **30/30** 채점표 및 30개 문항 상세 내역
