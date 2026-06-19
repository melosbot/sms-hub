"""Message query, export, and deletion routes (v2:sim_id 业务分区 + 设备维度墓碑)。"""
import csv
import io
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from core.app import auth
from core.app.simutil import resolve_sim_id_param, online_sim_ids
from core.infra import db
from core.device import manager as device_manager
from core.sms.extractor import extract_brand

log = logging.getLogger("hub")
router = APIRouter(dependencies=[Depends(auth.require_auth)])

_FROM = (
    " FROM messages"
    " LEFT JOIN sims s ON s.sim_id=messages.sim_id"
    " LEFT JOIN contacts c ON c.phone=messages.sender"
)
_SELECT = (
    "SELECT messages.id, messages.sim_id, s.name AS sim_name, s.imsi_tail AS sim_imsi_tail, messages.device_mac,"
    " messages.device_msg_id, messages.gw_epoch, messages.sender, c.alias AS sender_alias,"
    " messages.text, messages.scts, messages.received_at, messages.code,"
    " messages.complete, messages.truncated, messages.blocked, messages.created_at,"
    " messages.content_type, messages.mms_url, messages.mms_size,"
    # 投递汇总(失败>进行中>成功>无),走 idx_notify_msg 子查询,无 N+1。
    " CASE"
    "  WHEN EXISTS(SELECT 1 FROM notify_jobs WHERE msg_id=messages.id AND status='give_up') THEN 'failed'"
    "  WHEN EXISTS(SELECT 1 FROM notify_jobs WHERE msg_id=messages.id AND status IN ('pending','retry')) THEN 'pending'"
    "  WHEN EXISTS(SELECT 1 FROM notify_jobs WHERE msg_id=messages.id) THEN 'delivered'"
    "  ELSE 'none' END AS delivery"
    + _FROM
)


async def _sim_filter(sim_id: str) -> tuple[str, list]:
    """返回 (WHERE 子句, 参数)。'all'=不过滤;'online'=仅在线卡集合;具体 sim_id=single。"""
    if sim_id == "all":
        return "", []
    if sim_id == "online":
        ids = await online_sim_ids()
        if not ids:
            return " WHERE 0", []
        ph = ",".join("?" for _ in ids)
        return f" WHERE messages.sim_id IN ({ph})", ids
    return " WHERE messages.sim_id=?", [sim_id]


def _search_cond(q: str, params: list) -> tuple[str, list]:
    if not q:
        return "", []
    q_like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return (
        " WHERE " if not params else " AND "
    ) + (
        "(messages.sender LIKE ? ESCAPE '\\' OR messages.text LIKE ? ESCAPE '\\'"
        " OR c.alias LIKE ? ESCAPE '\\')"
    ), params + [f"%{q_like}%", f"%{q_like}%", f"%{q_like}%"]


@router.get("/api/messages")
async def list_messages(sim_id: str | None = None, q: str = "",
                        limit: int = 50, offset: int = 0):
    sim_id = await resolve_sim_id_param(sim_id)
    readonly = False
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    params: list = []
    where, sp = await _sim_filter(sim_id)
    if where:
        params += sp
    sc, sp = _search_cond(q, params)
    if sc:
        where += sc
        params = sp
    sql = _SELECT + where + " ORDER BY received_at DESC, id DESC LIMIT ? OFFSET ?"
    async with db.db().execute(sql, params + [limit, offset]) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["brand"] = extract_brand(r.get("text", ""))
    async with db.db().execute("SELECT COUNT(*) AS n" + _FROM + where, params) as cur:
        total = (await cur.fetchone())["n"]
    return {"total": total, "messages": rows, "readonly": readonly}


@router.get("/api/messages/export")
async def export_messages(sim_id: str | None = None, fmt: str = "csv",
                          q: str = "", limit: int = 5000):
    sim_id = await resolve_sim_id_param(sim_id)
    limit = max(1, min(limit, 20000))
    params: list = []
    where, sp = await _sim_filter(sim_id)
    if where:
        params += sp
    sc, sp = _search_cond(q, params)
    if sc:
        where += sc
        params = sp
    sql = _SELECT + where + " ORDER BY received_at DESC, id DESC LIMIT ?"
    async with db.db().execute(sql, params + [limit]) as cur:
        rows = [dict(r) for r in await cur.fetchall()]

    if fmt.lower() == "json":
        body = json.dumps({"total": len(rows), "messages": rows}, ensure_ascii=False, indent=2)
        return Response(
            body,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="sms-export.json"'},
        )

    out = io.StringIO()
    fields = [
        "id", "sim_id", "sim_name", "device_mac", "device_msg_id", "gw_epoch",
        "sender", "sender_alias", "received_at", "code", "text",
        "scts", "complete", "truncated", "blocked", "created_at",
    ]
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="sms-export.csv"'},
    )


@router.get("/api/messages/{msg_id}")
async def get_message(msg_id: int):
    async with db.db().execute(_SELECT + " WHERE messages.id=?", (msg_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404)
    async with db.db().execute(
        "SELECT channel, status, attempts, last_error, created_at"
        " FROM notify_jobs WHERE msg_id=? ORDER BY id",
        (msg_id,),
    ) as cur:
        notify = [dict(r) for r in await cur.fetchall()]
    msg = dict(row)
    msg["brand"] = extract_brand(msg.get("text", ""))
    return {**msg, "notify": notify}


async def _delete_ids(ids: list[int]) -> int:
    """删除一批消息(本地必定删 + 写设备维度墓碑;设备删除 best-effort)。返回设备删除数。"""
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    async with db.db().execute(
        f"SELECT id, sim_id, device_mac, gw_epoch, device_msg_id"
        f" FROM messages WHERE id IN ({placeholders})", ids
    ) as cur:
        rows = await cur.fetchall()
    triples = [
        (r["device_mac"], r["gw_epoch"], r["device_msg_id"], r["sim_id"]) for r in rows
    ]
    await db.tombstone_messages(triples, commit=False)
    # messages 删除级联清 notify_jobs(ON DELETE CASCADE)
    await db.db().execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids)
    await db.db().commit()
    # best-effort 设备批量删除,按 device_mac 分组
    grouped: dict[str, list[int]] = {}
    for r in rows:
        grouped.setdefault(r["device_mac"], []).append(r["device_msg_id"])
    return await _delete_on_device(grouped)


async def _delete_on_device(grouped: dict[str, list[int]]) -> int:
    mgr = device_manager.get()
    deleted = 0
    for mac, ids in grouped.items():
        rt = mgr.get_runtime(mac)
        if rt is None or not rt.base_url:
            continue
        try:
            r = await rt.delete(ids)
        except Exception as e:
            log.info("设备 %s 批量删除失败(墓碑已兜底): %s", mac, e)
            continue
        for d in (r.get("deleted") or []):
            if d.get("found"):
                deleted += 1
    return deleted


@router.delete("/api/messages/{msg_id}")
async def delete_message(msg_id: int):
    device_deleted = await _delete_ids([msg_id])
    return {"ok": True, "deleted": 1, "device_deleted": device_deleted}


class DeleteMessagesBody(BaseModel):
    ids: list[int]


@router.delete("/api/messages")
async def delete_messages_batch(body: DeleteMessagesBody):
    if not body.ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    device_deleted = await _delete_ids(body.ids)
    return {"ok": True, "deleted": len(body.ids), "device_deleted": device_deleted}
