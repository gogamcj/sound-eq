# 안전 기능

| 요구 사항 | 구현 | 테스트 |
|---|---|---|
| 기본 모드는 분석 전용 | `SafetyController.mode = ANALYSIS_ONLY`. 모드 A에서는 적용과 복구 모두 거부 | `test_default_mode_is_analysis_only_and_blocks_writes` |
| 승인 없는 변경 금지 | 모드 B는 사용자 버튼 경로(`apply_recommendation`)만 쓰기 가능. `auto_tick`은 아무것도 하지 않음 | `test_mode_b_user_apply_verified_and_recorded` |
| 자동 보정은 명시적으로 켜기 | 모드 C로 전환(확인 대화상자)한 뒤 '자동 보정 시작'을 눌러야 함. LAN과 오디오가 정상이어야 켜짐 | `test_auto_requires_mode_c_explicit_enable_and_limits_step` |
| 자동 채널 개별 지정 | '자동 보정 허용'과 '악기 유형 확정'이 모두 필요. 자동 인식만으로는 적용하지 않음 | `test_auto_denied_for_unconfirmed_instrument_or_not_allowed` |
| 신호 부족, 무음, 클리핑 | 자동 적용 거부. 추천도 하지 않음 | `test_auto_denied_on_silence_and_clipping` |
| 변경량, 빈도, 누적 제한 | 1회 최대 1.5 dB, 채널별 최소 간격 20초, 시작 기준 누적 6 dB, 1회에 1밴드, 자동 부스트 금지, HPF 자동 변경 금지 (모두 설정 가능) | `test_cumulative_limit` |
| 사용 중인 밴드 보존 | 자동 모드에서는 게인이 0이 아닌 밴드의 주파수와 Q를 옮기지 않음 | `safety_controller._limit` |
| 변경 전 현재값 저장 | 적용 직전에 재조회한 값을 이력(before)과 baseline에 저장 | `test_restore_*` |
| 외부 변경 감지 | 추천 계산 때의 값과 적용 직전 값이 다르면 중단 (사용자가 콘솔에서 바꾼 경우) | `test_external_change_aborts_apply` |
| 파라미터 사전 검증 | 허용 키 목록, 장비 범위, 유한값. 금지 경로(게인, 팬텀, 페이더, 라우팅) 차단 | `test_validation_blocks_*` |
| 적용 후 재조회 | 재조회 값이 같을 때만 '적용 완료 및 재조회 확인' | `test_write_verified_by_readback`, `test_write_not_applied_*` |
| 동일 설정 반복 전송 금지 | 현재값과 같은 키는 보내지 않음 | `test_write_verified_by_readback` |
| 통신 실패 시 중지 | 단절(약 1–2초 안에 감지) 또는 쓰기 통신 실패 → 전체 자동 중지 | `test_comm_failure_stops_auto` |
| 오디오 오류 시 중지 | 오디오 상태가 ERROR/NO_DEVICE → 자동 중지 | `test_audio_error_stops_auto` |
| 하울링 의심 시 | 의심도 0.8 이상이면 그 채널의 일반 EQ 자동 적용을 보류. 자동 노치는 별도 옵션(기본 꺼짐) | `check_auto` |
| 음질 악화 의심 | 적용 뒤 지표가 1 dB 이상 나빠지면 그 채널의 자동 보정 정지 | `_run_pending_checks` |
| 전체 즉시 정지 | 빨간 버튼 → `emergency_stop`. 사용자가 다시 켜기 전까지 자동 보정 없음 | `test_stop_button_halts_auto` |
| 이전 설정 복구 | 프로그램이 바꾼 키만 대상. 현재값이 프로그램이 쓴 값과 다르면 건너뜀 | `test_restore_only_program_changes_and_respects_user_edits` |
| 종료 시 | 자동 중지, 스레드 정리. **믹서에는 아무것도 쓰지 않음** → 믹서 처리는 그대로 유지 | `test_shutdown_stops_auto_and_does_not_touch_mixer` |
| 금지 기능 | 프리앰프 게인, 팬텀 전원, 출력 레벨, 라우팅은 코드상 쓰기 불가 (WRITABLE_KEYS, FORBIDDEN_SUBSTRINGS) | `test_validation_blocks_*` |

## 독립성

이 프로그램은 믹서의 오디오 경로에 끼어들지 않습니다. USB로 사본을 받고, LAN으로 파라미터만 바꿉니다. 프로그램이 멈추거나 종료되거나 노트북이 꺼져도 믹서는 마지막 설정으로 계속 동작합니다. 다만 프로그램이 마지막으로 적용한 EQ는 남아 있습니다. 되돌리려면 '이전 설정 복구'를 쓰십시오.

## 한계

* 하울링 감지는 보조 기능입니다. 완전한 제거를 보장하지 않습니다. 마이크 위치, 스피커 배치, 게인 구조, 공간 음향을 먼저 점검하십시오.
* USB 신호가 EQ 이전 탭이면, 적용한 뒤의 개선 확인은 측정이 아니라 근사 커브를 이용한 예측입니다.
