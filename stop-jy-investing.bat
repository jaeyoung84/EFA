@echo off
chcp 65001 >nul
pushd "%~dp0." || (
    echo.
    echo 이 폴더로 이동할 수 없습니다: %~dp0
    pause
    exit /b 1
)
title JY investing 종료 중...

echo ============================================
echo   JY investing 컨테이너를 종료합니다
echo   (저장된 계정·포트폴리오 데이터는 유지됩니다)
echo ============================================
echo.

docker compose -f docker-compose.desktop.yml down
if errorlevel 1 (
    echo.
    echo 종료 중 오류가 발생했습니다. 위 메시지를 확인해 주세요.
) else (
    echo.
    echo 정상적으로 종료했습니다.
)

echo.
pause
popd
