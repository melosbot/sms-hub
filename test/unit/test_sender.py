"""Persistent outbound sender tests (v2:sim_id → 承载设备 runtime)。"""

import asyncio

from core.infra import config
from core.infra import db
from core.device import manager as device_manager
from core.device import runtime as runtime_mod
from core.sms import sender

MAC = "aabbccddeeff"


def _setup(monkeypatch, tmp_path, dbname):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / dbname)


async def _bootstrap():
    await db.upsert_sim("sim_a", enabled=1, current_device_mac=MAC, commit=True)
    await db.upsert_device(MAC, base_url="http://x/t", enabled=1, commit=True)
    await db.set_device_current_sim(MAC, "sim_a", commit=True)


def _manager_with_send(send_fn):
    mgr = device_manager.DeviceManager(hub_self=set())
    rt = runtime_mod.DeviceRuntime(MAC, mgr)
    rt.base_url = "http://x/t"
    rt.send = send_fn
    mgr.runtimes[MAC] = rt
    device_manager.set_manager(mgr)
    return mgr


def test_sender_success(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "sender.db")

    async def fake_send(to, text):
        return {"ok": True, "device_msg_id": 77, "parts": 2}

    async def run():
        await db.open_db()
        try:
            await _bootstrap()
            _manager_with_send(fake_send)
            ob_id = await sender.enqueue_sms(
                "+8613800138000", "hello" * 20, "webui", sim_id="sim_a", device_mac=MAC
            )
            assert await sender.process_once() == 1
            async with db.db().execute("SELECT * FROM outbound WHERE id=?", (ob_id,)) as cur:
                row = await cur.fetchone()
            assert row["status"] == "sent"
            assert row["device_msg_id"] == 77
            assert row["device_mac"] == MAC
            assert row["parts"] == 2
            assert row["attempts"] == 1
        finally:
            await db.close_db()

    asyncio.run(run())


def test_sender_retry_then_give_up(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "sender-fail.db")

    async def fake_send(to, text):
        raise RuntimeError("device offline")

    async def run():
        await db.open_db()
        try:
            await _bootstrap()
            _manager_with_send(fake_send)
            ob_id = await sender.enqueue_sms(
                "+8613800138000", "hello", "webui", sim_id="sim_a", device_mac=MAC
            )
            for _ in range(sender.MAX_ATTEMPTS):
                await db.db().execute(
                    "UPDATE outbound SET next_attempt_ts=0 WHERE id=?", (ob_id,)
                )
                await db.db().commit()
                assert await sender.process_once() == 1
            async with db.db().execute("SELECT * FROM outbound WHERE id=?", (ob_id,)) as cur:
                row = await cur.fetchone()
            assert row["status"] == "give_up"
            assert row["attempts"] == sender.MAX_ATTEMPTS
            assert "device offline" in row["last_error"]
        finally:
            await db.close_db()

    asyncio.run(run())


def test_sender_give_up_when_no_bearer(monkeypatch, tmp_path):
    """卡片无当前承载设备 → 立即放弃(不故障转移,§9)。"""
    _setup(monkeypatch, tmp_path, "sender-nobearer.db")

    async def run():
        await db.open_db()
        try:
            await db.upsert_sim("sim_a", enabled=1, current_device_mac="", commit=True)
            device_manager.set_manager(device_manager.DeviceManager(hub_self=set()))
            ob_id = await sender.enqueue_sms(
                "13800138000", "hi", "webui", sim_id="sim_a"
            )
            assert await sender.process_once() == 1
            async with db.db().execute("SELECT * FROM outbound WHERE id=?", (ob_id,)) as cur:
                row = await cur.fetchone()
            assert row["status"] == "give_up"
        finally:
            await db.close_db()

    asyncio.run(run())


# ── _resolve_bearer 分支覆盖 + §9 不故障转移 ──
MAC2 = "b0b0b0b0b0b0"


def test_resolve_bearer_none_when_sim_disabled(monkeypatch, tmp_path):
    """卡片禁用 → (None, ""),不解析承载设备。"""
    _setup(monkeypatch, tmp_path, "bearer-simoff.db")

    async def run():
        await db.open_db()
        try:
            await db.upsert_sim("sim_a", enabled=0, current_device_mac=MAC, commit=True)
            await db.upsert_device(MAC, base_url="http://x/t", enabled=1, commit=True)
            rt, mac = await sender._resolve_bearer("sim_a")
            assert rt is None and mac == ""
        finally:
            await db.close_db()

    asyncio.run(run())


def test_resolve_bearer_none_when_device_disabled(monkeypatch, tmp_path):
    """承载设备禁用 → (None, mac)。"""
    _setup(monkeypatch, tmp_path, "bearer-devoff.db")

    async def run():
        await db.open_db()
        try:
            await db.upsert_sim("sim_a", enabled=1, current_device_mac=MAC, commit=True)
            await db.upsert_device(MAC, base_url="http://x/t", enabled=0, commit=True)
            rt, mac = await sender._resolve_bearer("sim_a")
            assert rt is None and mac == MAC
        finally:
            await db.close_db()

    asyncio.run(run())


def test_resolve_bearer_none_when_runtime_missing(monkeypatch, tmp_path):
    """设备在库但 manager 无 runtime(未上报地址)→ (None, mac)。"""
    _setup(monkeypatch, tmp_path, "bearer-nort.db")

    async def run():
        await db.open_db()
        try:
            await db.upsert_sim("sim_a", enabled=1, current_device_mac=MAC, commit=True)
            await db.upsert_device(MAC, base_url="http://x/t", enabled=1, commit=True)
            device_manager.set_manager(device_manager.DeviceManager(hub_self=set()))
            rt, mac = await sender._resolve_bearer("sim_a")
            assert rt is None and mac == MAC
        finally:
            await db.close_db()

    asyncio.run(run())


def test_no_failover_to_other_card(monkeypatch, tmp_path):
    """§9:目标卡不可用时立即 give_up,绝不故障转移到其他可用卡。"""
    _setup(monkeypatch, tmp_path, "sender-nofailover.db")
    sent = []  # 记录所有 runtime.send 调用

    async def run():
        await db.open_db()
        try:
            # 卡 A(发送目标)无承载设备;卡 B 在线可发
            await db.upsert_sim("sim_a", enabled=1, current_device_mac="", commit=True)
            await db.upsert_sim("sim_b", enabled=1, current_device_mac=MAC2, commit=True)
            await db.upsert_device(MAC2, base_url="http://b/t", enabled=1, commit=True)
            await db.set_device_current_sim(MAC2, "sim_b", commit=True)

            async def fake_send(to, text):
                sent.append((to, text))
                return {"ok": True, "device_msg_id": 1, "parts": 1}

            mgr = device_manager.DeviceManager(hub_self=set())
            rt_b = runtime_mod.DeviceRuntime(MAC2, mgr)
            rt_b.base_url = "http://b/t"
            rt_b.send = fake_send
            mgr.runtimes[MAC2] = rt_b
            device_manager.set_manager(mgr)

            ob = await sender.enqueue_sms("13800138000", "hi", "webui", sim_id="sim_a")
            await sender.process_once()
            async with db.db().execute("SELECT status FROM outbound WHERE id=?", (ob,)) as cur:
                row = await cur.fetchone()
            assert row["status"] == "give_up"   # A 立即放弃
            assert sent == []                    # B 的 send 从未被调用 → 未故障转移
        finally:
            await db.close_db()

    asyncio.run(run())
