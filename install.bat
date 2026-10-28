@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  WING Auto EQ Live - 설치 (처음 한 번만)
echo ============================================
echo.
echo [1/3] Python 확인 중...
set "PY="
py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY py -3 -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul && set "PY=python"
if not defined PY (
    echo.
    echo Python 을 찾을 수 없습니다.
    echo python.org 에서 Python 3.12 를 설치할 때 "Add python.exe to PATH" 를 체크하고,
    echo 노트북을 재시작한 뒤 다시 실행하세요.
    goto fail
)
%PY% --version
echo.
echo [2/3] 프로그램 전용 환경 만드는 중...
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv
    if errorlevel 1 goto fail
)
echo.
echo [3/3] 필요한 부품 내려받는 중 (5~10분, 창을 닫지 마세요)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo.
echo ============================================
echo  설치 완료
echo  다음: run_simulation.bat (연습) 또는 verify_wing.bat (WING 연결 확인)
echo ============================================
pause
exit /b 0

:fail
echo.
echo ============================================
echo  설치 실패 - 이 창을 화면 캡처해서 보내 주세요
echo ============================================
pause
exit /b 1
