"""EQ 쓰기: 사전 검증 → 현재값 재확인 → 변경분만 전송 → 재조회로 실제 적용 확인.

WING 은 UDP 설정을 에코하지 않으므로(문서) 적용 성공 여부는 오직 재조회 값으로만 판단한다.
안전 정책(모드/승인/제한)은 eq/safety_controller.py 가 담당하고, 이 모듈은 그 허가를 받은 뒤 호출된다.
"""
from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field

from .parameter_validation import ParameterValidator, ValidationError, values_equal
from .protocol_adapter import MixerAdapter, MixerError, MixerTimeout

log = logging.getLogger(__name__)


class ApplyStatus(str, enum.Enum):
    VERIFIED = "적용 완료 및 재조회 확인"
    NO_CHANGE = "변경 없음 (동일 값, 전송 안 함)"
    REJECTED_VALIDATION = "사전 검증 실패 (전송 안 함)"
    ABORTED_EXTERNAL_CHANGE = "현재값이 예상과 달라 중단 (전송 안 함)"
    FAILED_COMM = "통신 실패"
    FAILED_MISMATCH = "적용 실패 (재조회 값 불일치)"


@dataclass
class ApplyResult:
    ch: int
    status: ApplyStatus
    before: dict = field(default_factory=dict)
    requested: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    sent_keys: list = field(default_factory=list)
    message: str = ""
    latency_ms: float | None = None

    @property
    def ok(self) -> bool:
        return self.status in (ApplyStatus.VERIFIED, ApplyStatus.NO_CHANGE)


class EqController:
    def __init__(self, adapter: MixerAdapter, validator: ParameterValidator, settle_s: float = 0.05,
                 max_ch: int = 40):
        self.adapter = adapter
        self.validator = validator
        self.settle_s = settle_s
        self.max_ch = max_ch

    def apply(self, ch: int, changes: dict, expected_current: dict | None = None) -> ApplyResult:
        t0 = time.perf_counter()
        # 1) 전체 사전 검증 (하나라도 실패하면 아무것도 보내지 않는다)
        validated = {}
        try:
            for k, v in changes.items():
                validated[k] = self.validator.validate(ch, k, v, self.max_ch)
        except ValidationError as e:
            return ApplyResult(ch, ApplyStatus.REJECTED_VALIDATION, requested=dict(changes), message=str(e))

        # 2) 현재값 재확인
        try:
            before = {k: self.adapter.read_param(ch, k).value for k in validated}
        except (MixerTimeout, MixerError) as e:
            return ApplyResult(ch, ApplyStatus.FAILED_COMM, requested=validated, message=f"적용 전 조회 실패: {e}")
        if expected_current:
            for k, exp in expected_current.items():
                if k in before and not values_equal(k, before[k], exp):
                    return ApplyResult(ch, ApplyStatus.ABORTED_EXTERNAL_CHANGE, before=before, requested=validated,
                                       message=f"{k}: 추천 계산 당시 {exp}, 현재 {before[k]} (사용자가 변경한 것으로 보임)")

        # 3) 동일 값은 보내지 않는다
        to_send = {k: v for k, v in validated.items() if not values_equal(k, before[k], v)}
        if not to_send:
            return ApplyResult(ch, ApplyStatus.NO_CHANGE, before=before, requested=validated, after=dict(before))

        # 4) 전송
        sent = []
        try:
            for k, v in to_send.items():
                self.adapter.write_param(ch, k, v)
                sent.append(k)
        except MixerError as e:
            return ApplyResult(ch, ApplyStatus.FAILED_COMM, before=before, requested=validated, sent_keys=sent,
                               message=f"전송 실패: {e}")
        time.sleep(self.settle_s)

        # 5) 재조회
        try:
            after = {k: self.adapter.read_param(ch, k).value for k in validated}
        except (MixerTimeout, MixerError) as e:
            return ApplyResult(ch, ApplyStatus.FAILED_COMM, before=before, requested=validated, sent_keys=sent,
                               message=f"적용 후 재조회 실패 (적용 여부 미확인): {e}")
        mismatched = [k for k in to_send if not values_equal(k, after[k], to_send[k])]
        lat = (time.perf_counter() - t0) * 1000
        if mismatched:
            msg = ", ".join(f"{k}: 요청 {to_send[k]} / 실제 {after[k]}" for k in mismatched)
            return ApplyResult(ch, ApplyStatus.FAILED_MISMATCH, before, validated, after, sent, msg, lat)
        return ApplyResult(ch, ApplyStatus.VERIFIED, before, validated, after, sent, "", lat)
