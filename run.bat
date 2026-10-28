@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 먼저 install.bat 을 실행하세요.
    pause
    exit /b 1
)
echo WING Auto EQ Live 실행 중... (이 검은 창은 닫지 마세요)
".venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo.
    echo 오류로 종료되었습니다. 이 창과 logs 폴더의 wing_auto_eq.log 를 보내 주세요.
    pause
)
