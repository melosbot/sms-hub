"""1.5 身份快照脱敏测试:完整 IMSI/ICCID 可用于派生 sim_id,
但不得出现在 DB 快照、API 响应或日志中(§1.5)。"""

import asyncio
import json
import logging

from core.device import client
from core.app.routes import status as status_routes
from core.app.routes import devices as devices_routes
from core.infra import config
from core.infra import db
from core.device import manager as device_manager

MAC = "aabbccddeeff"


# ═══════════════════════════════════════════════════════════
# 纯函数: sanitize_device_snapshot
# ═══════════════════════════════════════════════════════════

def test_sanitize_removes_full_imsi_and_iccid():
    body = {
        "modem": {
            "imsi": "460001234567890",
            "iccid": "898600A00000F0217731",
            "imsi_tail": "7890",
            "operator": "CMCC",
        }
    }
    client.sanitize_device_snapshot(body)
    assert "imsi" not in body["modem"], "完整 imsi 必须删除"
    assert "iccid" not in body["modem"], "完整 iccid 必须删除"
    assert body["modem"]["imsi_tail"] == "7890"
    assert body["modem"]["operator"] == "CMCC"


def test_sanitize_preserves_tail_when_missing():
    """若快照中没有 tail,脱敏时从完整值提取。"""
    body = {"modem": {"imsi": "460001234567890", "iccid": "898600A00000F0217731"}}
    client.sanitize_device_snapshot(body)
    assert body["modem"]["imsi_tail"] == "7890"
    assert body["modem"]["iccid_tail"] == "7731"


def test_sanitize_does_not_overwrite_existing_tail():
    body = {"modem": {"imsi": "460001234567890", "imsi_tail": "TAIL"}}
    client.sanitize_device_snapshot(body)
    assert body["modem"]["imsi_tail"] == "TAIL"


def test_sanitize_idempotent():
    body = {"modem": {"imsi": "460001234567890", "imsi_tail": "7890"}}
    client.sanitize_device_snapshot(body)
    client.sanitize_device_snapshot(body)
    assert "imsi" not in body["modem"]


def test_sanitize_no_modem():
    body = {"buffer": {"latest_id": 1}}
    client.sanitize_device_snapshot(body)
    assert body == {"buffer": {"latest_id": 1}}


def test_sanitize_modem_not_dict():
    for val in [None, 42, "modem"]:
        body = {"modem": val}
        result = client.sanitize_device_snapshot(body)
        assert "modem" in result


# ═══════════════════════════════════════════════════════════
# 纯函数: sanitize_modem_block
# ═══════════════════════════════════════════════════════════

def test_sanitize_modem_block_removes_sensitive():
    clean = client.sanitize_modem_block(
        {"imsi": "460001234567890", "imsi_tail": "7890", "csq_dbm": -78}
    )
    assert "imsi" not in clean
    assert "iccid" not in clean
    assert clean["imsi_tail"] == "7890"
    assert clean["csq_dbm"] == -78


def test_sanitize_modem_block_none():
    assert client.sanitize_modem_block(None) is None


def test_sanitize_modem_block_no_sensitive():
    clean = client.sanitize_modem_block({"csq_dbm": -78, "operator": "CMCC"})
    assert clean == {"csq_dbm": -78, "operator": "CMCC"}


# ═══════════════════════════════════════════════════════════
# DB 快照: webhook 保存前脱敏
# ═══════════════════════════════════════════════════════════

def test_webhook_snapshot_is_clean(monkeypatch, tmp_path):
    """通过 handle_webhook 保存的快照不含完整 imsi/iccid。"""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "hook_snap.db")

    async def run():
        await db.open_db()
        try:
            # 注册设备
            await db.upsert_device(MAC, base_url="http://10.0.0.1/t", commit=True)
            mgr = device_manager.DeviceManager(hub_self=set())
            device_manager.set_manager(mgr)

            # webhook 携带完整身份
            body = {
                "mac": MAC,
                "event": "heartbeat",
                "ip": "10.0.0.1",
                "port": 80,
                "modem": {
                    "imsi": "460001234567890",
                    "iccid": "898600A00000F0217731",
                    "imsi_tail": "7890",
                    "operator": "CMCC",
                },
            }
            await mgr.handle_webhook(body, peer_ip="10.0.0.1")

            # 读 DB 快照
            dev = await db.get_device(MAC)
            snap = json.loads(dev["last_status_json"]) if dev["last_status_json"] else {}
            modem = snap.get("modem", {})

            assert "imsi" not in modem, f"DB 快照不应含完整 imsi,但发现: {modem}"
            assert "iccid" not in modem, f"DB 快照不应含完整 iccid,但发现: {modem}"
            assert modem.get("imsi_tail") == "7890", "imsi_tail 应保留"
            assert modem.get("operator") == "CMCC", "非敏感字段应保留"
        finally:
            await db.close_db()

    asyncio.run(run())


# ═══════════════════════════════════════════════════════════
# API 防御性过滤: /api/status
# ═══════════════════════════════════════════════════════════

def test_status_api_filters_imsi(monkeypatch, tmp_path):
    """即使用 DB 存有脏数据(完整 imsi),/api/status 返回仍过滤。"""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "status_api.db")
    monkeypatch.setattr(config, "POLL_INTERVAL", 60)

    async def run():
        await db.open_db()
        try:
            sim_id = "sim_0000000000000001"
            await db.upsert_sim(sim_id, enabled=1, current_device_mac=MAC,
                                imsi_tail="7890", commit=True)
            await db.upsert_device(MAC, base_url="http://x/t", commit=True)
            await db.set_device_current_sim(MAC, sim_id, commit=True)
            # 注入脏快照(含完整 imsi)
            dirty = {"modem": {"imsi": "460001234567890", "imsi_tail": "7890"}}
            await db.update_device_status_snapshot(
                MAC, json.dumps(dirty), 1234567890.0, commit=True
            )
            await db.update_device_timestamps(MAC, last_status_ts=1234567890.0, commit=True)

            mgr = device_manager.DeviceManager(hub_self=set())
            device_manager.set_manager(mgr)

            out = await status_routes.status()
            modem = (out.get("device") or {}).get("modem", {})
            assert "imsi" not in modem, f"API 响应不应含完整 imsi,但发现: {modem}"
            assert modem.get("imsi_tail") == "7890"
        finally:
            await db.close_db()

    asyncio.run(run())


# ═══════════════════════════════════════════════════════════
# API 防御性过滤: /api/devices
# ═══════════════════════════════════════════════════════════

def test_devices_api_filters_imsi(monkeypatch, tmp_path):
    """/api/devices 返回的 modem 块不含完整 imsi/iccid。"""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "devices_api.db")

    async def run():
        await db.open_db()
        try:
            await db.upsert_device(MAC, base_url="http://x/t", commit=True)
            # 注入脏快照(含完整 imsi 和 iccid)
            dirty = {"modem": {
                "imsi": "460001234567890", "iccid": "898600A00000F0217731",
                "imsi_tail": "7890", "iccid_tail": "7731",
            }}
            await db.update_device_status_snapshot(
                MAC, json.dumps(dirty), 1234567890.0, commit=True
            )

            mgr = device_manager.DeviceManager(hub_self=set())
            device_manager.set_manager(mgr)

            out = await devices_routes.list_devices()
            devices_list = out["devices"]
            for d in devices_list:
                modem = d.get("modem", {})
                assert "imsi" not in modem, f"devices API modem 不应含 imsi: {modem}"
                assert "iccid" not in modem, f"devices API modem 不应含 iccid: {modem}"
        finally:
            await db.close_db()

    asyncio.run(run())


# ═══════════════════════════════════════════════════════════
# DB 清洗迁移: _sanitize_device_snapshots
# ═══════════════════════════════════════════════════════════

def test_db_migration_cleans_existing_snapshots(monkeypatch, tmp_path):
    """启动时清洗迁移清除 devices.last_status_json 中遗留的完整身份。"""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "migrate.db")

    async def run():
        # 不经过 open_db(它已调用 _sanitize_device_snapshots)
        # 先手动建库、注入脏数据,再手动调迁移
        import aiosqlite
        conn = await aiosqlite.connect(tmp_path / "migrate.db")
        conn.row_factory = aiosqlite.Row
        try:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.executescript(db.SCHEMA)

            # 注入脏数据:含完整 imsi/iccid
            dirty = json.dumps({
                "modem": {"imsi": "460001234567890", "iccid": "898600A00000F0217731"}
            })
            await conn.execute(
                "INSERT INTO devices(mac, last_status_json, last_status_ts)"
                " VALUES(?,?,?)", (MAC, dirty, 1.0)
            )
            # 注入干净数据:不含 imsi/iccid
            clean = json.dumps({"modem": {"imsi_tail": "1234"}})
            await conn.execute(
                "INSERT INTO devices(mac, last_status_json, last_status_ts)"
                " VALUES(?,?,?)", ("bbccddeeff00", clean, 1.0)
            )
            await conn.commit()
            await conn.close()

            # 现在通过 open_db 打开(会调 _sanitize_device_snapshots)
            # 但 open_db 用全局 _db,需确保未初始化
            db._db = None

            # 跳过 open_db 的完整流程,直接调迁移函数来测试
            import aiosqlite as aiosqlite2
            db._db = await aiosqlite2.connect(tmp_path / "migrate.db")
            db._db.row_factory = aiosqlite.Row
            await db._sanitize_device_snapshots()

            # 验证脏数据已清洗
            dev = await db.get_device(MAC)
            snap = json.loads(dev["last_status_json"])
            modem = snap.get("modem", {})
            assert "imsi" not in modem, f"迁移后不应含完整 imsi: {modem}"
            assert "iccid" not in modem, f"迁移后不应含完整 iccid: {modem}"
            assert modem.get("imsi_tail") == "7890", "应提取 imsi_tail"
            assert modem.get("iccid_tail") == "7731", "应提取 iccid_tail"

            # 验证干净数据未变
            dev2 = await db.get_device("bbccddeeff00")
            snap2 = json.loads(dev2["last_status_json"])
            assert snap2["modem"]["imsi_tail"] == "1234"

        finally:
            if db._db is not None:
                await db._db.close()
                db._db = None

    asyncio.run(run())


# ═══════════════════════════════════════════════════════════
# sim_id 派生独立于脱敏
# ═══════════════════════════════════════════════════════════

def test_derive_still_works_after_sanitize():
    """脱敏只影响快照持久化,不影响 sim_id 派生(handle_webhook 已重排顺序)。"""
    imsi = "460001234567890"
    result = client.derive_sim_id(imsi)
    assert result is not None
    sim_id, imsi_hash, imsi_tail = result
    assert sim_id.startswith("sim_")
    assert len(sim_id) == 20  # "sim_" + 16 hex
    assert len(imsi_hash) == 64  # sha256 hex
    assert imsi_tail == "7890"


def test_imsi_never_in_sim_id():
    """sim_id 是 hash 派生,从不包含原始 IMSI 的任何子串。"""
    imsi = "460001234567890"
    result = client.derive_sim_id(imsi)
    assert result is not None
    sim_id, imsi_hash, _ = result
    assert "46000" not in sim_id
    # sha256(cat /dev/urandom) 不可能等于自身;但 imsi_hash 是 imsi 的 sha256
    # 这里只验证 sim_id 不含明文
    assert imsi not in sim_id
    assert imsi not in imsi_hash  # sha256 是 hex,不会有原始数字串


def test_empty_imsi_returns_none():
    assert client.derive_sim_id("") is None
    assert client.derive_sim_id("12345") is None  # < 6 位
    assert client.derive_sim_id("abc1234567890") is None  # 非纯数字


# ═══════════════════════════════════════════════════════════
# 日志不泄漏:确保 derive/sanitize 路径不意外 log imsi
# ═══════════════════════════════════════════════════════════

def test_sanitize_does_not_log_imsi(caplog):
    """脱敏过程不得将完整 IMSI 写入日志。"""
    body = {"modem": {"imsi": "460001234567890"}}
    with caplog.at_level(logging.DEBUG):
        client.sanitize_device_snapshot(body)
    # sanitize 函数本身不写日志;但验证日志没有意外泄漏
    for record in caplog.records:
        assert "460001234567890" not in record.getMessage(), \
            f"日志不应含完整 IMSI: {record.getMessage()}"
