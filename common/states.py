"""화면에 표시하는 상태 (명세 8장의 상태 구분)."""
from __future__ import annotations

import enum


class LinkState(str, enum.Enum):
    DISCONNECTED = "연결되지 않음"
    CONNECTING = "연결 확인 중"
    CONNECTED = "연결됨"
    LOST = "통신 단절 (재연결 시도 중)"


class AudioState(str, enum.Enum):
    NO_DEVICE = "오디오 장치 없음"
    STOPPED = "연결되지 않음"
    STARTING = "연결 확인 중"
    RECEIVING = "오디오 수신 중"
    ERROR = "오디오 오류 (재연결 시도 중)"


class ChannelStatus(str, enum.Enum):
    IDLE = "대기"
    ANALYZING = "분석 중"
    WAITING_RECOMMENDATION = "추천 대기"
    AWAITING_APPROVAL = "사용자 승인 대기"
    APPLYING = "적용 중"
    APPLIED_VERIFIED = "적용 완료 및 재조회 확인"
    APPLY_FAILED = "적용 실패"
    AUTO_STOPPED = "자동 보정 중지"


class OperatingMode(str, enum.Enum):
    ANALYSIS_ONLY = "A: 분석 전용"
    RECOMMEND_APPROVE = "B: 추천 및 사용자 승인"
    AUTO_LIMITED = "C: 제한적 자동 보정"
