"""GUI 스레드를 막지 않도록 믹서 통신 등은 작업 스레드에서 실행한다."""
from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()

    def run(self):
        try:
            r = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            log.error("작업 실패: %s\n%s", e, traceback.format_exc())
            self.signals.failed.emit(str(e))
            return
        self.signals.done.emit(r)


# 실행 중인 작업을 끝날 때까지 붙잡아 둔다. 호출한 쪽이 참조를 버리면 Python 이 신호 객체를
# 먼저 지워서, 작업 스레드가 지워진 객체로 신호를 보내다 콜백이 사라지거나 프로그램이 멈춘다.
_active: set = set()


def run_job(fn, *args, on_done=None, on_fail=None, **kwargs) -> Job:
    j = Job(fn, *args, **kwargs)
    j.setAutoDelete(False)
    _active.add(j)

    def _release(*_):
        _active.discard(j)

    if on_done:
        j.signals.done.connect(on_done)
    if on_fail:
        j.signals.failed.connect(on_fail)
    j.signals.done.connect(_release)
    j.signals.failed.connect(_release)
    QThreadPool.globalInstance().start(j)
    return j
