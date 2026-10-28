"""악기별 기본 EQ 분석 프로필.

이 값들은 실전 경험에서 흔히 쓰는 출발점(휴리스틱)이며, '고정 EQ 값'이 아니다. 추천 엔진은 실제 입력 분석
결과에서 문제가 확인될 때만 해당 영역을 조정하고, 문제가 없으면 아무것도 바꾸지 않는다.
모든 수치는 config/instrument_overrides.json 으로 덮어쓸 수 있다.

regions: 영역명 → (하한 Hz, 상한 Hz, 허용 초과 dB, 최대 감쇠 dB, Q)
  '허용 초과 dB' 는 스펙트럼 기울기 추세선 대비 해당 영역 평균이 이보다 더 높을 때만 문제로 본다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

GENERIC_REGIONS = {
    "boom": (60, 160, 4.0, 4.0, 1.2),
    "mud": (180, 500, 3.0, 4.0, 1.4),
    "boxy": (500, 1000, 3.5, 3.0, 1.6),
    "harsh": (2000, 5000, 3.5, 3.0, 2.0),
}

REGION_LABELS = {
    "boom": "저역 울림(부밍)", "mud": "저중역 혼탁함", "boxy": "중역 답답함(박스톤)",
    "harsh": "고역의 거친 성분", "sibilance": "치찰음/과도한 고역", "rumble": "저주파 잡음",
    "presence": "존재감/명료도 부족", "resonance": "지속 공진", "feedback": "하울링 의심",
    "click": "어택/클릭 과다", "ring": "링(공명) 과다",
}


@dataclass
class InstrumentProfile:
    key: str
    label: str
    fund: tuple[float, float]               # 기본음 대략 범위 (보호 영역 판단용)
    hpf: float | None                       # 권장 HPF 시작점 (None 이면 HPF 추천 안 함)
    regions: dict = field(default_factory=dict)
    presence: tuple | None = None           # (하한, 상한, 부족 판단 dB, 최대 부스트 dB, Q)
    analysis_range: tuple[float, float] = (60.0, 12000.0)
    resonance_threshold_db: float = 3.5
    resonance_max_cut_db: float = 6.0
    notes: list[str] = field(default_factory=list)   # 청음 확인이 필요한 항목


def _r(*names, **over):
    d = {n: GENERIC_REGIONS[n] for n in names}
    d.update(over)
    return d


PROFILES: dict[str, InstrumentProfile] = {p.key: p for p in [
    InstrumentProfile("vocal_male", "남성 보컬", (85, 400), 90,
                      _r("mud", "boxy", "harsh", sibilance=(5000, 9000, 4.0, 3.0, 3.0)),
                      presence=(2000, 5000, -5.0, 2.0, 1.2), analysis_range=(80, 12000),
                      notes=["근접 효과(마이크 거리)에 따라 저역이 크게 달라짐", "치찰음은 정적 EQ 보다 디에서가 적합할 수 있음"]),
    InstrumentProfile("vocal_female", "여성 보컬", (160, 1100), 120,
                      _r("mud", "boxy", "harsh", sibilance=(5500, 10000, 4.0, 3.0, 3.0)),
                      presence=(2500, 6000, -5.0, 2.0, 1.2), analysis_range=(120, 14000),
                      notes=["치찰음은 정적 EQ 보다 디에서가 적합할 수 있음"]),
    InstrumentProfile("chorus", "코러스/합창", (100, 1100), 110, _r("mud", "boxy", "harsh"),
                      analysis_range=(100, 12000), notes=["여러 성부가 섞여 개별 공진 판단 신뢰도가 낮음"]),
    InstrumentProfile("acoustic_guitar", "어쿠스틱 기타", (80, 1200), 90,
                      _r("boom", "mud", "harsh", boom=(80, 220, 4.0, 5.0, 1.4)),
                      notes=["바디 공진은 픽업/마이크 위치에 따라 다름", "피킹 어택(2~5kHz)은 과도하게 깎지 않음"]),
    InstrumentProfile("electric_guitar", "일렉트릭 기타", (80, 1200), 80, _r("boom", "mud", "harsh"),
                      notes=["앰프/캐비닛 톤은 연주자 의도일 수 있음 — 큰 변경은 청음 확인 필요"]),
    InstrumentProfile("bass_guitar", "베이스 기타", (40, 400), 30,
                      {"mud": (200, 450, 4.0, 3.0, 1.2), "boxy": (500, 900, 4.0, 3.0, 1.4)},
                      analysis_range=(35, 6000), resonance_max_cut_db=4.0,
                      notes=["킥 드럼과의 마스킹은 두 채널을 함께 들어 판단", "기본음 대역은 깎지 않음"]),
    InstrumentProfile("piano", "피아노/디지털 건반", (27, 4200), 50, _r("mud", "boxy", "harsh"),
                      analysis_range=(40, 14000), notes=["보컬·기타와 겹치는 중역은 믹스 맥락에서 판단"]),
    InstrumentProfile("synth_organ", "신시사이저/오르간", (30, 4000), 40, _r("mud", "harsh"),
                      analysis_range=(40, 14000), notes=["음색 프리셋에 따라 스펙트럼이 크게 다름"]),
    InstrumentProfile("kick", "킥 드럼", (40, 120), 30,
                      {"boxy": (250, 600, 4.0, 5.0, 1.4)}, analysis_range=(35, 10000),
                      notes=["어택(2~5kHz) 은 장르 취향 — 자동 조정 안 함"]),
    InstrumentProfile("snare", "스네어 드럼", (150, 300), 90,
                      {"boxy": (400, 900, 4.0, 4.0, 1.6), "ring": (600, 1500, 6.0, 4.0, 5.0)},
                      analysis_range=(80, 14000), notes=["하이햇 누음이 고역 분석에 섞임"]),
    InstrumentProfile("tom", "탐", (60, 300), 60, {"boxy": (300, 800, 4.0, 5.0, 1.4)},
                      analysis_range=(50, 10000), notes=["탐 링은 연주 사이 공명으로 판단 — 청음 확인"]),
    InstrumentProfile("overhead", "오버헤드/심벌", (200, 16000), 150,
                      {"mud": (200, 500, 3.0, 4.0, 1.2), "harsh": (2500, 6000, 4.0, 3.0, 2.0)},
                      analysis_range=(150, 16000), notes=["여러 드럼 누음 및 위상 문제 가능성 — 청음 확인"]),
    InstrumentProfile("drum_bus", "드럼 버스", (40, 16000), None, _r("mud", "boxy"),
                      analysis_range=(40, 14000), notes=["버스 EQ 는 개별 채널 EQ 이후 판단"]),
    InstrumentProfile("cello", "첼로", (65, 1000), 50, _r("mud", "harsh"),
                      analysis_range=(60, 12000), notes=["활 소리·배음은 음색의 일부 — 최소 조정"]),
    InstrumentProfile("violin", "바이올린", (196, 3500), 150,
                      _r("boxy", harsh=(2500, 6000, 4.0, 3.0, 2.0)), analysis_range=(180, 14000),
                      notes=["활 소리·배음은 음색의 일부 — 최소 조정"]),
    InstrumentProfile("viola", "비올라", (130, 2000), 110, _r("mud", "boxy", "harsh"),
                      analysis_range=(120, 12000), notes=["활 소리·배음은 음색의 일부 — 최소 조정"]),
    InstrumentProfile("accordion", "아코디언", (80, 3500), 80, _r("mud", "boxy", "harsh"),
                      analysis_range=(80, 12000), notes=["리드 배음 구조가 촘촘해 공진 판단 신뢰도 낮음"]),
    InstrumentProfile("saxophone", "색소폰", (100, 900), 90, _r("mud", "boxy", "harsh"),
                      analysis_range=(90, 12000), notes=["연주 강도에 따라 고역 비율이 크게 변함"]),
    InstrumentProfile("brass", "트럼펫/금관", (150, 1000), 120,
                      _r("mud", harsh=(2000, 6000, 4.0, 3.0, 2.0)), analysis_range=(120, 12000)),
    InstrumentProfile("woodwind", "플루트/목관", (250, 2500), 150, _r("mud", "harsh"),
                      analysis_range=(200, 14000), notes=["숨소리는 음색의 일부"]),
    InstrumentProfile("custom", "사용자 지정", (60, 4000), None, _r("mud", "harsh")),
    InstrumentProfile("unknown", "미지정", (40, 4000), None, {}),
]}


def get_profile(key: str) -> InstrumentProfile:
    return PROFILES.get(key, PROFILES["unknown"])


def load_overrides(path: str | Path) -> None:
    p = Path(path)
    if not p.exists():
        return
    data = json.loads(p.read_text("utf-8"))
    for key, over in data.items():
        if key in PROFILES:
            if "regions" in over:
                over["regions"] = {k: tuple(v) for k, v in over["regions"].items()}
            PROFILES[key] = replace(PROFILES[key], **over)
