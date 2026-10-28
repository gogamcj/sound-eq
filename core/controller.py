"""애플리케이션 핵심 로직 (GUI 와 분리 — 헤드리스 테스트 가능).

GUI 는 이 컨트롤러의 메서드만 호출하고 상태를 표시한다. 무거운 작업(믹서 조회/쓰기)은 호출 스레드에서
동기로 실행되므로 GUI 는 작업 스레드(QThreadPool)에서 호출해야 한다.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field

from audio.audio_stream import AudioSource
from audio.channel_mapping import ChannelConfig, ChannelMap
from audio.dsp_analyzer import AnalysisWorker, AnalyzerConfig, ChannelSnapshot, DspAnalyzer
from common.latency import LATENCY
from common.states import AudioState, ChannelStatus, LinkState, OperatingMode
from eq.feedback_monitor import FeedbackMonitor
from eq.instrument_classifier import classify
from eq.instrument_profiles import get_profile
from eq.recommendation_engine import EngineSettings, Recommendation, RecommendationEngine
from eq.safety_controller import SafetyController, SafetySettings
from mixer import wing_addresses as wa
from mixer.channel_reader import learn_ranges, probe_channel_count, read_channel
from mixer.connection_manager import ConnectionManager
from mixer.eq_controller import ApplyResult, ApplyStatus, EqController
from mixer.models import ChannelEqState
from mixer.parameter_validation import ParameterValidator, values_equal
from mixer.protocol_adapter import MixerError, WingOscAdapter
from storage.database import Database
from storage.history import History
from storage.profiles import ProfileStore

log = logging.getLogger(__name__)


@dataclass
class AppSettings:
    mixer_ip: str = ""
    mixer_port: int = wa.OSC_PORT
    osc_timeout_s: float = 0.5
    allow_fallback_ranges: bool = False
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)
    engine: EngineSettings = field(default_factory=EngineSettings)
    safety: SafetySettings = field(default_factory=SafetySettings)
    auto_tick_s: float = 1.0
    eq_cache_max_age_s: float = 5.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AppSettings":
        s = cls()
        for k, v in d.items():
            if k == "analyzer":
                s.analyzer = AnalyzerConfig(**v)
            elif k == "engine":
                s.engine = EngineSettings(**v)
            elif k == "safety":
                s.safety = SafetySettings(**v)
            elif hasattr(s, k):
                setattr(s, k, v)
        return s


@dataclass
class PendingCheck:
    usb_idx: int
    mixer_ch: int
    kind: str
    freq: float
    excess_before: float
    due: float


class AppController:
    def __init__(self, settings: AppSettings | None = None, db_path: str = "data/wing_auto_eq.db"):
        self.settings = settings or AppSettings()
        self.db = Database(db_path)
        self.history = History(self.db)
        self.profiles = ProfileStore(self.db)
        self.safety = SafetyController(self.settings.safety)
        self.validator = ParameterValidator(self.settings.allow_fallback_ranges)
        self.engine = RecommendationEngine(self.settings.engine)
        self.channel_map = ChannelMap()
        self.link: ConnectionManager | None = None
        self.eq_ctl: EqController | None = None
        self.mixer_channel_count = wa.DEFAULT_MAX_CH
        self.ranges_learned: dict = {}
        self.audio: AudioSource | None = None
        self.analyzer: DspAnalyzer | None = None
        self.worker: AnalysisWorker | None = None
        self.feedback: FeedbackMonitor | None = None
        self.eq_cache: dict[int, ChannelEqState] = {}
        self.recs: dict[int, Recommendation] = {}
        self.ch_status: dict[int, ChannelStatus] = {}
        self.pending_checks: list[PendingCheck] = []
        self.messages: list[tuple[float, str, str]] = []   # (ts, level, text) GUI 로그
        self._auto_thread: threading.Thread | None = None
        self._auto_stop = threading.Event()
        self._mixer_lock = threading.Lock()
        self.safety.listeners.append(self._on_safety_change)

    # ================================================================ 공통
    def log(self, level: str, category: str, text: str) -> None:
        self.messages.append((time.time(), level, text))
        del self.messages[:-500]
        getattr(log, "warning" if level == "warning" else ("error" if level == "error" else "info"))(text)
        try:
            self.db.log_event(level, category, text)
        except Exception:
            log.exception("이벤트 기록 실패")

    @property
    def link_state(self) -> LinkState:
        return self.link.state if self.link else LinkState.DISCONNECTED

    @property
    def mixer_is_simulator(self) -> bool:
        return bool(self.link and self.link.adapter and getattr(self.link.adapter, "is_simulator", False))

    @property
    def audio_state(self) -> AudioState:
        return self.audio.state if self.audio else AudioState.STOPPED

    def _on_safety_change(self) -> None:
        if not self.safety.auto_enabled and self.safety.stop_reason:
            for k, st in list(self.ch_status.items()):
                cfg = self.channel_map.by_usb(k + 1)
                if cfg and cfg.auto_allowed:
                    self.ch_status[k] = ChannelStatus.AUTO_STOPPED

    # ================================================================ LAN
    def connect_mixer(self, ip: str, port: int | None = None, wait: float = 3.0) -> bool:
        self.disconnect_mixer()
        self.settings.mixer_ip = ip
        port = port or self.settings.mixer_port
        self.settings.mixer_port = port

        def factory():
            return WingOscAdapter(ip, port, timeout=self.settings.osc_timeout_s)

        self.link = ConnectionManager(factory)
        self.link.add_listener(self._on_link_change)
        self.link.start()
        ok = self.link.connected_event.wait(wait)
        if not ok:
            self.log("warning", "lan", f"믹서 {ip}:{port} 응답 없음 — 재연결을 계속 시도함 ({self.link.last_error})")
        return ok

    def disconnect_mixer(self) -> None:
        if self.link:
            self.link.stop()
        self.link = None
        self.eq_ctl = None

    def _on_link_change(self, old: LinkState, new: LinkState) -> None:
        self.safety.on_link(new == LinkState.CONNECTED)
        if new == LinkState.CONNECTED:
            info = self.link.info
            tag = "[시뮬레이터] " if self.mixer_is_simulator else ""
            self.log("info", "lan", f"{tag}믹서 연결: {info.name} {info.model} FW {info.firmware}")
            threading.Thread(target=self._post_connect, daemon=True).start()
        elif new == LinkState.LOST:
            self.log("error", "lan", f"LAN 통신 단절 — 자동 보정 중지, 재연결 시도 ({self.link.last_error})")

    def _post_connect(self) -> None:
        ad = self.link.adapter if self.link else None
        if ad is None:
            return
        try:
            with self._mixer_lock:
                self.mixer_channel_count = probe_channel_count(ad) or wa.DEFAULT_MAX_CH
                self.ranges_learned = learn_ranges(ad, self.validator, 1)
            self.eq_ctl = EqController(ad, self.validator, max_ch=self.mixer_channel_count)
            n_ok = sum(1 for v in self.ranges_learned.values() if v)
            if n_ok == 0:
                self.log("warning", "lan", "장비가 파라미터 범위(',s ?')를 알려주지 않음 — 쓰기 사전검증 불가로 적용이 거부됨")
            else:
                self.log("info", "lan", f"파라미터 범위 {n_ok}개 장비에서 확인, 입력 채널 {self.mixer_channel_count}개")
        except MixerError as e:
            self.log("error", "lan", f"연결 후 초기 조회 실패: {e}")

    def read_eq(self, mixer_ch: int, force: bool = False) -> ChannelEqState | None:
        st = self.eq_cache.get(mixer_ch)
        if st and not force and time.time() - st.read_time < self.settings.eq_cache_max_age_s:
            return st
        if not (self.link and self.link.is_connected and self.link.adapter):
            return st
        t0 = time.perf_counter()
        try:
            with self._mixer_lock:
                st = read_channel(self.link.adapter, mixer_ch)
        except MixerError as e:
            self.log("warning", "lan", f"ch{mixer_ch} EQ 읽기 실패: {e}")
            return self.eq_cache.get(mixer_ch)
        LATENCY.add("mixer_control", (time.perf_counter() - t0) * 1000 / max(1, len(st.values)))
        self.eq_cache[mixer_ch] = st
        return st

    # ================================================================ 오디오
    def start_audio(self, source: AudioSource) -> None:
        self.stop_audio()
        self.audio = source
        self.analyzer = DspAnalyzer(source.channels, source.samplerate, self.settings.analyzer)
        self.feedback = FeedbackMonitor(self.analyzer.bands.centers, source.channels, self.analyzer.frame_dt)
        self.analyzer.frame_listeners.append(self.feedback.on_frame)
        if len(self.channel_map.channels) != source.channels:
            self.channel_map = ChannelMap.identity(source.channels, self.mixer_channel_count)
        source.add_listener(self._on_audio_change)
        self.worker = AnalysisWorker(source.queue, self.analyzer)
        self.worker.start()
        source.start()
        self.ch_status = {i: ChannelStatus.ANALYZING for i in range(source.channels)}

    def stop_audio(self) -> None:
        if self.audio:
            self.audio.stop()
        if self.worker:
            self.worker.stop()
            self.worker.join(timeout=2)
        self.audio = self.worker = None

    def _on_audio_change(self, old: AudioState, new: AudioState) -> None:
        self.safety.on_audio(new == AudioState.RECEIVING)
        if new == AudioState.ERROR:
            self.log("error", "audio", f"오디오 입력 오류 — 자동 보정 중지 ({getattr(self.audio, 'last_error', '')})")
        elif new == AudioState.RECEIVING:
            tag = "[시뮬레이션 입력] " if self.audio and self.audio.is_simulated else ""
            self.log("info", "audio", f"{tag}오디오 수신 중 {self.audio.channels}ch @ {self.audio.samplerate}Hz")

    def snapshot(self, usb_idx: int) -> ChannelSnapshot | None:
        return self.analyzer.snapshot(usb_idx) if self.analyzer else None

    def instrument_suggestions(self, usb_idx: int):
        s = self.snapshot(usb_idx)
        return classify(s) if s else []

    # ================================================================ 추천
    def recommend(self, usb_idx: int, refresh_eq: bool = True) -> Recommendation | None:
        snap = self.snapshot(usb_idx)
        if snap is None:
            return None
        cfg = self.channel_map.by_usb(usb_idx + 1) or ChannelConfig(usb_idx + 1)
        eq = self.read_eq(cfg.mixer_ch, force=refresh_eq) if cfg.mixer_ch else None
        fb = self.feedback.suspects(usb_idx) if self.feedback else []
        rec = self.engine.analyze(snap, get_profile(cfg.instrument), eq, fb, cfg.instrument_confirmed, cfg.mixer_ch)
        if cfg.mixer_ch is None:
            rec.warnings.append("USB↔믹서 채널 매핑이 없어 적용할 수 없음 (분석만)")
        self.recs[usb_idx] = rec
        if self.ch_status.get(usb_idx) not in (ChannelStatus.APPLYING,):
            self.ch_status[usb_idx] = (ChannelStatus.AWAITING_APPROVAL if rec.items and
                                       self.safety.mode != OperatingMode.ANALYSIS_ONLY
                                       else ChannelStatus.WAITING_RECOMMENDATION)
        return rec

    def apply_recommendation(self, usb_idx: int, item_indices: list[int] | None = None) -> list[ApplyResult]:
        """모드 B/C 에서 사용자가 적용 버튼을 눌렀을 때만 호출된다."""
        rec = self.recs.get(usb_idx)
        cfg = self.channel_map.by_usb(usb_idx + 1)
        if rec is None or cfg is None or cfg.mixer_ch is None:
            raise RuntimeError("추천 또는 채널 매핑이 없다")
        items = [rec.items[i] for i in (item_indices if item_indices is not None else range(len(rec.items)))]
        changes, expected = {}, {}
        for it in items:
            changes.update(it.as_dict())
            expected.update({c.key: c.current for c in it.changes if c.current is not None})
        dec = self.safety.check_user_apply(changes)
        if not dec.allowed:
            self.log("warning", "safety", f"ch{cfg.mixer_ch} 적용 거부: {dec.reason}")
            self.ch_status[usb_idx] = ChannelStatus.APPLY_FAILED
            return [ApplyResult(cfg.mixer_ch, ApplyStatus.REJECTED_VALIDATION, requested=changes, message=dec.reason)]
        self.history.record_decision(cfg.mixer_ch, "approved", str(changes))
        res = self._apply(usb_idx, cfg.mixer_ch, changes, expected, "user_approved",
                          "; ".join(i.reason for i in items), {"items": [i.reason for i in items]})
        return [res]

    def reject_recommendation(self, usb_idx: int) -> None:
        cfg = self.channel_map.by_usb(usb_idx + 1)
        rec = self.recs.pop(usb_idx, None)
        self.history.record_decision(cfg.mixer_ch if cfg else None, "rejected",
                                     "; ".join(i.reason for i in rec.items) if rec else "")
        self.ch_status[usb_idx] = ChannelStatus.WAITING_RECOMMENDATION

    def _apply(self, usb_idx, mixer_ch, changes, expected, source, reason, recinfo, is_notch=False) -> ApplyResult:
        if self.eq_ctl is None:
            res = ApplyResult(mixer_ch, ApplyStatus.FAILED_COMM, requested=changes, message="믹서 제어가 준비되지 않음")
        else:
            self.ch_status[usb_idx] = ChannelStatus.APPLYING
            with self._mixer_lock:
                res = self.eq_ctl.apply(mixer_ch, changes, expected)
        LATENCY.add("mixer_control", res.latency_ms)
        if res.sent_keys or res.status != ApplyStatus.NO_CHANGE:
            self.history.record_change(mixer_ch, source, res.status.name, res.before, res.requested, res.after,
                                       reason + (f" | {res.message}" if res.message else ""), recinfo,
                                       simulated=self.mixer_is_simulator)
        if res.status == ApplyStatus.VERIFIED:
            self.safety.record_applied(mixer_ch, res.before, res.after, source == "auto", is_notch)
            self.ch_status[usb_idx] = ChannelStatus.APPLIED_VERIFIED
            self.eq_cache.pop(mixer_ch, None)
            self.log("info", "apply", f"ch{mixer_ch} 적용 확인 ({source}): {res.after}")
        elif res.ok:
            self.ch_status[usb_idx] = ChannelStatus.APPLIED_VERIFIED
        else:
            self.ch_status[usb_idx] = ChannelStatus.APPLY_FAILED
            self.log("error", "apply", f"ch{mixer_ch} 적용 실패 ({source}): {res.status.value} {res.message}")
            if source == "auto":
                self.safety.freeze_channel(mixer_ch, f"적용 실패: {res.status.value}")
                if res.status == ApplyStatus.FAILED_COMM:
                    self.safety.emergency_stop("믹서 통신 실패")
        return res

    # ================================================================ 복구
    def restore_channel(self, mixer_ch: int, only_last: bool = True) -> list[dict]:
        """프로그램이 바꾼 파라미터만 되돌린다. 현재 값이 프로그램이 쓴 값과 다르면(사용자가 이후 변경) 건너뛴다."""
        if self.safety.mode == OperatingMode.ANALYSIS_ONLY:
            raise PermissionError("분석 전용 모드(A)에서는 복구도 쓰기이므로 실행하지 않는다. 모드 B 로 바꾼 뒤 실행")
        if not (self.link and self.link.is_connected) or self.eq_ctl is None:
            raise ConnectionError("믹서 연결이 없다")
        records = self.history.restorable(mixer_ch)
        if only_last:
            records = records[:1]
        report = []
        usb_idx = next((c.usb_input - 1 for c in self.channel_map.channels if c.mixer_ch == mixer_ch), -1)
        for rec in records:   # 최신 변경부터 역순으로
            keys = [k for k in rec.after if k in rec.before]
            with self._mixer_lock:
                try:
                    current = {k: self.link.adapter.read_param(mixer_ch, k).value for k in keys}
                except MixerError as e:
                    report.append({"change_id": rec.id, "status": "통신 실패", "detail": str(e)})
                    break
            todo, skipped = {}, []
            for k in keys:
                if values_equal(k, current[k], rec.after[k]):
                    if not values_equal(k, rec.before[k], current[k]):
                        todo[k] = rec.before[k]
                else:
                    skipped.append(f"{k}: 프로그램 적용값 {rec.after[k]} ≠ 현재 {current[k]} (사용자 변경으로 보고 유지)")
            if not todo:
                report.append({"change_id": rec.id, "status": "복구할 항목 없음", "skipped": skipped})
                continue
            res = self._apply(usb_idx, mixer_ch, todo, {k: rec.after[k] for k in todo}, "restore",
                              f"변경 #{rec.id} 복구", {"restore_of": rec.id})
            if res.status == ApplyStatus.VERIFIED:
                rid = self.history.changes(mixer_ch, 1)[0].id
                self.history.mark_restored(rec.id, rid)
            report.append({"change_id": rec.id, "status": res.status.value, "restored": todo, "skipped": skipped,
                           "detail": res.message})
        return report

    # ================================================================ 자동 보정
    def set_mode(self, mode: OperatingMode) -> None:
        self.safety.set_mode(mode)
        self.log("info", "safety", f"운영 모드: {mode.value}")
        if mode == OperatingMode.AUTO_LIMITED:
            self._start_auto_thread()

    def enable_auto(self):
        d = self.safety.enable_auto()
        self.log("info" if d.allowed else "warning", "safety", f"자동 보정 켜기: {d.reason}")
        if d.allowed:
            self._start_auto_thread()
        return d

    def stop_all_auto(self, reason: str = "사용자 전체 정지 버튼") -> None:
        self.safety.emergency_stop(reason)
        self.pending_checks.clear()
        self.log("warning", "safety", f"자동 보정 전체 정지: {reason}")

    def _start_auto_thread(self) -> None:
        if self._auto_thread and self._auto_thread.is_alive():
            return
        self._auto_stop.clear()
        self._auto_thread = threading.Thread(target=self._auto_loop, name="auto-eq", daemon=True)
        self._auto_thread.start()

    def _auto_loop(self) -> None:
        while not self._auto_stop.wait(self.settings.auto_tick_s):
            try:
                self.auto_tick()
            except Exception:
                log.exception("자동 보정 루프 오류")
                self.safety.emergency_stop("자동 보정 내부 오류")

    def auto_tick(self) -> list[str]:
        """한 번의 자동 보정 판단. 수행 내역 문자열 리스트 반환 (테스트용)."""
        done: list[str] = []
        self._run_pending_checks()
        if not self.safety.auto_enabled or self.analyzer is None:
            return done
        for cfg in self.channel_map.channels:
            if not self.safety.auto_enabled:
                break
            if not cfg.auto_allowed or cfg.mixer_ch is None:
                continue
            idx = cfg.usb_input - 1
            snap = self.snapshot(idx)
            rec = self.recommend(idx, refresh_eq=True)
            if rec is None or not rec.items:
                continue
            fb = self.feedback.suspects(idx) if self.feedback else []
            current = dict(rec.based_on_eq)
            for it in sorted(rec.items, key=lambda i: -i.confidence)[: self.safety.s.max_bands_per_step]:
                d = self.safety.check_auto(cfg.mixer_ch, cfg, snap, it, fb, current)
                if not d.allowed:
                    continue
                exp = {k: current.get(k) for k in d.changes if current.get(k) is not None}
                res = self._apply(idx, cfg.mixer_ch, d.changes, exp, "auto", it.reason,
                                  {"kind": it.kind, "confidence": it.confidence}, it.is_notch)
                done.append(f"ch{cfg.mixer_ch} {it.kind} {d.changes} → {res.status.name}")
                if res.status == ApplyStatus.VERIFIED:
                    if self.settings.engine.analysis_tap == "post_eq":
                        self.analyzer.reset_long_term(idx)
                    self.pending_checks.append(PendingCheck(idx, cfg.mixer_ch, it.kind, it.freq, it.excess_db,
                                                            time.monotonic() + self.safety.s.min_interval_s * 0.75))
                break
        return done

    def _run_pending_checks(self) -> None:
        now = time.monotonic()
        for pc in [p for p in self.pending_checks if p.due <= now]:
            self.pending_checks.remove(pc)
            rec = self.recommend(pc.usb_idx, refresh_eq=True)
            if rec is None:
                continue
            fb = self.feedback.suspects(pc.usb_idx) if self.feedback else []
            if any(f.score >= self.safety.s.stop_on_feedback_score for f in fb) and pc.kind != "feedback":
                self.safety.freeze_channel(pc.mixer_ch, "변경 후 하울링 의심 증가")
                continue
            same = [i for i in rec.items if i.kind == pc.kind]
            if same:
                after = same[0].excess_db
                if after > pc.excess_before + self.safety.s.worsen_threshold_db:
                    self.safety.freeze_channel(pc.mixer_ch, f"{pc.kind} 지표 악화 {pc.excess_before:.1f}→{after:.1f}dB")
                    self.log("warning", "safety", f"ch{pc.mixer_ch} 변경 후 악화 의심 — 추가 자동 조정 중지")

    # ================================================================ 프로필
    def save_profile(self, name: str) -> None:
        self.profiles.save(name, self.channel_map, {"mixer_ip": self.settings.mixer_ip,
                                                    "analysis_tap": self.settings.engine.analysis_tap})
        self.history.profile = name

    def load_profile(self, name: str) -> None:
        cmap, st = self.profiles.load(name)
        n_usb = self.audio.channels if self.audio else len(cmap.channels)
        errs = cmap.validate(n_usb, self.mixer_channel_count)
        if errs:
            self.log("warning", "profile", "프로필 매핑 경고: " + "; ".join(errs))
        self.channel_map = cmap
        self.history.profile = name
        if st.get("analysis_tap"):
            self.settings.engine.analysis_tap = st["analysis_tap"]

    # ================================================================ 종료
    def shutdown(self) -> None:
        """자동 적용 중지 → 스레드 정리. 믹서에는 아무것도 쓰지 않는다(믹서 처리는 그대로 유지)."""
        self.safety.emergency_stop("프로그램 종료")
        self._auto_stop.set()
        if self._auto_thread:
            self._auto_thread.join(timeout=3)
        self.stop_audio()
        self.disconnect_mixer()
        self.db.close()
