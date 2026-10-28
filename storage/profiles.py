"""공연 프로필 저장/불러오기 (채널 매핑, 악기, 자동 허용, 설정)."""
from __future__ import annotations

import json
import time

from audio.channel_mapping import ChannelMap

from .database import Database, dumps


class ProfileStore:
    def __init__(self, db: Database):
        self.db = db

    def save(self, name: str, channel_map: ChannelMap, settings: dict | None = None) -> None:
        data = {"channels": channel_map.to_list(), "settings": settings or {}}
        now = time.time()
        self.db.execute(
            "INSERT INTO profiles(name, created, updated, data) VALUES (?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET updated=excluded.updated, data=excluded.data",
            (name, now, now, dumps(data)))

    def load(self, name: str) -> tuple[ChannelMap, dict]:
        rows = self.db.query("SELECT data FROM profiles WHERE name=?", (name,))
        if not rows:
            raise KeyError(name)
        data = json.loads(rows[0]["data"])
        return ChannelMap.from_list(data["channels"]), data.get("settings", {})

    def list(self) -> list[str]:
        return [r["name"] for r in self.db.query("SELECT name FROM profiles ORDER BY updated DESC")]

    def delete(self, name: str) -> None:
        self.db.execute("DELETE FROM profiles WHERE name=?", (name,))
