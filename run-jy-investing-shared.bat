@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
pushd "%~dp0." || (
    echo.
    echo       이 폴더로 이동할 수 없습니다: %~dp0
    pause
    exit /b 1
)
title JY investing (공유 이미지) 실행 중...

echo ============================================
echo   JY investing - 공유받은 Docker 이미지로 실행
echo   (소스 코드 없이 jaeyoungsong/jy-investing 이미지를 내려받습니다)
echo ============================================
echo.

echo [1/3] Docker Desktop 상태를 확인합니다...
docker info >nul 2>&1
if not errorlevel 1 goto docker_already_running

echo       Docker Desktop이 꺼져 있어 자동으로 실행합니다.
set "DOCKER_DESKTOP_EXE=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if not exist "%DOCKER_DESKTOP_EXE%" (
    echo.
    echo       Docker Desktop 실행 파일을 찾지 못했습니다.
    echo       https://www.docker.com/products/docker-desktop/ 에서 설치한 뒤
    echo       이 파일을 다시 실행해 주세요.
    echo.
    pause
    exit /b 1
)
start "" "%DOCKER_DESKTOP_EXE%"

echo       Docker 엔진이 준비될 때까지 기다립니다(최대 2분)...
set /a DOCKER_WAIT=0

:wait_docker_loop
timeout /t 3 >nul
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready
set /a DOCKER_WAIT+=1
if !DOCKER_WAIT! GEQ 40 (
    echo.
    echo       Docker Desktop 시작이 오래 걸리고 있습니다.
    echo       Docker Desktop이 완전히 켜진 뒤 이 파일을 다시 실행해 주세요.
    echo.
    pause
    exit /b 1
)
goto wait_docker_loop

:docker_ready
echo       Docker Desktop 준비 완료.
goto after_docker_check

:docker_already_running
echo       Docker Desktop이 이미 실행 중입니다.

:after_docker_check
echo.
echo [2/3] jaeyoungsong/jy-investing 이미지를 내려받고 컨테이너를 시작합니다.
echo       (최초 실행은 이미지 용량 때문에 몇 분 걸릴 수 있습니다)
echo.
docker compose -f docker-compose.share.yml up -d
if errorlevel 1 (
    echo.
    echo       컨테이너 시작에 실패했습니다. 위 오류 메시지를 확인해 주세요.
    echo.
    pause
    exit /b 1
)

echo.
echo [3/3] 서버가 준비될 때까지 기다립니다...
set /a APP_WAIT=0

:wait_app_loop
set "APP_HEALTH="
for /f "usebackq tokens=*" %%H in (`docker inspect --format="{{.State.Health.Status}}" jy_investing_backend 2^>nul`) do set "APP_HEALTH=%%H"
if "!APP_HEALTH!"=="healthy" goto app_ready
timeout /t 3 >nul
set /a APP_WAIT+=1
if !APP_WAIT! GEQ 40 goto wait_timeout
goto wait_app_loop

:wait_timeout
echo       서버 준비를 기다리다 시간이 초과됐습니다.
echo       잠시 후 브라우저에서 http://localhost:8000 을 직접 열어 보세요.
goto open_browser

:app_ready
echo       서버가 준비됐습니다!

:open_browser
start "" "http://localhost:8000"

echo.
echo ============================================
echo   실행 완료!
echo   브라우저가 자동으로 열리지 않으면
echo   http://localhost:8000 을 직접 열어 주세요.
echo.
echo   이 컴퓨터에서 새로 계정을 만들어야 합니다(다른 사람과 계정·
echo   보유자산이 공유되지 않습니다 - 각자 자기 컴퓨터에서 독립적으로 실행됩니다).
echo.
echo   앱을 끄려면 stop-jy-investing-shared.bat 을 실행하세요.
echo ============================================
echo.
pause
popd
endlocal
