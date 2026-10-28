from audio.channel_mapping import ChannelConfig, ChannelMap
from storage.database import Database
from storage.history import History
from storage.profiles import ProfileStore


def test_profile_roundtrip(tmp_path):
    db = Database(tmp_path / "p.db")
    ps = ProfileStore(db)
    m = ChannelMap([ChannelConfig(1, 5, "Lead Vox", "vocal_female", True, "mic", True)])
    ps.save("공연A", m, {"mixer_ip": "192.168.1.10"})
    m2, st = ps.load("공연A")
    assert m2.channels[0].mixer_ch == 5 and m2.channels[0].instrument == "vocal_female"
    assert st["mixer_ip"] == "192.168.1.10"
    assert ps.list() == ["공연A"]


def test_history_restorable(tmp_path):
    h = History(Database(tmp_path / "h.db"))
    a = h.record_change(3, "user_approved", "VERIFIED", {"eq/1g": 0}, {"eq/1g": -2}, {"eq/1g": -2})
    h.record_change(3, "auto", "FAILED_MISMATCH", {"eq/2g": 0}, {"eq/2g": -1}, {"eq/2g": 0})
    assert [r.id for r in h.restorable(3)] == [a]
    h.mark_restored(a, 99)
    assert h.restorable(3) == []
    h.record_decision(3, "rejected", "x")
