"""v2 多设备/多卡关键正确性测试。

覆盖计划要求的硬约束:跨设备同编号不冲突、IMSI 派生与临时卡合并、
墓碑按设备隔离、SSRF/MAC/sim_id 纯函数、sim_id 推断、全局并发池上限。
"""
import asyncio
import hashlib

import pytest
from fastapi import HTTPException

from core.infra import config
from core.infra import db
from core.device import client
from core.device import manager as device_manager
from core.app.simutil import resolve_sim_id_param


# ── 纯函数:MAC / sim_id 派生 / SSRF ──

def test_normalize_mac_accepts_common_formats():
    assert client.normalize_mac("aa:bb:cc:dd:ee:ff") == "aabbccddeeff"
    assert client.normalize_mac("AABBCCDDEEFF") == "aabbccddeeff"
    assert client.normalize_mac("aa-bb-cc-dd-ee-ff") == "aabbccddeeff"
    assert client.normalize_mac("AA.BB.CC.DD.EE.FF") == "aabbccddeeff"
    assert client.display_mac("aabbccddeeff") == "aa:bb:cc:dd:ee:ff"


def test_normalize_mac_rejects_invalid():
    for bad in [None, "", "short", "ggbbccddeeff", "aabbccddeeff1", "xx:yy"]:
        assert client.normalize_mac(bad) is None


def test_derive_sim_id_deterministic():
    a = client.derive_sim_id("460001234567731")
    b = client.derive_sim_id("460001234567731")
    assert a == b
    sim_id, imsi_hash, tail = a
    assert sim_id == "sim_" + hashlib.sha256(b"460001234567731").hexdigest()[:16]
    assert imsi_hash == hashlib.sha256(b"460001234567731").hexdigest()
    assert tail == "7731"
    # 完整 IMSI 不出现在 sim_id 里
    assert "460001234567731" not in sim_id


def test_derive_sim_id_rejects_garbage():
    for bad in ["", "abc", "12345", None]:
        assert client.derive_sim_id(bad) is None


def test_validate_device_addr_accepts_private():
    base = client.validate_device_addr("10.0.0.5", 8080, set())
    assert base == f"http://10.0.0.5:8080/{config.DEVICE_TOKEN}"
    # port 80 省略
    assert client.validate_device_addr("192.168.1.88", 80, set()).startswith("http://192.168.1.88/")
    # 172.16/12 与 192.168/16
    client.validate_device_addr("172.16.0.1", 80, set())
    client.validate_device_addr("192.168.0.1", 80, set())


def test_validate_device_addr_rejects_untrusted():
    for bad_ip in ["8.8.8.8", "127.0.0.1", "169.254.169.254", "0.0.0.0",
                   "224.0.0.1", "example.com"]:
        with pytest.raises(ValueError):
            client.validate_device_addr(bad_ip, 80, set())
    with pytest.raises(ValueError):
        client.validate_device_addr("10.0.0.5", 0, set())
    with pytest.raises(ValueError):
        client.validate_device_addr("10.0.0.5", 70000, set())
    # loopback 仅在 allow_loopback 时放行(demo 栈)
    client.validate_device_addr("127.0.0.1", 8080, set(), allow_loopback=True)
    # Hub 自身监听地址 + LISTEN_PORT 拒绝(自调用风险)
    with pytest.raises(ValueError):
        client.validate_device_addr("10.0.0.5", config.LISTEN_PORT, {"10.0.0.5"})


# ── 跨设备同编号不冲突 + UNIQUE 去重 ──

def test_two_devices_same_device_msg_id_no_collision(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "coll.db")

    async def run():
        await db.open_db()
        try:
            await db.upsert_sim("sim_a", commit=True)
            await db.upsert_sim("sim_b", commit=True)
            a1 = await db.insert_message({
                "sim_id": "sim_a", "device_mac": "aabbccddeeff",
                "device_msg_id": 1, "gw_epoch": 0, "sender": "x",
                "text": "from A", "received_at": "2026-06-16 10:00:00",
            })
            b1 = await db.insert_message({
                "sim_id": "sim_b", "device_mac": "112233445566",
                "device_msg_id": 1, "gw_epoch": 0, "sender": "x",
                "text": "from B", "received_at": "2026-06-16 10:00:00",
            })
            await db.db().commit()
            assert a1 is not None and b1 is not None and a1 != b1
            # 同 (mac,epoch,id) 重复 → 拦截
            dup = await db.insert_message({
                "sim_id": "sim_a", "device_mac": "aabbccddeeff",
                "device_msg_id": 1, "gw_epoch": 0, "sender": "x",
                "text": "dup", "received_at": "2026-06-16 10:00:00",
            })
            assert dup is None
        finally:
            await db.close_db()

    asyncio.run(run())


# ── 墓碑按设备隔离 ──

def test_tombstone_isolation_across_devices(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "tomb.db")

    async def run():
        await db.open_db()
        try:
            await db.upsert_sim("sim_a", commit=True)
            await db.upsert_sim("sim_b", commit=True)
            # 仅墓碑 A 设备 (mac_a, 0, 1)
            await db.tombstone_messages(
                [("aabbccddeeff", 0, 1, "sim_a")], commit=True
            )
            # A 设备同编号 → 被墓碑拦截
            assert await db.insert_message({
                "sim_id": "sim_a", "device_mac": "aabbccddeeff",
                "device_msg_id": 1, "gw_epoch": 0, "sender": "x",
                "text": "a", "received_at": "2026-06-16 10:00:00",
            }) is None
            # B 设备同编号 → 不受影响
            assert await db.insert_message({
                "sim_id": "sim_b", "device_mac": "112233445566",
                "device_msg_id": 1, "gw_epoch": 0, "sender": "x",
                "text": "b", "received_at": "2026-06-16 10:00:00",
            }) is not None
            await db.db().commit()
        finally:
            await db.close_db()

    asyncio.run(run())


# ── sim_id 派生 + 临时卡合并 ──

def test_derive_and_merge_sim_reassigns_temp(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "sim.db")

    async def run():
        await db.open_db()
        try:
            mgr = device_manager.DeviceManager(hub_self=set())
            mac = "aabbccddeeff"
            await db.upsert_device(mac, commit=True)
            # 先以临时卡收一条短信
            temp = client.temp_sim_id(mac)
            await db.upsert_sim(temp, identity_source="temporary",
                                current_device_mac=mac, commit=True)
            await db.set_device_current_sim(mac, temp, commit=True)
            mid = await db.insert_message({
                "sim_id": temp, "device_mac": mac, "device_msg_id": 1,
                "gw_epoch": 0, "sender": "10086", "text": "hi",
                "received_at": "2026-06-16 10:00:00",
            })
            await db.db().commit()

            # IMSI 到达:派生稳定 sim_id 并合并临时卡
            sim_id = await mgr.derive_and_merge_sim(mac, {
                "imsi": "460001234567731", "imsi_tail": "7731",
                "iccid": "898600A00000F0217731", "operator": "CMCC",
            })
            assert sim_id == "sim_" + hashlib.sha256(b"460001234567731").hexdigest()[:16]
            assert await db.get_sim(temp) is None
            async with db.db().execute("SELECT sim_id FROM messages WHERE id=?", (mid,)) as cur:
                assert (await cur.fetchone())["sim_id"] == sim_id
            assert (await db.get_device(mac))["current_sim_id"] == sim_id

            # 同一 IMSI 出现在另一台设备 → 复用同一 sim_id,sims 无重复
            mac2 = "112233445566"
            await db.upsert_device(mac2, commit=True)
            sim_id2 = await mgr.derive_and_merge_sim(mac2, {"imsi": "460001234567731"})
            assert sim_id2 == sim_id
            assert len(await db.list_sims()) == 1
        finally:
            await db.close_db()

    asyncio.run(run())


# ── sim_id 推断(0/1/多 启用卡)──

def test_resolve_sim_id_param_inference(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "infer.db")

    async def run():
        await db.open_db()
        try:
            with pytest.raises(HTTPException):
                await resolve_sim_id_param(None)  # 0 启用卡
            await db.upsert_sim("sim_a", enabled=1, commit=True)
            assert await resolve_sim_id_param(None) == "sim_a"  # 自动推断
            assert await resolve_sim_id_param("all") == "all"
            assert await resolve_sim_id_param("sim_a") == "sim_a"
            with pytest.raises(HTTPException):
                await resolve_sim_id_param("sim_x")  # 不存在
            await db.upsert_sim("sim_b", enabled=1, commit=True)
            with pytest.raises(HTTPException):
                await resolve_sim_id_param(None)  # 2 启用卡,无法推断
        finally:
            await db.close_db()

    asyncio.run(run())


# ── 全局设备 I/O 并发池上限 ──

def test_io_concurrency_capped(monkeypatch):
    monkeypatch.setattr(config, "MAX_DEVICE_IO_CONCURRENCY", 3)

    async def run():
        mgr = device_manager.DeviceManager(hub_self=set())
        current = 0
        peak = 0

        async def work():
            nonlocal current, peak
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.05)
            current -= 1

        await asyncio.gather(*[mgr.with_io(work) for _ in range(8)])
        assert peak <= 3  # 并发不超过池上限

    asyncio.run(run())
