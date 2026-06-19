"""SQLite schema baseline + retention tests (v2)。"""

import asyncio
from datetime import datetime, timedelta

from core.infra import config
from core.infra import db


def test_open_db_creates_fresh_schema(monkeypatch, tmp_path):
    path = tmp_path / "fresh.db"
    monkeypatch.setattr(config, "DB_PATH", path)

    async def run():
        await db.open_db()
        try:
            assert db.SCHEMA_VERSION == 4
            async with db.db().execute("PRAGMA user_version") as cur:
                assert (await cur.fetchone())[0] == db.SCHEMA_VERSION
            assert await db.get_kv("schema_version") == str(db.SCHEMA_VERSION)
            for table, expected in {
                "messages": {"sim_id", "device_mac", "device_msg_id", "gw_epoch",
                             "sender", "blocked"},
                "outbound": {"sim_id", "device_mac", "device_msg_id",
                             "to_phone", "parts", "source"},
                "notify_jobs": {"msg_id", "target", "text"},
                "deleted_messages": {"sim_id", "device_mac", "gw_epoch", "device_msg_id"},
                "devices": {"mac", "base_url", "cursor", "gw_epoch", "current_sim_id"},
                "sims": {"sim_id", "imsi_hash", "imsi_tail", "identity_source",
                         "current_device_mac"},
                "contacts": {"phone", "alias"},
            }.items():
                async with db.db().execute(f"PRAGMA table_info({table})") as cur:
                    cols = {row["name"] for row in await cur.fetchall()}
                assert expected <= cols, f"{table} missing {expected - cols}"
            async with db.db().execute("PRAGMA index_list(outbound)") as cur:
                indexes = {row["name"] for row in await cur.fetchall()}
            assert "idx_outbound_status" in indexes
            async with db.db().execute("PRAGMA index_list(sims)") as cur:
                indexes = {row["name"] for row in await cur.fetchall()}
            assert "idx_sims_imsi_hash" in indexes
        finally:
            await db.close_db()

    asyncio.run(run())


def test_cleanup_old_messages_tombstones_deleted_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "retention.db")

    async def run():
        await db.open_db()
        try:
            await db.upsert_sim("sim_test", identity_source="imsi", commit=True)
            old_ts = (datetime.now() - timedelta(days=9)).replace(
                microsecond=0
            ).isoformat(sep=" ")
            new_ts = datetime.now().replace(microsecond=0).isoformat(sep=" ")
            old_id = await db.insert_message({
                "sim_id": "sim_test", "device_mac": "aabbccddeeff",
                "device_msg_id": 10, "gw_epoch": 0,
                "sender": "1069", "text": "old", "received_at": old_ts,
            })
            new_id = await db.insert_message({
                "sim_id": "sim_test", "device_mac": "aabbccddeeff",
                "device_msg_id": 11, "gw_epoch": 0,
                "sender": "1069", "text": "new", "received_at": new_ts,
            })
            await db.db().execute(
                "INSERT INTO notify_jobs(msg_id,channel,target,text) VALUES(?,?,?,?)",
                (old_id, "telegram", "42", "old"),
            )
            await db.db().execute(
                "INSERT INTO notify_jobs(msg_id,channel,target,text) VALUES(?,?,?,?)",
                (new_id, "telegram", "42", "new"),
            )
            await db.db().commit()

            assert await db.cleanup_old_messages(7) == 1
            async with db.db().execute(
                "SELECT device_msg_id FROM messages ORDER BY device_msg_id"
            ) as cur:
                assert [r["device_msg_id"] for r in await cur.fetchall()] == [11]
            async with db.db().execute("SELECT device_msg_id FROM deleted_messages") as cur:
                assert [r["device_msg_id"] for r in await cur.fetchall()] == [10]
            async with db.db().execute("SELECT msg_id FROM notify_jobs") as cur:
                assert [r["msg_id"] for r in await cur.fetchall()] == [new_id]
        finally:
            await db.close_db()

    asyncio.run(run())
