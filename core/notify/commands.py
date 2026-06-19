"""Telegram Bot 命令:/status /sms /history。长轮询 getUpdates,offset 持久化。

v2 多设备:/status 取默认发送卡片(或首个启用卡)的承载设备状态(读心跳缓存,
不打扰设备);/sms 走全局 DEFAULT_SEND_SIM_ID。
"""
import asyncio
import json
import logging

from core.infra import config
from core.infra import db
from core.device import client
from core.device import manager as device_manager
from core.notify import notifier
from core.sms import phone as phone_module
from core.sms import sender

log = logging.getLogger("commands")


async def _reply(chat_id: str, text: str):
    try:
        await notifier.tg_api("sendMessage", {"chat_id": chat_id, "text": text})
    except Exception as e:
        log.warning("命令回复失败: %s", e)


async def _pick_status_device():
    """选默认发送卡片(或首个启用卡)的承载设备,返回 (row, mac)。"""
    sim_id = config.DEFAULT_SEND_SIM_ID
    sim = await db.get_sim(sim_id) if sim_id else None
    if not (sim and sim["enabled"] and sim["current_device_mac"]):
        sims = await db.list_sims()
        sim = next((s for s in sims if s["enabled"] and s["current_device_mac"]), None)
    if not sim:
        return None, ""
    mac = sim["current_device_mac"]
    return await db.get_device(mac), mac


async def _cmd_status() -> str:
    dev, mac = await _pick_status_device()
    if not dev:
        return "⚠️ 暂无已注册设备"
    s = json.loads(dev["last_status_json"]) if dev["last_status_json"] else {}
    m = s.get("modem", {}) or {}
    b = s.get("buffer", {}) or {}
    async with db.db().execute(
        "SELECT COUNT(*) AS n FROM messages WHERE sim_id=?", (dev["current_sim_id"],)
    ) as cur:
        total = (await cur.fetchone())["n"]
    online = "在线" if (s.get("modem") or {}).get("ready") or b.get("count") is not None else "未知"
    return (
        f"📡 {client.display_mac(mac)} · {online}\n"
        f"模组: {'✓' if m.get('ready') else '✗'} {m.get('operator','')}"
        f" {m.get('csq_dbm','?')}dBm · IMSI尾 {m.get('imsi_tail','?')}\n"
        f"缓冲: {b.get('count',0)}/{b.get('capacity',0)} 丢弃 {b.get('dropped_total',0)}\n"
        f"Hub 已存 {total} 条,游标 #{dev['cursor']}"
    )


async def _cmd_sms(args: str) -> str:
    parts = args.split(None, 1)
    if len(parts) < 2:
        return "用法: /sms 号码 内容"
    to_phone, content = phone_module.canonicalize(parts[0]), parts[1]
    if not config.DEFAULT_SEND_SIM_ID:
        return "⚠️ 未配置默认发送卡片(DEFAULT_SEND_SIM_ID)"
    ob_id = await sender.enqueue_sms(
        to_phone, content, "telegram", sim_id=config.DEFAULT_SEND_SIM_ID
    )
    return f"✓ 已加入发送队列 #{ob_id}({client.estimate_parts(content)} 段)"


async def _cmd_history(args: str) -> str:
    try:
        n = max(1, min(int(args.strip() or "5"), 20))
    except ValueError:
        n = 5
    async with db.db().execute(
        "SELECT * FROM messages ORDER BY received_at DESC, id DESC LIMIT ?", (n,)
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return "还没有短信"
    out = []
    for r in rows:
        text = r["text"][:80] + ("…" if len(r["text"]) > 80 else "")
        out.append(f"[{r['received_at']}] {r['sender']}\n{text}")
    return "\n\n".join(out)


async def _handle(text: str, chat_id: str):
    cmd, _, args = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/status":
        await _reply(chat_id, await _cmd_status())
    elif cmd == "/sms":
        await _reply(chat_id, await _cmd_sms(args))
    elif cmd == "/history":
        await _reply(chat_id, await _cmd_history(args))


async def worker():
    offset = int(await db.get_kv("tg_offset", "0"))
    first_run = offset == 0
    announced = False
    while True:
        # 配置可在 UI 运行时修改:未配置或管理关闭时安静等待
        if not config.TG_ENABLED or not config.TG_MANAGE_ENABLED:
            if not announced:
                reason = "未配置" if not config.TG_ENABLED else "管理功能已关闭"
                log.info("Telegram %s,命令轮询挂起(UI 配置后自动启动)", reason)
                announced = True
            await asyncio.sleep(10)
            continue
        if announced:
            log.info("Telegram 已配置,命令轮询启动")
            announced = False
        try:
            resp = await notifier.tg_api(
                "getUpdates",
                {"offset": offset + 1, "timeout": 25, "allowed_updates": ["message"]},
                timeout=35.0,
            )
            for upd in resp.get("result", []):
                offset = max(offset, upd["update_id"])
                if first_run:
                    continue  # 跳过启动前积压的旧命令
                msg = upd.get("message") or {}
                chat_id = str((msg.get("chat") or {}).get("id", ""))
                text = msg.get("text", "")
                if chat_id == str(config.TG_CHAT_ID) and text.startswith("/"):
                    await _handle(text, chat_id)
            if resp.get("result"):
                await db.set_kv("tg_offset", str(offset))
            first_run = False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("getUpdates 异常: %s", e)
            await asyncio.sleep(10)
