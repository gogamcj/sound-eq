"""WING OSC 주소 및 값 형식.

근거: Behringer "WING Remote Protocols V3.0.6-30" (WING FW 3.0.6-27 이상).
  * OSC 포트: UDP 2223 / 네이티브·디스커버리: UDP·TCP 2222
  * 조회: 인자 없는 주소 전송 → float 은 ',sff' (ascii, raw 0..1, 실제값), int 는 ',sfi', enum/문자열은 ',s'
  * 설정: ',s' 문자열 / ',f' 실제값 (예: /ch/2/fdr ,f -3.0 → dB) / ',i' 정수
  * 노드 인자: ',s *' 덤프, ',s ?' 범위 설명, ',s #' 설명+값
  * WING 은 UDP 로 받은 설정을 에코하지 않는다 → 반드시 재조회로 확인
  * /ch/1 노드 덤프에 다음 하위 키가 나타난다:
      .flt  lc, lcf, lcs, hc, hcf, hcs, tf, mdl, tilt
      .eq   on, mdl(=STD), mix, lg, lf, lq, leq, 1g..4g, 1f..4f, 1q..4q, hg, hf, hq, heq
      name, col, icon, mute, fdr ...
아래 경로는 위 덤프 구조에서 '/' 로 이어 붙여 얻은 것이다. 문서의 예시 경로(/ch/1/fdr, /ch/1/mute)와
같은 규칙이지만 EQ 하위 경로 자체가 문서에 직접 예시로 나오지는 않으므로, 실제 장비에서
tools/verify_connection.py 로 반드시 확인한다 (HARDWARE_VERIFICATION.md 참고).

범위(최소/최대)는 문서에 채널 EQ 에 대해 나와 있지 않다. 따라서 이 프로그램은 범위를 하드코딩하지 않고
장비에 ',s ?' 로 질의해 얻은 범위로만 쓰기 검증을 한다 (parameter_validation.py).
"""
from __future__ import annotations

import math
import re

OSC_PORT = 2223          # 문서: "OSC uses a single UDP communication port: 2223"
NATIVE_PORT = 2222       # 문서: "ports 2223 [UDP], and 2222 [UDP, TCP]"
DISCOVERY_PORT = 2222    # 문서: 'WING?' 5바이트 UDP 데이터그램을 2222 로
DISCOVERY_MESSAGE = b"WING?"
INFO_ADDRESS = "/?"      # 문서: "/?" → ",s" "WING,ip,name,model,serial,fw"
# 실장비 확인 (WING COMPACT FW 3.1.2, 2026-10-28): "/?" 의 응답은 주소 "/*" 로 온다.
REPLY_ADDRESS = {INFO_ADDRESS: "/*"}

# WING 콘솔은 입력 48개(ch + aux). 일반적으로 ch 40 + aux 8 로 알려져 있으나
# 문서 본문에서 ch 개수를 직접 확인하지 못했으므로 장비 질의로 확인한다.
DEFAULT_MAX_CH = 40

PEQ_BANDS = (1, 2, 3, 4)  # .eq 의 1..4 파라메트릭 + 로우/하이 셸프 (문서 소개: 6-band parametric EQ)


def ch_base(ch: int) -> str:
    if ch < 1:
        raise ValueError("채널 번호는 1부터 시작")
    return f"/ch/{ch}"


def ch_param(ch: int, key: str) -> str:
    """key 예: 'name', 'eq/on', 'eq/1g', 'flt/lcf'"""
    return f"{ch_base(ch)}/{key}"


EQ_KEYS = (
    ["eq/on", "eq/mdl", "eq/lg", "eq/lf", "eq/lq", "eq/leq"]
    + [f"eq/{b}{p}" for b in PEQ_BANDS for p in ("g", "f", "q")]
    + ["eq/hg", "eq/hf", "eq/hq", "eq/heq"]
)
FILTER_KEYS = ["flt/lc", "flt/lcf", "flt/lcs", "flt/hc", "flt/hcf", "flt/hcs"]

# 이 프로그램이 쓰기를 고려하는 파라미터 (프리앰프 게인·팬텀·라우팅·출력 레벨은 절대 포함하지 않는다)
WRITABLE_KEYS = frozenset(
    ["eq/on", "flt/lc", "flt/lcf"]
    + [f"eq/{b}{p}" for b in PEQ_BANDS for p in ("g", "f", "q")]
    + ["eq/lg", "eq/lf", "eq/lq", "eq/hg", "eq/hf", "eq/hq"]
)
FORBIDDEN_SUBSTRINGS = ("/in/set/trim", "/in/set/", "phantom", "/fdr", "/mute", "/conn", "/main", "/send")

BOOL_KEYS = frozenset({"eq/on", "flt/lc", "flt/hc"})


def key_kind(key: str) -> str:
    """'gain' | 'freq' | 'q' | 'bool' | 'enum' | 'text'"""
    if key in BOOL_KEYS:
        return "bool"
    last = key.split("/")[-1]
    if last in ("mdl", "leq", "heq", "lcs", "hcs"):
        return "enum"
    if last == "name":
        return "text"
    if last.endswith("g"):
        return "gain"
    if last.endswith("f"):
        return "freq"
    if last.endswith("q"):
        return "q"
    return "text"


_K_RE = re.compile(r"^([+-]?\d+)k(\d*)$")


def parse_wing_number(text: str) -> float:
    """WING ASCII 숫자 표기 해석: '1k50'=1500, '12k00'=12000, '10k0'=10000, '-oo'=-inf, '100.2'."""
    t = text.strip()
    if t in ("-oo", "-inf"):
        return -math.inf
    if t in ("oo", "+oo", "inf"):
        return math.inf
    m = _K_RE.match(t)
    if m:
        whole, frac = m.group(1), m.group(2) or "0"
        return float(f"{whole}.{frac}") * 1000.0
    return float(t)


_DESC_NUM_RE = re.compile(
    r"\b(lin|log|linf|logf|int|faderf|fader)\b\s*\[\s*([^\]]+?)\s*\.\.\s*([^\]\s]+)\s*([^\]]*)\]"
)
_DESC_LIST_RE = re.compile(r"\blist\b\s*\[([^\]]*)\]")


def parse_description(text: str) -> dict | None:
    """',s ?' 설명 문자열에서 범위를 해석한다.

    문서 예시: "fxmix lin [0 .. 100 %], 101 steps", "$esrc int [0 .. 400]",
              "mdl list [NONE, EXT, HALL, ...]", '#' 형식은 'fxmix 100 lin [...]', 읽기전용은 'r/o'.
    """
    ro = " r/o " in f" {text} "
    m = _DESC_NUM_RE.search(text)
    if m:
        kind, lo, hi, unit = m.groups()
        try:
            lo_v, hi_v = parse_wing_number(lo), parse_wing_number(hi)
        except ValueError:
            return None
        steps = None
        sm = re.search(r"(\d+)\s*steps", text)
        if sm:
            steps = int(sm.group(1))
        return {"type": kind, "min": lo_v, "max": hi_v, "unit": unit.strip(), "steps": steps, "read_only": ro}
    m = _DESC_LIST_RE.search(text)
    if m:
        options = [o.strip() for o in m.group(1).split(",") if o.strip()]
        return {"type": "list", "options": options, "read_only": ro}
    return None


def parse_node_description(text: str) -> dict[str, dict]:
    """노드에 ',s ?' 를 보냈을 때 오는 여러 줄 설명을 키별로 해석한다.

    실장비 확인 (WING COMPACT FW 3.1.2, 2026-10-28): leaf 주소(/ch/1/eq/1g)는 ',s ?' 에 응답하지 않고,
    노드 주소(/ch/1/eq)는 한 줄에 하나씩 "  1g   lin [-15.0 .. +15.0 dB], 301 steps" 형식으로 응답한다.
    """
    out = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        d = parse_description(parts[1])
        if d:
            out[parts[0]] = d
    return out


def parse_info_reply(text: str) -> dict:
    """'WING,192.168.1.71,PGM,ngc-full,NO_SERIAL,1.07.2-...' → dict (문서의 '/?' 및 디스커버리 응답 형식)."""
    parts = text.split(",")
    if not parts or parts[0] != "WING":
        raise ValueError(f"WING 응답 형식이 아니다: {text!r}")
    keys = ["ip", "name", "model", "serial", "firmware"]
    return {k: (parts[i + 1] if i + 1 < len(parts) else "") for i, k in enumerate(keys)}
