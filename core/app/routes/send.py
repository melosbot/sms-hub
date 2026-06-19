"""Outbound SMS routes (v2:必须指定 sim_id)。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.app import auth
from core.app.simutil import resolve_sim_id_param, online_sim_ids
from core.infra import config
from core.infra import db
from core.device import client
from core.sms import phone as phone_module
from core.sms import sender

router = APIRouter(dependencies=[Depends(auth.require_auth)])


class SendBody(BaseModel):
    sim_id: str | None = None
    to: str
    text: str


@router.post("/api/send")
async def send(body: SendBody):
    sim_id = await resolve_sim_id_param(body.sim_id)
    to = phone_module.canonicalize(body.to)
    text = body.text.strip()
    if not to or not text:
        raise HTTPException(status_code=400, detail="号码和内容不能为空")
    if not config.DEVICE_TOKEN:
        raise HTTPException(status_code=503, detail="DEVICE_TOKEN 未配置,无法发送")
    sim = await db.get_sim(sim_id)
    mac = sim["current_device_mac"] if sim else ""
    ob_id = await sender.enqueue_sms(to, text, "webui", sim_id=sim_id, device_mac=mac)
    return {
        "ok": True, "queued": True, "id": ob_id,
        "parts": client.estimate_parts(text), "sim_id": sim_id, "device_mac": mac,
    }


@router.get("/api/outbound")
async def list_outbound(sim_id: str | None = None, limit: int = 20):
    limit = max(1, min(limit, 100))
    if sim_id == "online":
        ids = await online_sim_ids()
        if not ids:
            return {"outbound": []}
        ph = ",".join("?" for _ in ids)
        async with db.db().execute(
            f"SELECT * FROM outbound WHERE sim_id IN ({ph}) ORDER BY id DESC LIMIT ?", (*ids, limit)
        ) as cur:
            return {"outbound": [dict(r) for r in await cur.fetchall()]}
    if sim_id and sim_id != "all":
        async with db.db().execute(
            "SELECT * FROM outbound WHERE sim_id=? ORDER BY id DESC LIMIT ?", (sim_id, limit)
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    else:
        async with db.db().execute(
            "SELECT * FROM outbound ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    return {"outbound": rows}


@router.delete("/api/outbound/{ob_id}")
async def delete_outbound(ob_id: int):
    async with db.db().execute("SELECT id FROM outbound WHERE id=?", (ob_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404)
    await db.db().execute("DELETE FROM outbound WHERE id=?", (ob_id,))
    await db.db().commit()
    return {"ok": True}
