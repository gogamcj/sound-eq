"""안전 제어: 운영 모드, 승인, 변경량/빈도/누적 제한, 자동 보정 중지 조건.

모든 믹서 쓰기는 이 모듈의 허가를 거친다.
 * 모드 A(분석 전용, 기본값): 어떤 쓰기도 허가하지 않는다.
 * 모드 B(추천+승인): 사용자가 적용 버튼을 누른 요청만 허가한다.
 * 모드 C(제한적 자동): 사용자가 명시적으로 자동 보정을 켜야 하고, 채널별 허용·신뢰도·신호·통신·클리핑·
   하울링 조건을 모두 통과한 경우에만, 제한된 양으로 허가한다.
전체 정지(emergency_stop)는 즉시 자동 보정을 끄며, 사용자가 다시 켜기 전까지 자동 적용은 없다.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from common.states import OperatingMode
from mixer import wing_addresses as wa

log = logging.getLogger(__name__)


@dataclass
class SafetySettings:
    max_step_db: float = 1.5               # 자동 1회 최대 게인 변화
    max_cumulative_db: float = 6.0         # 프로그램 시작 기준 밴드별 누적 변화 한도
    min_interval_s: float = 20.0           # 채널별 자동 적용 최소 간격
    max_bands_per_step: int = 1            # 자동 1회에 바꾸는 밴드 수
    auto_min_confidence: float = 0.6
    require_confirmed_instrument: bool = True
    auto_allow_boost: bool = False         # 자동 모드에서 부스트 금지 (기본)
    auto_allow_notch: bool = False         # 자동 노치 (별도 선택 기능)
    auto_notch_min_score: float = 0.85
    max_notches_per_channel: int = 2
    max_notch_cut_db: float = 6.0
    worsen_threshold_db: float = 1.0       # 변경 후 해당 문제 지표가 이만큼 나빠지면 채널 정지
    stop_on_feedback_score: float = 0.8    # 하울링 의심이 이 이상이면 (노치가 아니면) 자동 중지
    max_auto_freq_shift_oct: float = 0.5   # 이미 쓰는 밴드의 주파수는 자동으로 옮기지 않는다 (참고값)


@dataclass
class Decision:
    allowed: bool
    reason: str
    changes: dict = field(default_factory=dict)


class SafetyController:
    def __init__(self, settings: SafetySettings | None = None):
        self.s = settings or SafetySettings()
        self.mode = OperatingMode.ANALYSIS_ONLY
        self.auto_enabled = False
        self.stop_reason = ""
        self.link_ok = False
        self.audio_ok = False
        self.baseline: dict[int, dict[str, object]] = {}   # 프로그램 최초 변경 전 값
        self.last_auto: dict[int, float] = {}
        self.frozen: dict[int, str] = {}
        self.notch_count: dict[int, int] = {}
        self.listeners = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 상태
    def _notify(self):
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                log.exception("safety listener")

    def set_mode(self, mode: OperatingMode) -> None:
        with self._lock:
            self.mode = mode
            if mode != OperatingMode.AUTO_LIMITED and self.auto_enabled:
                self.auto_enabled = False
                self.stop_reason = "운영 모드 변경"
        self._notify()

    def enable_auto(self) -> Decision:
        with self._lock:
            if self.mode != OperatingMode.AUTO_LIMITED:
                return Decision(False, "자동 보정은 모드 C 에서만 켤 수 있다")
            if not self.link_ok:
                return Decision(False, "LAN 제어 연결이 없다")
            if not self.audio_ok:
                return Decision(False, "오디오 수신이 정상이 아니다")
            self.auto_enabled = True
            self.stop_reason = ""
        self._notify()
        return Decision(True, "자동 보정 시작")

    def emergency_stop(self, reason: str = "사용자 전체 정지") -> None:
        with self._lock:
            was = self.auto_enabled
            self.auto_enabled = False
            if was or reason.startswith("사용자"):
                self.stop_reason = reason   # 켜져 있지 않던 자동 보정은 '중지 사유'를 남기지 않는다
        if was:
            log.warning("자동 보정 중지: %s", reason)
        self._notify()

    def on_link(self, ok: bool) -> None:
        self.link_ok = ok
        if not ok:
            self.emergency_stop("LAN 통신 단절")

    def on_audio(self, ok: bool) -> None:
        self.audio_ok = ok
        if not ok:
            self.emergency_stop("오디오 입력 오류")

    def freeze_channel(self, ch: int, reason: str) -> None:
        with self._lock:
            self.frozen[ch] = reason
        log.warning("ch%d 자동 보정 정지: %s", ch, reason)
        self._notify()

    def unfreeze_channel(self, ch: int) -> None:
        with self._lock:
            self.frozen.pop(ch, None)
        self._notify()

    # ------------------------------------------------------------ 사용자 적용
    def check_user_apply(self, changes: dict) -> Decision:
        if self.mode == OperatingMode.ANALYSIS_ONLY:
            return Decision(False, "분석 전용 모드(A)에서는 믹서 설정을 바꾸지 않는다")
        if not self.link_ok:
            return Decision(False, "LAN 제어 연결이 없어 적용할 수 없다")
        bad = [k for k in changes if k not in wa.WRITABLE_KEYS]
        if bad:
            return Decision(False, f"허용되지 않은 파라미터: {bad}")
        return Decision(True, "사용자 승인", dict(changes))

    # ------------------------------------------------------------ 자동 적용
    def check_auto(self, ch: int, cfg, snap, item, feedback_suspects, current: dict) -> Decision:
        """cfg: ChannelConfig, snap: ChannelSnapshot, item: BandRecommendation, current: 현재 믹서 값."""
        s = self.s
        with self._lock:
            if self.mode != OperatingMode.AUTO_LIMITED or not self.auto_enabled:
                return Decision(False, "자동 보정이 꺼져 있다")
            if not self.link_ok:
                return Decision(False, "LAN 통신 단절")
            if not self.audio_ok:
                return Decision(False, "오디오 입력 오류")
            if ch in self.frozen:
                return Decision(False, f"채널 정지 상태: {self.frozen[ch]}")
            if not cfg.auto_allowed:
                return Decision(False, "이 채널은 자동 보정 허용 대상이 아니다")
            if cfg.mixer_ch is None:
                return Decision(False, "USB↔믹서 채널 매핑이 없다")
            if s.require_confirmed_instrument and not cfg.instrument_confirmed:
                return Decision(False, "악기 유형이 사용자 확정되지 않았다 (자동 인식만으로는 적용 안 함)")
            if snap.silent or snap.low_signal:
                return Decision(False, "신호 부족/무음")
            if snap.clipping:
                return Decision(False, "클리핑 감지")
            hot = [f for f in feedback_suspects if f.score >= s.stop_on_feedback_score]
            if hot and not (item.is_notch and s.auto_allow_notch):
                return Decision(False, f"하울링 의심 {hot[0].freq:.0f}Hz — 자동 EQ 보류")
            if item.confidence < s.auto_min_confidence or not item.evidence_sufficient:
                return Decision(False, f"추천 신뢰도 부족 ({item.confidence:.2f} < {s.auto_min_confidence})")
            if item.is_boost and not s.auto_allow_boost:
                return Decision(False, "자동 모드 부스트 금지")
            if item.is_notch:
                if not s.auto_allow_notch:
                    return Decision(False, "자동 노치 비활성 (승인 후 적용 필요)")
                if not any(f.score >= s.auto_notch_min_score and not f.harmonic_of_f0 for f in feedback_suspects):
                    return Decision(False, "하울링 판단 신뢰도 낮음 — 경고만 표시")
                if self.notch_count.get(ch, 0) >= s.max_notches_per_channel:
                    return Decision(False, "채널당 노치 개수 한도")
            if item.kind == "rumble":
                return Decision(False, "HPF 변경은 자동으로 하지 않음 (승인 필요)")
            last = self.last_auto.get(ch, 0.0)
            if time.monotonic() - last < s.min_interval_s:
                return Decision(False, f"조정 간격 제한 ({s.min_interval_s:.0f}s)")
            changes = self._limit(ch, item, current)
            if not changes:
                return Decision(False, "제한 적용 후 변경할 값이 없다 (누적 한도 도달 등)")
            return Decision(True, "자동 보정 조건 통과", changes)

    def _limit(self, ch: int, item, current: dict) -> dict:
        s = self.s
        base = self.baseline.get(ch, {})
        out = {}
        for c in item.changes:
            if not c.changed:
                continue
            k = c.key
            kind = wa.key_kind(k)
            if kind == "gain":
                cur = float(current.get(k, c.current) or 0.0)
                b0 = float(base.get(k, cur))
                target = float(c.recommended)
                max_cut = s.max_notch_cut_db if item.is_notch else s.max_cumulative_db
                step = max(-s.max_step_db, min(s.max_step_db, target - cur))
                new = cur + step
                # 누적 한도 (프로그램 최초 변경 전 값 기준)
                new = max(b0 - max_cut, min(b0 + (s.max_cumulative_db if s.auto_allow_boost else 0.0), new))
                if abs(new - cur) >= 0.1:
                    out[k] = round(new, 1)
            elif kind in ("freq", "q"):
                gk = k[:-1] + "g"
                g_cur = float(current.get(gk, 0.0) or 0.0)
                if abs(g_cur) > 0.05:
                    continue   # 사용 중인 밴드의 주파수/Q 는 자동으로 옮기지 않는다
                out[k] = c.recommended
            elif kind == "bool" and k == "eq/on":
                out[k] = c.recommended
        if not any(wa.key_kind(k) == "gain" for k in out):
            return {}   # 게인 변화가 없으면 주파수/Q 만 바꾸지 않는다
        return out

    # ------------------------------------------------------------ 기록
    def record_applied(self, ch: int, before: dict, after: dict, auto: bool, is_notch: bool = False) -> None:
        with self._lock:
            base = self.baseline.setdefault(ch, {})
            for k, v in before.items():
                base.setdefault(k, v)
            if auto:
                self.last_auto[ch] = time.monotonic()
                if is_notch:
                    self.notch_count[ch] = self.notch_count.get(ch, 0) + 1
