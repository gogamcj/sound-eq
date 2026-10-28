@echo off
REM WING Auto EQ Live - Windows 실행 파일 빌드
REM 사전 조건: Python 3.12 64-bit 설치, 이 폴더에서 실행
setlocal
if not exist .venv (
    py -3.12 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || goto :err
python -m pytest -q || goto :err
pyinstaller --noconfirm wing_auto_eq.spec || goto :err
echo.
echo 빌드 완료: dist\WingAutoEQ\WingAutoEQ.exe
echo (dist\WingAutoEQ 폴더 전체를 배포하세요)
goto :eof
:err
echo 빌드 실패 (위 오류 확인)
exit /b 1
