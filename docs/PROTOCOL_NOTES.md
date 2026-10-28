# WING 프로토콜 근거 기록

## 확인한 문서

| 문서 | 상태 |
|---|---|
| WING Remote Protocols **V3.0.6-30** (WING FW 3.0.6-27 이상), Music Tribe 공식 CDN (`cdn.mediavalet.com/.../WING Remote Protocols_V3.0.6-30.pdf`) | **본문 확인함.** 아래 사실은 모두 이 문서에서 나온 것입니다 |
| WING Remote Protocols **3.1-03** (사용자가 준 링크, empowertribe CDN) | 개발 환경에서 다운로드가 차단돼 **확인 못 함** |
| BE-P0BV2 WING OSC Documentation (사용자가 준 링크, mediadl.musictribe.com) | 접근이 차단돼 **확인 못 함** |
| WING COMPACT 제품 페이지 | 확인 못 함 |

→ 실제 장비의 펌웨어가 3.1 이상이면 3.1-03 문서와 차이가 있는지 사용자가 확인해야 합니다. 그래서 `tools/verify_connection.py`를 실장비에 실행해 실제 응답으로 확정합니다.

## 문서에서 확인한 사실 (V3.0.6-30)

| 항목 | 문서 내용 | 코드 위치 |
|---|---|---|
| OSC 포트 | "OSC uses a single UDP communication port: 2223" | `wing_addresses.OSC_PORT` |
| 네이티브/디스커버리 포트 | "ports 2223 [UDP], and 2222 [UDP, TCP]" | `NATIVE_PORT`, `DISCOVERY_PORT` |
| 디스커버리 | 5바이트 `WING?`를 2222로 보내면 `WING,ip,name,model,serial,firmware`로 응답 | `protocol_adapter.discover` |
| 정보 조회 | `/?` → `,s` `WING,192.168.1.71,PGM,ngc-full,NO_SERIAL,1.07...` | `WingOscAdapter.ping` |
| 값 조회 | 인자 없이 주소만 보냄. 예: `/ch/1/fdr` | `osc_codec.encode` |
| 조회 응답 | float는 `,sff` (ascii, raw 0..1, 실제값), int는 `,sfi`, enum과 문자열은 `,s` | `osc_client.WingValue` |
| 값 설정 | `,s` 문자열 / `,f` 값 (예: `/ch/2/fdr ,f -3.0`이면 raw 0.675와 -3.0 dB) / `,i` 정수 | `WingOscAdapter.write_param` |
| 에코 | "WING does not echo data sent over UDP" → 반드시 재조회 | `eq_controller` |
| 노드 질의 | `,s *` 덤프, `,s ?` 설명(범위), `,s #` 설명+값. 예: `fxmix lin [0 .. 100 %], 101 steps` | `parse_description` |
| 구독 | 한 번에 1개, 10초마다 유지 필요 | 사용 안 함 (폴링 방식) |
| /ch/1 덤프의 하위 키 | `.flt lc,lcf,lcs,hc,hcf,hcs,tf,mdl,tilt` / `.eq on,mdl=STD,mix,lg,lf,lq,leq,1g..4g,1f..4f,1q..4q,hg,hf,hq,heq` / `name` | `EQ_KEYS`, `FILTER_KEYS` |
| 숫자 표기 | `1k50`=1500, `12k00`=12000, `-oo` | `parse_wing_number` |
| 입력 수 | "48-channel inputs [in/aux]" | `DEFAULT_MAX_CH=40` (장비 질의로 확인) |

## 문서에서 직접 확인하지 못한 것 (추론 또는 미확인)

* **EQ 하위 경로의 정확한 형태** (`/ch/N/eq/1g` 등): 덤프 구조와 `/ch/1/fdr` 예시의 규칙을 따라 이어 붙인 것입니다. 문서에 직접 예시로 나오지는 않습니다. **→ 실장비 검증 필수.**
* **채널 EQ의 최소·최대 범위**: 문서에 없습니다. 프로그램은 장비에 `,s ?`로 물어서 얻은 범위만 사용하고, 범위를 모르면 쓰기를 거부합니다. 설정에서 보수적인 기본 범위를 켤 수는 있지만, 이 값은 공식 값이 아닙니다.
* **leaf 주소에 `,s ?` 응답이 오는지와 그 형식**: 문서 예시는 노드(`/fx/1`)뿐입니다. 해석에 실패하면 범위를 모르는 것으로 처리합니다.
* **`,f`로 보낸 Hz, Q 값을 실제 단위로 해석하는지**: fdr 예시(dB)로 미루어 볼 때 실제 단위로 추정합니다. → 쓰기 테스트의 재조회로 확인합니다.
* **WING COMPACT의 /ch 개수**: 문서에는 콘솔 기준 입력 48개(in/aux)만 나옵니다. 프로그램이 `/ch/N/name` 응답으로 확인합니다.
* **EQ 모델이 STD가 아닐 때의 파라미터 구조**: 다를 수 있으므로, STD가 아니면 적용 대상에서 제외합니다.
* **네이티브 TCP 프로토콜(바이너리 토큰)**: 문서의 해당 절을 확보하지 못해 구현하지 않았습니다.

## 지어내지 않은 것

프로그램은 위 표에 없는 주소를 쓰지 않습니다. 쓰기 허용 키는 `eq/on`, `eq/{1-4}{g,f,q}`, `eq/{l,h}{g,f,q}`, `flt/lc`, `flt/lcf`뿐입니다 (`WRITABLE_KEYS`). 프리앰프 게인, 팬텀, 페이더, 뮤트, 라우팅, 센드는 경로 문자열 단계에서 차단합니다.

## 실장비에서 확인한 사실 (WING COMPACT, FW 3.1.2-0-g53fb5e0d, 2026-10-28)

* UDP 2222 디스커버리 `WING?` → `WING,192.168.0.150,B4ND-WING,wing-compact,<serial>,3.1.2-0-g53fb5e0d:release` (문서와 같은 형식).
* UDP 2223 OSC는 WING에서 OSC를 켠 뒤 응답했다.
* `/?` 요청의 응답 주소는 `/?`가 아니라 **`/*`** 이다 (`,s` + 정보 문자열). 타입 태그 `,`의 유무와 관계없이 응답한다.
* `/ch/1/name` → `,s ""` (이름이 비어 있는 채널).
