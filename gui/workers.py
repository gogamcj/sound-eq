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


def run_job(fn, *args, on_done=None, on_fail=None, **kwargs) -> Job:
    j = Job(fn, *args, **kwargs)
    if on_done:
        j.signals.done.connect(on_done)
    if on_fail:
        j.signals.failed.connect(on_fail)
    QThreadPool.globalInstance().start(j)
    return j
