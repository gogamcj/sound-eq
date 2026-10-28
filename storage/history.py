"""변경 이력, 승인/거부 기록, 오류 로그, 분석 요약."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .database import Database, dumps


@dataclass
class ChangeRecord:
    id: int
    ts: float
    mixer_ch: int
    source: str
    status: str
    before: dict
    requested: dict
    after: dict
    reason: str
    restored_by: int | None
    simulated: bool


class History:
    def __init__(self, db: Database, profile: str = ""):
        self.db = db
        self.profile = profile

    def record_change(self, mixer_ch: int, source: str, status: str, before: dict, requested: dict,
                      after: dict, reason: str = "", recommendation: dict | None = None,
                      simulated: bool = False) -> int:
        cur = self.db.execute(
            "INSERT INTO eq_changes(ts, profile, mixer_ch, source, status, before, requested, after, reason, "
            "recommendation, simulated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), self.profile, mixer_ch, source, status, dumps(before), dumps(requested), dumps(after),
             reason, dumps(recommendation or {}), int(simulated)))
        return int(cur.lastrowid)

    def mark_restored(self, change_id: int, restore_id: int) -> None:
        self.db.execute("UPDATE eq_changes SET restored_by=? WHERE id=?", (restore_id, change_id))

    def record_decision(self, mixer_ch: int | None, decision: str, detail: str = "") -> None:
        self.db.execute("INSERT INTO decisions(ts, mixer_ch, decision, detail) VALUES (?,?,?,?)",
                        (time.time(), mixer_ch, decision, detail))

    def record_summary(self, usb_ch: int, mixer_ch: int | None, instrument: str, summary: dict) -> None:
        self.db.execute("INSERT INTO analysis_summaries(ts, usb_ch, mixer_ch, instrument, summary) VALUES (?,?,?,?,?)",
                        (time.time(), usb_ch, mixer_ch, instrument, dumps(summary)))

    def _row(self, r) -> ChangeRecord:
        return ChangeRecord(r["id"], r["ts"], r["mixer_ch"], r["source"], r["status"], json.loads(r["before"]),
                            json.loads(r["requested"]), json.loads(r["after"]), r["reason"] or "",
                            r["restored_by"], bool(r["simulated"]))

    def changes(self, mixer_ch: int | None = None, limit: int = 200) -> list[ChangeRecord]:
        if mixer_ch is None:
            rows = self.db.query("SELECT * FROM eq_changes ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = self.db.query("SELECT * FROM eq_changes WHERE mixer_ch=? ORDER BY id DESC LIMIT ?",
                                 (mixer_ch, limit))
        return [self._row(r) for r in rows]

    def restorable(self, mixer_ch: int) -> list[ChangeRecord]:
        """아직 되돌리지 않은, 실제로 적용 확인된 프로그램 변경 (최신순)."""
        rows = self.db.query(
            "SELECT * FROM eq_changes WHERE mixer_ch=? AND restored_by IS NULL AND source IN ('user_approved','auto') "
            "AND status=? ORDER BY id DESC", (mixer_ch, "VERIFIED"))
        return [self._row(r) for r in rows]

    def events(self, limit: int = 200) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]
