@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 먼저 install.bat 을 실행하세요.
    pause
    exit /b 1
)
echo ============================================
echo  WING 연결 확인 (USB 오디오 + LAN 제어)
echo  시작 전에 마이크나 악기 하나에서 소리가 나게 해 주세요.
echo ============================================
echo.
set "WINGIP="
set /p WINGIP=WING IP 주소를 입력하고 Enter (모르면 그냥 Enter): 
if "%WINGIP%"=="" goto audio_only

".venv\Scripts\python.exe" -m tools.verify_connection --audio --audio-test 5 --ip %WINGIP% --ch 1
goto ask_write

:audio_only
echo IP 없이 USB 오디오와 자동 검색만 확인합니다.
".venv\Scripts\python.exe" -m tools.verify_connection --audio --audio-test 5 --discover
goto done

:ask_write
echo.
set "DOWRITE="
set /p DOWRITE=쓰기 테스트도 할까요? 지정한 채널의 EQ 를 0.5dB 바꿨다가 바로 되돌립니다 (y/n): 
if /i not "%DOWRITE%"=="y" goto done
set "WCH="
set /p WCH=아무것도 연결되지 않은 채널 번호 (예: 40): 
if "%WCH%"=="" goto done
".venv\Scripts\python.exe" -m tools.verify_connection --ip %WINGIP% --write-test %WCH% --confirm-write

:done
echo.
echo ============================================
echo  끝났습니다. 열리는 reports 폴더에서 가장 최근 verify_....md 파일을 보내 주세요.
echo ============================================
if exist reports start "" reports
pause
