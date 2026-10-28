# WING Auto EQ Live

Behringer WING COMPACT용 실시간 라이브 음향 분석과 EQ 추천, 그리고 제한적 자동 EQ 보정 프로그램입니다 (Windows 10/11 64비트, Python 3.12).

* **오디오 입력은 USB**입니다. WING의 USB 오디오 인터페이스로 최대 48채널을 받아 분석합니다.
* **믹서 제어는 Ethernet LAN**으로 합니다. OSC(UDP 2223)로 채널 이름과 EQ를 읽고, 허용된 EQ 파라미터만 씁니다.
* 두 경로는 서로 다릅니다. LAN 제어로는 오디오가 오지 않고, USB 채널 번호만으로 믹서 채널을 알 수 없습니다. 그래서 매핑은 사용자가 설정합니다.

> ⚠ **현재 상태:** 실제 WING COMPACT(FW 3.1.2)에서 연결, USB 48ch 수신, EQ 읽기, 범위 질의, 게인 쓰기/복구까지 확인했습니다 (2026-10-28). 주파수·Q 쓰기, 단절 처리, 장시간 실행, 실제 악기 추천의 타당성은 아직 확인하지 않았습니다.
> 실제 WING에서는 **OSC를 켜야** 프로그램이 연결됩니다.
> 공연에 쓰기 전에 [docs/HARDWARE_VERIFICATION.md](docs/HARDWARE_VERIFICATION.md)의 절차를 실제 장비로 먼저 통과해야 합니다.

## 처음 사용한다면 (더블클릭만으로)

명령어를 입력하지 않아도 됩니다. 단계별 안내서는 [WING Auto EQ Live 처음 시작하기](https://claude.ai/artifact/1qPptEKiQwsZB7g8zZrzpY)에 있습니다.

| 순서 | 파일 | 하는 일 |
|---|---|---|
| 1 | `install.bat` | 처음 한 번 실행합니다. 필요한 부품을 설치합니다 (Python 3.12 필요) |
| 2 | `run_simulation.bat` | 장비 없이 연습합니다. 가짜 WING과 가짜 소리를 사용합니다 |
| 3 | `verify_wing.bat` | WING USB와 LAN 연결을 확인하고, `reports` 폴더에 결과를 저장합니다 |
| 4 | `run.bat` | 실제 프로그램을 실행합니다 |

## 1. 설치

1. Python 3.12 64-bit를 설치합니다. 설치할 때 "Add to PATH"를 체크합니다.
2. Behringer WING Windows USB 오디오 드라이버(ASIO)를 설치하고, WING COMPACT를 USB로 연결합니다.
3. 노트북과 WING을 유선 LAN으로 같은 서브넷에 연결합니다 ([docs/LAN_SETUP.md](docs/LAN_SETUP.md)).
4. 이 폴더에서 다음을 실행합니다.

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 2. 실행

```bat
REM (1) 단계 1 연결 검증 도구. 먼저 이것부터 실행합니다.
python -m tools.verify_connection --audio                     # USB 오디오 장치, 샘플레이트, 채널 수 확인
python -m tools.verify_connection --audio --audio-test 5      # 5초 동안 받아 채널별 레벨 측정
python -m tools.verify_connection --discover                  # LAN에서 WING 자동 검색
python -m tools.verify_connection --ip 192.168.1.71 --ch 1    # 응답, 채널 이름, EQ 읽기, 범위 질의
python -m tools.verify_connection --ip 192.168.1.71 --write-test 40 --confirm-write
REM   ↑ 쓰지 않는 채널(예: 40)의 EQ 밴드1 게인을 +0.5dB 바꾸고, 재조회한 뒤 원래대로 되돌립니다. 믹서 설정을 실제로 바꿉니다!

REM (2) 프로그램 실행
python main.py                                   # '연결 > 연결 및 설정'에서 IP와 오디오 장치를 선택합니다
python main.py --ip 192.168.1.71 --device 12 --channels 48
python main.py --simulate                        # 시뮬레이터와 합성 오디오 (실제 장비 아님, 화면 상단에 보라색 배너로 표시)
python main.py --wav show_multitrack.wav --ip 192.168.1.71   # 녹음한 멀티트랙을 재생해 분석

REM (3) 테스트
python -m pytest -q
```

검증 도구는 결과를 `reports/verify_*.md` / `.json`으로 저장합니다. 시뮬레이터로 실행한 결과는 파일 이름에 `_SIMULATION`이 붙습니다.

## 3. 사용 순서 (라이브)

1. **USB 라우팅을 확인합니다.** 믹서의 어떤 소스가 USB 몇 번으로 나가는지 확인합니다 ([docs/USB_ROUTING.md](docs/USB_ROUTING.md)).
2. **채널 매핑을 설정합니다.** 각 행을 선택한 뒤 오른쪽 '채널 설정'에서 믹서 채널, 이름, 악기, 마이크/DI를 지정합니다. 중복이나 범위 밖 매핑은 경고합니다.
3. **악기를 확정합니다.** 자동 악기 감지는 참고용입니다. 신뢰도가 최대 60%로 제한되며, 자동 보정에는 '악기 유형 확정' 체크가 필요합니다.
4. **모드 A (분석 전용, 기본값)** 에서는 레벨, 스펙트럼, 현재 EQ, 추천만 표시하고 믹서는 절대 바꾸지 않습니다.
5. **모드 B (추천 + 승인)** 에서는 추천 항목을 확인하고 '선택 항목 믹서에 적용'을 누를 때만 전송합니다. 전송한 뒤 믹서 값을 다시 조회해서, 실제 값이 같을 때만 "적용 완료 및 재조회 확인"으로 표시합니다.
6. **모드 C (제한적 자동)** 는 모드를 전환하고 확인한 뒤 '자동 보정 시작'을 눌러야 작동합니다. '자동 보정 허용'과 '악기 확정'이 모두 체크된 채널만 조정합니다.
7. 빨간 **■ 자동 보정 전체 정지** 버튼을 누르면 언제든 즉시 중지합니다.
8. '이전 설정 복구'는 이 프로그램이 마지막으로 바꾼 값만 되돌립니다. 그 뒤에 사용자가 콘솔에서 바꾼 항목은 덮어쓰지 않습니다.

## 4. 프로젝트 구조

```
main.py                     GUI 진입점 (--simulate / --wav / --ip / --device)
tools/verify_connection.py  단계 1 하드웨어 연결 검증 도구 (CLI, 보고서 생성)
audio/   device_manager.py  장치 검색, WING 후보, 샘플레이트·최대 채널 확인
         audio_stream.py    USB 입력 스트림 (콜백은 큐 삽입만), 드롭아웃 집계, 워치독 재연결
         channel_mapping.py USB↔믹서 채널 매핑, 검증
         spectrum.py        STFT(창 함수/FFT/홉 설정), 1/24 옥타브 대역, f0 추정
         dsp_analyzer.py    RMS/피크/클리핑/무음, 장기 평균, 지속성, 어택 구분, 분석 스레드
mixer/   osc_codec.py       OSC 인코딩/디코딩
         wing_addresses.py  문서로 확인한 포트, 주소, 값 형식 (출처 주석 포함)
         osc_client.py      UDP 요청-응답, 타임아웃, 재시도
         protocol_adapter.py  어댑터 인터페이스 + WingOscAdapter (+ 디스커버리)
         channel_reader.py  채널 이름, EQ, 필터 읽기, 범위 질의, 채널 수 확인
         parameter_validation.py  쓰기 허용 목록, 장비 범위 검증, 재조회 비교 허용오차
         eq_controller.py   검증 → 현재값 재확인 → 변경분만 전송 → 재조회 확인
         connection_manager.py  상태 확인(heartbeat), 단절 감지, 자동 재연결
eq/      instrument_profiles.py  21개 악기 프로필 (휴리스틱 출발점, JSON으로 덮어쓰기 가능)
         recommendation_engine.py  규칙 기반 추천 (근거, 신뢰도, 청음 확인 표시)
         feedback_monitor.py  하울링 의심도 (지속성, 돌출, 배음 관계)
         instrument_classifier.py  보조 악기 분류 (상한 0.6)
         eq_curve.py        EQ 커브 근사 (표시와 예상값 계산용)
         safety_controller.py  모드, 승인, 변경량·빈도·누적 제한, 중지 조건
core/controller.py          전체 흐름 (GUI와 분리되어 헤드리스로 테스트 가능)
gui/                        PySide6 화면 (main_window, channel_view, spectrum_view, eq_editor, settings_view)
storage/                    SQLite: 프로필, 변경 이력, 승인 기록, 이벤트, 분석 요약
simulator/                  WING OSC 시뮬레이터, 합성/파일 오디오 입력 (테스트 전용, 실장비와 구분 표시)
tests/                      단위 및 통합 테스트 (51개)
docs/                       라우팅, LAN, 알고리즘, 안전, 검증 기록, 문제 해결, 기능 현황
```

## 5. 문서

| 문서 | 내용 |
|---|---|
| [docs/PROTOCOL_NOTES.md](docs/PROTOCOL_NOTES.md) | 확인한 공식 문서, 주소와 포트의 근거, 미확인 항목 |
| [docs/USB_ROUTING.md](docs/USB_ROUTING.md) | USB 오디오 라우팅 안내, 분석 탭(pre/post EQ) |
| [docs/LAN_SETUP.md](docs/LAN_SETUP.md) | LAN 연결과 제어 설정 |
| [docs/ALGORITHM.md](docs/ALGORITHM.md) | DSP 분석과 EQ 추천 알고리즘 |
| [docs/SAFETY.md](docs/SAFETY.md) | 안전 기능 |
| [docs/HARDWARE_VERIFICATION.md](docs/HARDWARE_VERIFICATION.md) | 시뮬레이터 결과와 실제 장비 검증 절차·결과 (분리 기록) |
| [docs/BUILD_WINDOWS.md](docs/BUILD_WINDOWS.md) | Windows 실행 파일 빌드 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 문제 해결 |
| [docs/FEATURE_STATUS.md](docs/FEATURE_STATUS.md) | 구현 완료 기능과 미구현·미검증 기능 목록 |
