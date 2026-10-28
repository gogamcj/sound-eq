"""악기별 EQ 추천 엔진 (설명 가능한 규칙 기반).

절차: 현재 믹서 EQ 확인 → 충분한 분석 데이터 확인 → 악기 프로필과 결합 → 문제 영역 추정 →
추천값 계산 → 현재값과 비교 → 제한 범위 적용(safety_controller) → 승인/자동 정책에 따라 적용.

분석 탭(analysis_tap):
  * 'pre_eq'  : USB 로 받는 신호가 채널 EQ 이전(예: 입력 소스 직접 라우팅)인 경우.
                현재 믹서 EQ 커브(근사)를 더해 '현재 EQ 를 거친 소리'를 추정한 뒤 판단한다.
  * 'post_eq' : USB 로 받는 신호가 채널 EQ 이후(다이렉트 아웃 탭 등)인 경우. 측정값 그대로 판단.
믹서 USB 라우팅에 따라 달라지므로 사용자가 설정한다 (docs/USB_ROUTING.md).

추천 원칙:
  * 확실한 문제가 없으면 아무것도 바꾸지 않는다.
  * 감쇠 위주. 부스트는 보컬 존재감 부족처럼 근거가 명확할 때 +2dB 이내, 항상 '청음 확인 필요'.
  * 기본음 근처의 큰 에너지(음정이 고정된 음)는 공진으로 보지 않는다.
  * 기존 EQ 밴드를 보존: 비어 있는(게인 0) 밴드나 같은 주파수 근처 밴드만 사용한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from audio.dsp_analyzer import ChannelSnapshot
from audio.spectrum import smooth_octave
from mixer import wing_addresses as wa
from mixer.models import ChannelEqState

from . import eq_curve
from .feedback_monitor import FeedbackSuspect
from .instrument_profiles import REGION_LABELS, InstrumentProfile


@dataclass
class EngineSettings:
    min_analysis_seconds: float = 8.0      # 이 이상 유효 신호가 모여야 추천
    full_confidence_seconds: float = 30.0
    analysis_tap: str = "pre_eq"
    allow_boost: bool = True
    max_boost_db: float = 2.0
    feedback_warn_score: float = 0.5
    feedback_notch_score: float = 0.8
    notch_q: float = 8.0
    notch_max_cut_db: float = 6.0
    max_notches: int = 2


@dataclass
class Finding:
    kind: str           # region 이름, 'resonance', 'rumble', 'presence', 'feedback'
    freq: float
    gain_db: float      # 원하는 변화량 (음수=감쇠)
    q: float
    excess_db: float
    strength: float     # 0..1
    reason: str


@dataclass
class ParamChange:
    key: str
    current: object
    recommended: object

    @property
    def changed(self) -> bool:
        from mixer.parameter_validation import values_equal
        return self.current is None or not values_equal(self.key, self.current, self.recommended)


@dataclass
class BandRecommendation:
    target: str                     # 'eq/2' (밴드), 'flt/lc' (HPF)
    kind: str
    changes: list[ParamChange]
    reason: str
    confidence: float
    evidence_sufficient: bool
    listen_check: bool
    is_boost: bool = False
    is_notch: bool = False
    excess_db: float = 0.0
    freq: float = 0.0

    def as_dict(self) -> dict:
        return {c.key: c.recommended for c in self.changes if c.changed}

    def current_dict(self) -> dict:
        return {c.key: c.current for c in self.changes}


@dataclass
class Recommendation:
    ch_index: int
    mixer_ch: int | None
    status: str                         # 'ok' | 'no_issue' | 'insufficient' | 'silent' | 'clipping' | 'unsupported'
    message: str
    items: list[BandRecommendation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    listen_notes: list[str] = field(default_factory=list)
    current_curve: np.ndarray | None = None
    expected_curve: np.ndarray | None = None
    freqs: np.ndarray | None = None
    based_on_eq: dict = field(default_factory=dict)   # 추천 계산 당시 읽은 현재 EQ 값


def _mean_in(x, centers, lo, hi):
    m = (centers >= lo) & (centers < hi)
    return float(np.mean(x[m])) if m.any() else 0.0


def _tilt_residual(spec_db: np.ndarray, centers: np.ndarray, rng: tuple[float, float]) -> np.ndarray:
    """분석 범위에서 dB-log2(f) 직선(전체 기울기)을 빼서 상대적 과잉/부족을 본다."""
    m = (centers >= rng[0]) & (centers <= rng[1])
    # 잡음 바닥(최대 대비 -45dB 이하)은 추세 계산에서 제외
    m &= spec_db > (np.max(spec_db[m]) - 45.0)
    lx = np.log2(centers)
    # 2차 곡선(로그 주파수)으로 자연스러운 스펙트럼 외형을 근사하고, 튀는 영역(>4dB)을 빼고 한 번 더 맞춘다
    coef = np.polyfit(lx[m], spec_db[m], 2)
    r = spec_db - np.polyval(coef, lx)
    m2 = m & (np.abs(r) < 4.0)
    if m2.sum() > 10:
        coef = np.polyfit(lx[m2], spec_db[m2], 2)
    return spec_db - np.polyval(coef, lx)


def _smooth_power_db(spec_db: np.ndarray, width_oct: float) -> np.ndarray:
    """파워 영역에서 평활 (배음 사이 골짜기가 dB 평균을 끌어내리는 것을 방지)."""
    p = 10 ** (spec_db / 10)
    sm = smooth_octave(p[:, None], 24, width_oct)[:, 0]
    return 10 * np.log10(np.maximum(sm, 1e-16))


class RecommendationEngine:
    def __init__(self, settings: EngineSettings | None = None):
        self.s = settings or EngineSettings()

    # ------------------------------------------------------------------
    def analyze(self, snap: ChannelSnapshot, prof: InstrumentProfile, eq: ChannelEqState | None,
                feedback: list[FeedbackSuspect] | None = None, instrument_confirmed: bool = False,
                mixer_ch: int | None = None) -> Recommendation:
        s = self.s
        rec = Recommendation(snap.index, mixer_ch, "ok", "", freqs=snap.centers)
        rec.listen_notes = list(prof.notes)
        cur_vals = {k: pv.value for k, pv in (eq.values.items() if eq else [])}
        rec.based_on_eq = cur_vals
        cur_curve = eq_curve.response_db(cur_vals, snap.centers) if cur_vals else np.zeros_like(snap.centers)
        rec.current_curve = cur_curve

        for fs in feedback or []:
            if fs.score >= s.feedback_warn_score:
                rec.warnings.append(f"하울링 의심 {fs.freq:.0f}Hz (의심도 {fs.score:.2f}, {fs.duration_s:.1f}s 지속, "
                                    f"{'배음 가능성' if fs.harmonic_of_f0 else '배음 아님'})")
        if snap.silent:
            return self._done(rec, "silent", "무음 — 추천하지 않음")
        if snap.clipping:
            return self._done(rec, "clipping", "입력 클리핑 감지 — 게인 구조를 먼저 확인하세요. 클리핑 중 스펙트럼은 "
                                               "신뢰할 수 없어 추천하지 않습니다 (프로그램은 프리앰프 게인을 바꾸지 않음)")
        if snap.low_signal or snap.active_seconds < s.min_analysis_seconds:
            return self._done(rec, "insufficient",
                              f"분석 데이터 부족 (유효 신호 {snap.active_seconds:.1f}s / 필요 {s.min_analysis_seconds:.0f}s)")
        if eq is None or not eq.values:
            return self._done(rec, "unsupported", "현재 믹서 EQ 를 읽지 못해 추천을 계산하지 않음 (덮어쓰기 방지)")

        spec = snap.long_db + (cur_curve if s.analysis_tap == "pre_eq" else 0.0)
        fb_findings = self._feedback_findings(feedback or [])
        findings = self._findings(snap, prof, spec, cur_vals, [f.freq for f in fb_findings]) + fb_findings

        data_factor = min(1.0, snap.active_seconds / s.full_confidence_seconds)
        inst_factor = 1.0 if instrument_confirmed else (0.6 if prof.key not in ("unknown",) else 0.4)
        model_ok = (eq.eq_model in (None, "STD"))
        if not model_ok:
            rec.warnings.append(f"EQ 모델이 {eq.eq_model} — 밴드 구조가 STD 와 다를 수 있어 적용 대상에서 제외")

        items = self._assign(findings, cur_vals, prof, data_factor, inst_factor)
        rec.items = [i for i in items if any(c.changed for c in i.changes)]
        if not model_ok:
            for i in rec.items:
                i.evidence_sufficient = False
        exp_vals = dict(cur_vals)
        for it in rec.items:
            exp_vals.update(it.as_dict())
        rec.expected_curve = eq_curve.response_db(exp_vals, snap.centers)
        if not rec.items:
            return self._done(rec, "no_issue", "확실한 음향 문제를 찾지 못함 — EQ 변경 권장 없음")
        rec.message = f"추천 {len(rec.items)}건"
        return rec

    @staticmethod
    def _done(rec, status, msg):
        rec.status, rec.message = status, msg
        rec.expected_curve = rec.current_curve
        return rec

    # ------------------------------------------------------------------
    def _findings(self, snap, prof, spec, cur, avoid: list[float] | None = None) -> list[Finding]:
        s, c = self.s, snap.centers
        out: list[Finding] = []
        spec_s = _smooth_power_db(spec, 1 / 3)
        resid = _tilt_residual(spec_s, c, prof.analysis_range)

        # 1) 저주파 잡음 → HPF
        if prof.hpf:
            lc_on = int(float(cur.get("flt/lc", 0) or 0))
            lcf = float(cur.get("flt/lcf", 0) or 0)
            m_rng = (c >= prof.analysis_range[0]) & (c <= prof.analysis_range[1])
            below = _mean_in(spec_s, c, 20, prof.hpf * 0.8)
            main = float(np.max(spec_s[m_rng]))        # 신호의 주 에너지 대역 레벨
            plays_low = snap.f0_median is not None and snap.f0_median < prof.hpf * 1.2
            excess = below - main + 20.0               # 주 대역보다 20dB 이상 작지 않으면 문제로 봄
            if excess > 0 and not plays_low and (not lc_on or lcf < prof.hpf * 0.8):
                out.append(Finding("rumble", prof.hpf, 0, 0, excess, min(1, excess / 8),
                                   f"{prof.hpf * 0.8:.0f}Hz 이하 에너지가 주 대역 대비 {below - main:.1f}dB "
                                   f"(허용 -20dB) — 저주파 잡음/근접 효과"))

        # 2) 영역별 과잉 (프로필 영역만)
        for name, (lo, hi, thr, max_cut, q) in prof.regions.items():
            m = (c >= lo) & (c < hi)
            if not m.any():
                continue
            ex = float(np.mean(resid[m]))
            note = ""
            if snap.f0_median and lo <= snap.f0_median <= hi:
                thr += 3.0   # 기본음이 이 영역에 있으면 큰 에너지가 정상일 수 있어 기준을 높인다
                note = " — 기본음 대역과 겹침(기준 +3dB 상향)"
            if ex > thr:
                f_peak = float(c[m][np.argmax(resid[m])])
                if any(abs(math.log2(f_peak / u)) < 1 / 3 for u in (avoid or [])):
                    continue  # 하울링 의심 주파수는 노치로 따로 다룬다
                g = -min(max_cut, max(1.0, (ex - thr) * 0.7 + 1.0))
                out.append(Finding(name, f_peak, round(g, 1), q, ex, min(1, (ex - thr) / thr + 0.3),
                                   f"{REGION_LABELS.get(name, name)}: {lo}-{hi}Hz 평균이 추세 대비 +{ex:.1f}dB "
                                   f"(허용 {thr:.1f}dB), 최대점 {f_peak:.0f}Hz{note}"))

        # 3) 지속 공진: 장기 평균 스펙트럼의 좁은 돌출 (1/6 옥타브 평활 vs 1 옥타브 평활, 파워 영역)
        #    장기 평균은 여러 음을 거치며 쌓이므로, 음정이 바뀌어도 남아 있는 돌출은 고정 공진일 가능성이 높다.
        narrow = _smooth_power_db(spec, 1 / 6) - _smooth_power_db(spec, 1.0)
        lo_r, hi_r = prof.analysis_range
        spread = snap.f0_spread_semitones
        stable_pitch = spread is not None and spread < 2.0
        varied_pitch = spread is not None and spread >= 3.0
        cand = np.where((narrow > prof.resonance_threshold_db) & (c >= lo_r) & (c <= hi_r)
                        & ((snap.persistence > 0.5) | varied_pitch))[0]
        used: list[float] = []
        for b in cand[np.argsort(-narrow[cand])]:
            f = float(c[b])
            if any(abs(math.log2(f / u)) < 1 / 3 for u in used + list(avoid or [])):
                continue  # 이미 다룬 공진 또는 하울링 의심 주파수
            if stable_pitch and snap.f0_median:
                k = round(f / snap.f0_median)
                if 1 <= k <= 6 and abs(12 * math.log2(f / (k * snap.f0_median))) < 1.0:
                    continue   # 고정 음정의 기본음/배음 — 공진으로 보지 않음
            ex = float(narrow[b])
            # 대역폭 추정 → Q
            half = ex / 2
            lo_b = b
            while lo_b > 0 and narrow[lo_b - 1] > half:
                lo_b -= 1
            hi_b = b
            while hi_b < len(c) - 1 and narrow[hi_b + 1] > half:
                hi_b += 1
            bw_oct = max((hi_b - lo_b + 1) / 24.0, 1 / 12)
            q = float(np.clip(1.0 / bw_oct, 2.0, 8.0))
            used.append(f)
            why = "음정 변화와 무관하게 유지" if varied_pitch else f"지속성 {snap.persistence[b] * 100:.0f}%"
            out.append(Finding("resonance", f, -round(min(prof.resonance_max_cut_db, ex), 1), round(q, 1), ex,
                               min(1, (ex - prof.resonance_threshold_db) / 2 + 0.4),
                               f"{f:.0f}Hz 지속 공진 후보: 장기 평균에서 주변 1옥타브 대비 +{ex:.1f}dB ({why})"))
            if len(used) >= 2:
                break

        # 4) 존재감 부족 (보컬 등, 부스트)
        if prof.presence and self.s.allow_boost:
            lo, hi, thr, max_boost, q = prof.presence
            ex = _mean_in(resid, c, lo, hi)
            if ex < thr:
                g = min(max_boost, self.s.max_boost_db, (thr - ex) * 0.5 + 0.5)
                out.append(Finding("presence", math.sqrt(lo * hi), round(g, 1), q, ex, min(1, (thr - ex) / 6),
                                   f"{REGION_LABELS['presence']}: {lo}-{hi}Hz 가 추세 대비 {ex:.1f}dB (기준 {thr}dB)"))
        return out

    def _feedback_findings(self, fb: list[FeedbackSuspect]) -> list[Finding]:
        out = []
        for f in fb:
            if f.score >= self.s.feedback_notch_score and not f.harmonic_of_f0:
                g = -min(self.s.notch_max_cut_db, 3.0 + 3.0 * (f.score - self.s.feedback_notch_score) / 0.2)
                out.append(Finding("feedback", f.freq, round(g, 1), self.s.notch_q, f.prominence_db, f.score,
                                   f"하울링 의심 {f.freq:.0f}Hz: 의심도 {f.score:.2f}, {f.duration_s:.1f}s 지속, "
                                   f"돌출 {f.prominence_db:.0f}dB, 배음 관계 없음"))
            if len([o for o in out if o.kind == "feedback"]) >= self.s.max_notches:
                break
        return out

    # ------------------------------------------------------------------
    def _assign(self, findings: list[Finding], cur: dict, prof, data_factor, inst_factor) -> list[BandRecommendation]:
        out: list[BandRecommendation] = []
        free = []
        for b in wa.PEQ_BANDS:
            g = cur.get(f"eq/{b}g")
            if g is None:
                continue
            free.append(b)
        taken: set[int] = set()
        eq_on = int(float(cur.get("eq/on", 0) or 0))
        for fd in sorted(findings, key=lambda x: (x.kind != "feedback", -x.strength)):
            conf = round(max(0.0, min(1.0, fd.strength)) * data_factor * inst_factor, 2)
            sufficient = conf >= 0.5
            if fd.kind == "rumble":
                ch = [ParamChange("flt/lc", cur.get("flt/lc"), 1),
                      ParamChange("flt/lcf", cur.get("flt/lcf"), float(fd.freq))]
                out.append(BandRecommendation("flt/lc", fd.kind, ch, fd.reason, conf, sufficient, False,
                                              excess_db=fd.excess_db, freq=fd.freq))
                continue
            # 밴드 선택: 같은 주파수(1/3 옥타브) 근처에서 이미 쓰는 밴드 → 그 밴드 조정, 아니면 게인 0 인 밴드
            best = None
            for b in free:
                if b in taken:
                    continue
                f = float(cur.get(f"eq/{b}f") or 0)
                g = float(cur.get(f"eq/{b}g") or 0)
                if f > 0 and abs(g) > 0.05 and abs(math.log2(fd.freq / f)) < 1 / 3:
                    best = b
                    break
            if best is None:
                zero = [b for b in free if b not in taken and abs(float(cur.get(f"eq/{b}g") or 0)) <= 0.05]
                if zero:
                    best = min(zero, key=lambda b: abs(math.log2(fd.freq / max(float(cur.get(f"eq/{b}f") or 1000), 1))))
            if best is None:
                continue  # 빈 밴드 없음 — 기존 EQ 보존
            taken.add(best)
            g0 = float(cur.get(f"eq/{best}g") or 0)
            if abs(g0) > 0.05:
                # 이미 사용 중인 밴드: 주파수/Q 는 그대로 두고 게인만 추가 조정 (기존 EQ 보존)
                changes = [ParamChange(f"eq/{best}g", cur.get(f"eq/{best}g"), round(g0 + fd.gain_db, 1))]
            else:
                changes = [ParamChange(f"eq/{best}g", cur.get(f"eq/{best}g"), fd.gain_db),
                           ParamChange(f"eq/{best}f", cur.get(f"eq/{best}f"), round(fd.freq, 1)),
                           ParamChange(f"eq/{best}q", cur.get(f"eq/{best}q"), round(fd.q, 2))]
            if not eq_on:
                changes.insert(0, ParamChange("eq/on", cur.get("eq/on"), 1))
            boost = fd.gain_db > 0
            listen = boost or fd.kind in ("presence", "harsh", "sibilance") or not sufficient or "기본음" in fd.reason
            out.append(BandRecommendation(f"eq/{best}", fd.kind, changes, fd.reason, conf, sufficient, listen,
                                          is_boost=boost, is_notch=fd.kind == "feedback",
                                          excess_db=fd.excess_db, freq=fd.freq))
        return out
