"""poller(v2):每设备独立拉取循环 + 单事务入库不变量。

每台启用设备由 DeviceManager 拉起一个 `device_loop` task,在自身
`runtime.trigger`(webhook 触发)或 `POLL_INTERVAL` 兜底上拉取一批。
核心不变量(承自 v1,现按设备隔离):把"插入消息 + 派发通知/代发 + 写游标/时间戳"
放在**同一事务提交,之后才推进游标**。任意点崩溃,重启后要么幂等重拉,要么已完整入库。

sim_id 解析在入库事务内完成(D4):设备 current_sim_id 为空时首拉带
include_status=1,从 status.modem.imsi 派生稳定 sim_id 并合并临时卡;
webhook 与首拉都无 IMSI 时回退临时卡 sim_tmp_<mac>。
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from core.device import client
from core.infra import config
from core.infra import db
from core.infra import events
from core.sms import extractor
from core.sms import mms
from core.sms import phone
from core.sms import rules
from core.notify import notifier
from core.sms import sender

# 管理员代发短信格式: 收件手机号:内容 (英文冒号)
_ADMIN_RELAY_RE = re.compile(r"^(\+?\d{3,20}):(.+)$", re.DOTALL)

# MMS concat 合并窗口:同 sim+sender+ref 的 mms 片在此窗口内收齐才合并。
_MMS_MERGE_WINDOW = "datetime('now','localtime','-5 minutes')"


async def _try_merge_mms(msg_id: int, rec: dict) -> int | None:
    """mms 片入库后检查同 (sim_id,sender,ref) 片齐否;齐则拼 hex 解码,
    把最早一片 UPDATE 为彩信(text 摘要+mms_url/size),删其余片并写墓碑防回流。
    单包 MMS(固件单片)直接 decode。整个合并属调用方事务(不 commit)。
    返回合并主 id(已完整,计入 inserted)或 None(未齐,静默等后续片)。"""
    log = logging.getLogger(__name__)
    u = mms.parse_udh(rec["text"])
    if not u:
        await db.db().execute(
            "UPDATE messages SET text='[彩信通知]' WHERE id=?", (msg_id,)
        )
        return msg_id
    if u["total"] <= 1:
        info = mms.decode_mms_notification([(u["seq"], u["payload"])])
        await db.db().execute(
            "UPDATE messages SET text=?, mms_url=?, mms_size=? WHERE id=?",
            (info["url"] or "[彩信通知]", info["url"], info["size"], msg_id),
        )
        return msg_id
    ref, total = u["ref"], u["total"]
    async with db.db().execute(
        f"SELECT id, text FROM messages WHERE sim_id=? AND sender=? "
        f"AND content_type='mms' AND mms_url='' AND received_at > {_MMS_MERGE_WINDOW} "
        f"ORDER BY id",
        (rec["sim_id"], rec["sender"]),
    ) as cur:
        rows = await cur.fetchall()
    parts = [
        (pu["seq"], pu["payload"], r["id"]) for r in rows
        if (pu := mms.parse_udh(r["text"])) and pu["ref"] == ref
    ]
    if len({p[0] for p in parts}) < total:
        return None  # 未齐,等后续片
    parts.sort(key=lambda x: x[0])
    info = mms.decode_mms_notification([(s, pl) for s, pl, _ in parts])
    primary_id = parts[0][2]
    await db.db().execute(
        "UPDATE messages SET text=?, mms_url=?, mms_size=? WHERE id=?",
        (info["url"] or "[彩信通知]", info["url"], info["size"], primary_id),
    )
    other_ids = [p[2] for p in parts[1:]]
    if other_ids:
        ph = ",".join("?" for _ in other_ids)
        async with db.db().execute(
            f"SELECT device_mac, gw_epoch, device_msg_id, sim_id FROM messages WHERE id IN ({ph})",
            other_ids,
        ) as cur:
            triples = [
                (r["device_mac"], r["gw_epoch"], r["device_msg_id"], r["sim_id"])
                for r in await cur.fetchall()
            ]
        await db.tombstone_messages(triples, commit=False)
        await db.db().execute(f"DELETE FROM messages WHERE id IN ({ph})", other_ids)
    log.info("MMS 合并 %d 片 → #%s (url=%s %sB)", len(parts), primary_id, info["url"], info["size"])
    return primary_id

log = logging.getLogger("poller")

# 单次拉取批量。固件拼 JSON 是整段 String,批次小一点避免堆内存尖峰
PULL_BATCH = 20

# pdulib 短信中心时间戳:YYMMDDhhmmsszz,14 位纯数字
_SCTS_RE = re.compile(r"^\d{14}$")

# 多设备失联告警合并(D9):60s 窗口内合并,消息列全部当前离线 mac
_down_macs: set[str] = set()
_last_alert_ts: float = 0.0
_ALERT_THROTTLE_S = 60.0


def _parse_scts(scts: str) -> str | None:
    if not _SCTS_RE.match(scts):
        return None
    try:
        yy, mo, dd, hh, mi, ss, tz_q = (int(scts[i:i + 2]) for i in range(0, 14, 2))
        t = datetime(2000 + yy, mo, dd, hh, mi, ss,
                     tzinfo=timezone(timedelta(minutes=tz_q * 15)))
    except ValueError:
        return None
    return t.astimezone().replace(microsecond=0, tzinfo=None).isoformat(sep=" ")


def _received_at(age_s: int, scts: str = "") -> str:
    """接收时间:正常用 age_s 回推;断电恢复(age_s=-1)用 scts 兜底。"""
    if age_s is not None and age_s >= 0:
        t = datetime.now() - timedelta(seconds=age_s)
        return t.replace(microsecond=0).isoformat(sep=" ")
    if scts:
        parsed = _parse_scts(scts)
        if parsed:
            return parsed
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


async def _handle_admin_relay(msg_id: int, row: dict):
    """处理管理员代发:解析 '收件人:内容',从 DEFAULT_SEND_SIM_ID 发出(§9)。"""
    if not config.DEFAULT_SEND_SIM_ID:
        log.info("管理员短信 #%s 收到但未配置 DEFAULT_SEND_SIM_ID,忽略代发", msg_id)
        return
    text = row["text"].strip()
    m = _ADMIN_RELAY_RE.match(text)
    if not m:
        return
    to_phone = phone.canonicalize(m.group(1))
    content = m.group(2).strip()
    if not to_phone or not content:
        return
    await sender.enqueue_sms(
        to_phone, content, "admin_relay",
        sim_id=config.DEFAULT_SEND_SIM_ID, commit=False,
    )


async def poll_device(manager, runtime) -> int:
    """对该设备拉取入库一批(批量直到拉空),返回新入库条数。
    任意时刻每台设备只允许一个 poll_device 在跑(_pull_in_progress 守卫),
    供 device_loop 与交互式 /api/poll 安全共享。"""
    if runtime._pull_in_progress:
        raise client.DeviceBusy("设备忙: 正在执行 拉取短信")
    runtime._pull_in_progress = True
    try:
        row = await db.get_device(runtime.mac)
        if not row or not row["enabled"]:
            return 0
        cursor = int(row["cursor"])
        epoch = int(row["gw_epoch"])
        resolved_sim = row["current_sim_id"] or ""
        include_status = not bool(resolved_sim)  # 首拉解析 sim_id
        inserted_total = 0
        latest_preview: dict | None = None  # 本轮最新入库 SMS 摘要,随 new_messages 事件下发
        while True:
            data = await runtime.pull(
                after=cursor, limit=PULL_BATCH, include_status=include_status
            )
            runtime.last_poll_ok_ts = time.time()
            buf = data.get("buffer") or {}
            latest = int(buf.get("latest_id", cursor))
            msgs = data.get("messages") or []

            try:
                await db.update_device_timestamps(
                    runtime.mac, last_poll_ok_ts=runtime.last_poll_ok_ts, commit=False
                )
                # 首批(include_status)解析/合并 sim_id
                if include_status:
                    modem = (data.get("status") or {}).get("modem") or {}
                    if modem.get("imsi"):
                        resolved_sim = await manager.derive_and_merge_sim(
                            runtime.mac, modem, commit=False
                        )
                    include_status = False
                # 仍无 sim_id:回退临时卡
                if not resolved_sim:
                    resolved_sim = client.temp_sim_id(runtime.mac)
                    await db.upsert_sim(
                        resolved_sim, identity_source="temporary",
                        current_device_mac=runtime.mac, commit=False,
                    )
                    await db.set_device_current_sim(runtime.mac, resolved_sim, commit=False)

                # 设备编号回退(仅该设备):latest<cursor 且无新消息
                if latest < cursor and not msgs:
                    epoch += 1
                    cursor = 0
                    await db.set_device_cursor_epoch(
                        runtime.mac, cursor=0, gw_epoch=epoch, commit=False
                    )
                    await db.db().commit()
                    await notifier.notify_telegram_direct(
                        notifier.format_system_event(
                            f"设备 {client.display_mac(runtime.mac)} 消息编号回退",
                            [("处理结果", "已重置游标并重拉设备缓冲")],
                            "疑似换机或固件重置",
                        ),
                        parse_mode="Markdown",
                    )
                    continue

                for m in msgs:
                    text = m.get("text", "")
                    from_phone = phone.canonicalize(m.get("from", ""))
                    blocked = rules.is_blocked(from_phone)
                    device_msg_id = int(m.get("device_msg_id", m.get("id", 0)))
                    is_mms = bool(m.get("mms"))
                    rec = {
                        "sim_id": resolved_sim,
                        "device_mac": runtime.mac,
                        "device_msg_id": device_msg_id,
                        "gw_epoch": epoch,
                        "sender": from_phone,
                        "text": text,
                        "scts": m.get("scts", ""),
                        "received_at": _received_at(m.get("age_s", -1), m.get("scts", "")),
                        "code": None if is_mms else extractor.extract_code(text),
                        "complete": m.get("complete", True),
                        "truncated": m.get("truncated", False),
                        "blocked": blocked,
                        "content_type": "mms" if is_mms else "sms",
                    }
                    msg_id = await db.insert_message(rec)
                    if msg_id is not None:
                        if is_mms:
                            # MMS concat 合并:收齐则解成一条彩信,不发 Telegram(hex 无意义);
                            # 未齐则静默等后续片。前端经 events 刷新可见。
                            merged = await _try_merge_mms(msg_id, rec)
                            if merged is not None:
                                inserted_total += 1
                        else:
                            inserted_total += 1
                            # 记最新一条摘要,随 new_messages 事件下发
                            latest_preview = {
                                "id": msg_id,
                                "sender": from_phone,
                                "brand": extractor.extract_brand(text),
                                "code": rec["code"],
                                "text": text[:60],
                                "content_type": "sms",
                            }
                            if blocked:
                                log.info("拦截短信 #%s 来自 %s(黑名单)", device_msg_id, from_phone)
                            elif rules.is_admin(from_phone):
                                await _handle_admin_relay(msg_id, rec)
                            else:
                                await notifier.enqueue_for_message(msg_id, rec, commit=False)
                    cursor = max(cursor, device_msg_id)

                await db.set_device_cursor_epoch(
                    runtime.mac, cursor=cursor, gw_epoch=epoch, commit=False
                )
                await db.db().commit()  # 单事务:消息+通知+游标+时间戳
                sender.wakeup()
            except Exception:
                await db.db().rollback()
                raise

            cursor = max(cursor, latest)
            await db.set_device_cursor_epoch(
                runtime.mac, cursor=cursor, gw_epoch=epoch, commit=True
            )
            if len(msgs) < PULL_BATCH:
                break

        # 墓碑/旧消息清理(全局,廉价)
        try:
            await db.cleanup_tombstones(config.TOMBSTONE_KEEP_DAYS)
            old = await db.cleanup_old_messages(config.MESSAGE_KEEP_DAYS)
            if old:
                log.info("按保留策略清理旧短信 %d 条", old)
        except Exception:
            pass

        if inserted_total:
            ev = {
                "type": "new_messages", "sim_id": resolved_sim,
                "device_mac": runtime.mac, "count": inserted_total,
            }
            if latest_preview:
                ev["latest"] = latest_preview
            events.publish(ev)
        return inserted_total
    finally:
        runtime._pull_in_progress = False


async def clear_device_buffer(runtime) -> int:
    """手动排空设备缓冲:删除设备本地缓冲里、且已同步到 Hub 的消息
    (先 pull 枚举设备缓冲实际内容,再按 mac+device_msg_id 跨 epoch 匹配已同步的)。
    未同步的保留以便继续拉取。设备缓冲默认作"Hub 刷机/丢库"兜底,不自动排空。"""
    row = await db.get_device(runtime.mac)
    if not row:
        return 0
    # 枚举设备缓冲当前内容(pull 不入库,仅读取)
    data = await runtime.pull(after=0, limit=50, include_status=False)
    dev_ids = [
        int(m["device_msg_id"])
        for m in (data.get("messages") or [])
        if m.get("device_msg_id") is not None
    ]
    if not dev_ids:
        return 0
    synced = await db.fetch_synced_device_ids(runtime.mac, dev_ids)
    if not synced:
        return 0
    await runtime.delete(synced)
    # 重拉刷新 Hub 缓存快照里的 buffer,状态页立即反映排空后的设备缓冲
    fresh = await runtime.pull(after=0, limit=1, include_status=False)
    existing = json.loads(row["last_status_json"]) if row["last_status_json"] else {}
    merged = {**existing, "buffer": fresh.get("buffer") or existing.get("buffer") or {}}
    await db.update_device_status_snapshot(
        runtime.mac, json.dumps(merged, ensure_ascii=False), time.time(), commit=True
    )
    return len(synced)


async def _on_device_down(runtime):
    global _last_alert_ts
    _down_macs.add(runtime.mac)
    now = time.time()
    if now - _last_alert_ts < _ALERT_THROTTLE_S:
        return
    _last_alert_ts = now
    lines = [(f"{client.display_mac(m)}", "失联") for m in sorted(_down_macs)]
    await notifier.notify_telegram_direct(
        notifier.format_system_event("瘦终端失联", lines),
        parse_mode="Markdown",
    )


async def _on_device_up(runtime):
    _down_macs.discard(runtime.mac)
    await notifier.notify_telegram_direct(
        notifier.format_system_event(
            f"瘦终端 {client.display_mac(runtime.mac)} 已恢复",
            [("状态", "在线")],
        ),
        parse_mode="Markdown",
    )


async def device_loop(manager, runtime):
    """每设备拉取循环:trigger(webhook)或 POLL_INTERVAL 兜底;连续失败合并告警。"""
    while True:
        try:
            if runtime.base_url:
                n = await poll_device(manager, runtime)
                if n:
                    log.info("设备 %s 拉取入库 %d 条新短信", runtime.mac, n)
                if runtime._alerted_down:
                    runtime._alerted_down = False
                    runtime.consecutive_fails = 0
                    events.publish({"type": "device", "device_mac": runtime.mac, "online": True})
                    await _on_device_up(runtime)
                runtime.consecutive_fails = 0
        except asyncio.CancelledError:
            raise
        except client.DeviceBusy:
            # 交互式 /api/poll 正占用该设备:本轮稍后再来
            runtime.pull_again = True
            await asyncio.sleep(0.5)
        except client.DeviceUnknown:
            pass  # 地址未知不算失联:安静等 webhook
        except Exception as e:
            runtime.consecutive_fails += 1
            log.warning("设备 %s 拉取失败(%d 连): %s", runtime.mac, runtime.consecutive_fails, e)
            if runtime.consecutive_fails == config.ALERT_CONSECUTIVE_FAILS and not runtime._alerted_down:
                runtime._alerted_down = True
                events.publish({"type": "device", "device_mac": runtime.mac, "online": False})
                await _on_device_down(runtime)

        # pull-merge:webhook 在拉取进行中到达 → 立即补一轮(不并发)
        if runtime.pull_again:
            runtime.pull_again = False
            continue

        # 等 webhook 触发;POLL_INTERVAL 内无 webhook 则兜底再拉一轮
        try:
            await asyncio.wait_for(runtime.trigger.wait(), timeout=config.POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass
        runtime.trigger.clear()
