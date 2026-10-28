# Windows 실행 파일 빌드

```bat
build_windows.bat
```

이 스크립트는 가상환경을 만들고, `requirements.txt`를 설치하고, 테스트를 실행한 다음, PyInstaller(`wing_auto_eq.spec`)로 빌드합니다.

결과물:
* `dist\WingAutoEQ\WingAutoEQ.exe`: GUI 프로그램 (onedir 방식이므로 폴더째 배포)
* `dist\WingVerify\WingVerify.exe`: 연결 검증 도구 (콘솔)

실행 파일 옆에 `config\`, `data\`(SQLite), `logs\`, `reports\`가 만들어집니다.

참고:
* sounddevice의 PortAudio DLL(`_sounddevice_data`)은 spec에서 함께 포함합니다. ASIO 장치가 목록에 보이지 않으면 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)를 보십시오.
* 이 빌드는 개발 환경(리눅스)에서 실행해 보지 못했습니다. **Windows에서 직접 빌드하고 실행해서 확인해야 합니다.**
* 코드 서명이 없어 SmartScreen 경고가 나올 수 있습니다.
