"""Status API semantics (v2):heartbeat 与 data-plane 双平面,按设备行判定。"""
import asyncio
import json
import time

from core.infra import config
from core.infra import db
from core.device import manager as device_manager
from core.app.routes import status

MAC = "aabbccddeeff"


async def _setup_sim_device(sim_id="sim_a"):
    """注册一张启用卡 + 承载设备。"""
    await db.upsert_sim(sim_id, enabled=1, current_device_mac=MAC, commit=True)
    await db.upsert_device(MAC, base_url="http://x/t", commit=True)
    await db.set_device_current_sim(MAC, sim_id, commit=True)


def test_status_splits_heartbeat_and_data_plane(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "status.db")
    monkeypatch.setattr(config, "POLL_INTERVAL", 5)

    async def run():
        await db.open_db()
        try:
            await _setup_sim_device()
            # 心跳旧(status_ts=0),数据面新(poll_ok_ts=now)
            await db.update_device_timestamps(
                MAC, last_status_ts=0.0, last_poll_ok_ts=time.time(), commit=True
            )
            device_manager.set_manager(device_manager.DeviceManager(hub_self=set()))
            out = await status.status()
            assert out["heartbeat_online"] is False
            assert out["data_plane_online"] is True
            assert out["overall_online"] is True
            assert out["device_reachable"] is True
        finally:
            await db.close_db()

    asyncio.run(run())


def test_status_uses_fresh_heartbeat(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "heartbeat.db")
    monkeypatch.setattr(config, "POLL_INTERVAL", 60)

    async def run():
        await db.open_db()
        try:
            await _setup_sim_device()
            await db.update_device_status_snapshot(
                MAC, json.dumps({"fw": "test-fw"}), time.time(), commit=True
            )
            await db.update_device_timestamps(
                MAC, last_status_ts=time.time(), last_poll_ok_ts=0.0, commit=True
            )
            device_manager.set_manager(device_manager.DeviceManager(hub_self=set()))
            out = await status.status()
            assert out["heartbeat_online"] is True
            assert out["data_plane_online"] is False
            assert out["overall_online"] is True
            assert out["device"]["fw"] == "test-fw"
        finally:
            await db.close_db()

    asyncio.run(run())
