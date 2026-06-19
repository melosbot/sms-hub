"""Hub message/contact integration tests (v2)。"""

import asyncio

from core.infra import config
from core.infra import db
from core.app.routes import messages
from core.app.routes import send as send_routes

MAC = "aabbccddeeff"


def test_messages_include_contact_alias_and_search(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "contacts.db")

    async def run():
        await db.open_db()
        try:
            await db.upsert_sim("sim_a", commit=True)
            await db.insert_message({
                "sim_id": "sim_a", "device_mac": MAC, "device_msg_id": 1,
                "gw_epoch": 0, "sender": "13800138000",
                "text": "验证码 1234", "received_at": "2026-06-14 10:00:00",
            })
            await db.db().execute(
                "INSERT INTO contacts(phone,alias) VALUES(?,?)",
                ("13800138000", "Main SIM"),
            )
            await db.db().commit()

            listed = await messages.list_messages(sim_id="sim_a", q="Main", limit=10)
            assert listed["total"] == 1
            assert listed["messages"][0]["sender_alias"] == "Main SIM"

            exported = await messages.export_messages(sim_id="sim_a", fmt="json", q="Main")
            body = exported.body.decode()
            assert '"sender_alias": "Main SIM"' in body
        finally:
            await db.close_db()

    asyncio.run(run())


def test_send_canonicalizes_mainland_mobile(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "send-phone.db")
    monkeypatch.setattr(config, "DEVICE_TOKEN", "test-token")

    async def run():
        await db.open_db()
        try:
            await db.upsert_sim("sim_a", enabled=1, current_device_mac=MAC, commit=True)
            await db.upsert_device(MAC, base_url="http://x/t", commit=True)
            result = await send_routes.send(send_routes.SendBody(
                sim_id="sim_a", to="+8613800138000", text="hello"
            ))
            assert result["ok"] is True
            async with db.db().execute(
                "SELECT to_phone FROM outbound WHERE id=?", (result["id"],)
            ) as cur:
                row = await cur.fetchone()
            assert row["to_phone"] == "13800138000"
        finally:
            await db.close_db()

    asyncio.run(run())
