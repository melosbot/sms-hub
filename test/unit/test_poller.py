"""poller(v2) 测试:scts 解析、单事务原子入库、per-device 游标回退自愈。"""
import asyncio
from datetime import datetime, timedelta, timezone

from core.infra import config
from core.infra import db
from core.device import poller


# ── scts 解析(pdulib 格式 YYMMDDhhmmsszz) ──

def _expect_local(y, mo, d, h, mi, s, tz_hours):
    t = datetime(y, mo, d, h, mi, s, tzinfo=timezone(timedelta(hours=tz_hours)))
    return t.astimezone().replace(microsecond=0, tzinfo=None).isoformat(sep=" ")


def test_parse_scts():
    assert poller._parse_scts("26061210000032") == _expect_local(2026, 6, 12, 10, 0, 0, 8)


def test_parse_scts_invalid():
    for bad in ["", "26/06/12,10:00:00+32", "2606121000003", "26131210000032",
                "abcdefghijklmn"]:
        assert poller._parse_scts(bad) is None


def test_received_at_prefers_age():
    out = poller._received_at(60, "26061210000032")
    parsed = datetime.fromisoformat(out)
    assert abs((datetime.now() - timedelta(seconds=60) - parsed).total_seconds()) < 5


def test_received_at_scts_fallback():
    assert poller._received_at(-1, "26061210000032") == \
        _expect_local(2026, 6, 12, 10, 0, 0, 8)


def test_received_at_last_resort():
    out = poller._received_at(-1, "")
    assert abs((datetime.now() - datetime.fromisoformat(out)).total_seconds()) < 5


# ── poll_device 集成:入库原子性 + per-device 游标回退 ──

class FakeRuntime:
    """可编辑的设备消息源,替身 runtime.pull/delete。"""

    def __init__(self, mac, messages, latest):
        self.mac = mac
        self.base_url = "http://dev/token"
        self._messages = messages
        self._latest = latest
        self._pull_in_progress = False
        self.last_poll_ok_ts = 0.0
        self.last_status_ts = 0.0
        self.last_hook_ts = 0.0
        self.trigger = asyncio.Event()
        self.pull_again = False
        self.consecutive_fails = 0
        self._alerted_down = False

    async def pull(self, *, after, limit=20, include_status=False):
        out = [m for m in self._messages if m["device_msg_id"] > after][:limit]
        return {
            "mac": self.mac,
            "buffer": {
                "oldest_id": self._messages[0]["device_msg_id"] if self._messages else 0,
                "latest_id": self._latest, "count": len(out),
                "capacity": 50, "dropped_total": 0,
            },
            "messages": out,
            "status": None,
        }

    async def delete(self, ids):
        return {"ok": True, "deleted": [{"device_msg_id": i, "found": True} for i in ids]}


class FakeManager:
    async def with_io(self, fn, *a, **k):
        return await fn(*a, **k)

    async def derive_and_merge_sim(self, mac, modem, *, commit=True):
        return ""


def _msg(i, text="验证码 1234"):
    return {"device_msg_id": i, "id": i, "from": "10690329", "scts": "26061210000032",
            "age_s": 5, "text": text, "complete": True, "truncated": False}


def test_poll_device_atomic_and_regression(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "NOTIFY_CHANNELS", [{
        "id": "telegram", "type": "telegram", "name": "Telegram", "enabled": True,
        "config": {"bot_token": "tok", "chat_id": "42",
                   "api_base": "https://api.telegram.org"},
    }])

    async def fake_direct_notify(_text, *, parse_mode=None):
        return None

    monkeypatch.setattr(poller.notifier, "notify_telegram_direct", fake_direct_notify)
    mac = "aabbccddeeff"

    async def run():
        await db.open_db()
        try:
            await db.upsert_sim("sim_a", identity_source="imsi",
                                current_device_mac=mac, commit=True)
            await db.upsert_device(mac, base_url="http://dev/token", commit=True)
            await db.set_device_current_sim(mac, "sim_a", commit=True)
            mgr = FakeManager()

            # 1) 正常拉取:3 条入库,每条有通知任务,游标推进
            rt = FakeRuntime(mac, [_msg(1), _msg(2), _msg(3)], 3)
            assert await poller.poll_device(mgr, rt) == 3
            dev = await db.get_device(mac)
            assert dev["cursor"] == 3
            async with db.db().execute("SELECT COUNT(*) AS n FROM messages") as c:
                assert (await c.fetchone())["n"] == 3
            async with db.db().execute("SELECT COUNT(*) AS n FROM notify_jobs") as c:
                assert (await c.fetchone())["n"] == 3

            # 2) 重复拉取幂等
            assert await poller.poll_device(mgr, FakeRuntime(mac, [_msg(1), _msg(2), _msg(3)], 3)) == 0

            # 3) 设备编号回退(仅该设备):cursor=500 > latest=2,自动重置游标重新拉齐,
            #    新代次 (mac, gw_epoch=1, device_msg_id) 不与旧消息冲突
            await db.set_device_cursor_epoch(mac, cursor=500, gw_epoch=0, commit=True)
            rt3 = FakeRuntime(mac, [_msg(1, "新设备短信 验证码 9876"), _msg(2)], 2)
            assert await poller.poll_device(mgr, rt3) == 2
            dev = await db.get_device(mac)
            assert dev["gw_epoch"] == 1
            assert dev["cursor"] == 2
            async with db.db().execute(
                "SELECT gw_epoch, device_msg_id FROM messages"
                " ORDER BY id DESC LIMIT 1"
            ) as c:
                row = await c.fetchone()
            assert row["gw_epoch"] == 1 and row["device_msg_id"] == 2
        finally:
            await db.close_db()

    asyncio.run(run())
