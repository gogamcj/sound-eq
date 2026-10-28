# 연결 검증 결과 (2026-10-28 01:56:08)

**시뮬레이터 결과 — 실제 장비 검증 아님**

| 단계 | 결과 | 내용 |
|---|---|---|
| OSC 응답 (/?) | PASS | 0.4 ms, model=SIMULATOR-not-real-hardware, name=SIM, fw=sim-0.1, serial=NO_SERIAL |
| 입력 채널 수 (/ch/N/name 응답) | PASS | 40개 |
| ch1 이름 | PASS | 'CH1' |
| ch1 EQ/필터 읽기 | PASS | 29개 읽음 |
| ch1 EQ 상태 | INFO | on=True, model=STD |
|   값 | INFO | eq/on = 1 (text '1', raw 1.0) |
|   값 | INFO | eq/mdl = STD (text 'STD', raw None) |
|   값 | INFO | eq/lg = 0.0 (text '0.0', raw 0.5) |
|   값 | INFO | eq/lf = 80.19999694824219 (text '80.2', raw 0.003013012930750847) |
|   값 | INFO | eq/lq = 1.0 (text '1.00', raw 0.05857740715146065) |
|   값 | INFO | eq/leq = SHV (text 'SHV', raw None) |
|   값 | INFO | eq/1g = 0.0 (text '0.0', raw 0.5) |
|   값 | INFO | eq/1f = 200.0 (text '200.0', raw 0.009009009227156639) |
|   값 | INFO | eq/1q = 1.0 (text '1.00', raw 0.05857740715146065) |
|   값 | INFO | eq/2g = 0.0 (text '0.0', raw 0.5) |
|   값 | INFO | eq/2f = 601.4000244140625 (text '601.4', raw 0.029099099338054657) |
|   값 | INFO | eq/2q = 1.0 (text '1.00', raw 0.05857740715146065) |
|   값 | INFO | eq/3g = 0.0 (text '0.0', raw 0.5) |
|   값 | INFO | eq/3f = 1500.0 (text '1k50', raw 0.07407407462596893) |
|   값 | INFO | eq/3q = 1.0 (text '1.00', raw 0.05857740715146065) |
|   값 | INFO | eq/4g = 0.0 (text '0.0', raw 0.5) |
|   값 | INFO | eq/4f = 3990.0 (text '3k99', raw 0.19869869947433472) |
|   값 | INFO | eq/4q = 1.0 (text '1.00', raw 0.05857740715146065) |
|   값 | INFO | eq/hg = 0.0 (text '0.0', raw 0.5) |
|   값 | INFO | eq/hf = 12000.0 (text '12k00', raw 0.5995995998382568) |
|   값 | INFO | eq/hq = 1.0 (text '1.00', raw 0.05857740715146065) |
|   값 | INFO | eq/heq = SHV (text 'SHV', raw None) |
|   값 | INFO | flt/lc = 0 (text '0', raw 0.0) |
|   값 | INFO | flt/lcf = 100.19999694824219 (text '100.2', raw 0.004014013800770044) |
|   값 | INFO | flt/lcs = 24 (text '24', raw None) |
|   값 | INFO | flt/hc = 0 (text '0', raw 0.0) |
|   값 | INFO | flt/hcf = 10020.0 (text '10k02', raw 0.500500500202179) |
|   값 | INFO | flt/hcs = 12 (text '12', raw None) |
| 파라미터 범위 질의 (',s ?') | PASS | 19/19개 해석됨 |
|   범위 | INFO | eq/1f: 20.0 .. 20000.0 Hz |
|   범위 | INFO | eq/1g: -15.0 .. 15.0 dB |
|   범위 | INFO | eq/1q: 0.44 .. 10.0  |
|   범위 | INFO | eq/2f: 20.0 .. 20000.0 Hz |
|   범위 | INFO | eq/2g: -15.0 .. 15.0 dB |
|   범위 | INFO | eq/2q: 0.44 .. 10.0  |
|   범위 | INFO | eq/3f: 20.0 .. 20000.0 Hz |
|   범위 | INFO | eq/3g: -15.0 .. 15.0 dB |
|   범위 | INFO | eq/3q: 0.44 .. 10.0  |
|   범위 | INFO | eq/4f: 20.0 .. 20000.0 Hz |
|   범위 | INFO | eq/4g: -15.0 .. 15.0 dB |
|   범위 | INFO | eq/4q: 0.44 .. 10.0  |
|   범위 | INFO | eq/hf: 20.0 .. 20000.0 Hz |
|   범위 | INFO | eq/hg: -15.0 .. 15.0 dB |
|   범위 | INFO | eq/hq: 0.44 .. 10.0  |
|   범위 | INFO | eq/lf: 20.0 .. 20000.0 Hz |
|   범위 | INFO | eq/lg: -15.0 .. 15.0 dB |
|   범위 | INFO | eq/lq: 0.44 .. 10.0  |
|   범위 | INFO | flt/lcf: 20.0 .. 20000.0 Hz |
| ch3 eq/1g 쓰기 0.0 → 0.5 | PASS | 적용 완료 및 재조회 확인; 재조회 0.5 (101 ms)  |
| ch3 eq/1g 원래 값 복구 | PASS | 적용 완료 및 재조회 확인; 재조회 0.0 |
