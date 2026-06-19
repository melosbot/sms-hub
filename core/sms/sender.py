"""Persistent outbound SMS queue (v2:sim_id 业务归属 + 实际承载设备)。

发送时 resolve `sim_id → sims.current_device_mac → DeviceRuntime`,记录实际
承载设备与 device_msg_id。承载设备缺失/离线/禁用 → give_up(§9:不故障转移到其他卡)。
"""
import asyncio
import logging
import time

import httpx

from core.infra import db
from core.device import client
from core.device import manager as device_manager
from core.infra import events
from core.notify import notifier

log = logging.getLogger("sender")

RETRY_DELAYS_S = [5, 15, 60]
MAX_ATTEMPTS = len(RETRY_DELAYS_S) + 1
_wakeup = asyncio.Event()


async def enqueue_sms(to_phone: str, text: str, source: str = "webui", *,
                      sim_id: str, device_mac: str = "", commit: bool = True) -> int:
    """Create an outbound queue item and wake the sender worker."""
    parts = client.estimate_parts(text)
    cur = await db.db().execute(
        "INSERT INTO outbound(sim_id, device_mac, to_phone, text, status, parts,"
        " attempts, next_attempt_ts, source, last_error)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (sim_id, device_mac, to_phone, text, "pending", parts, 0, time.time(), source, ""),
    )
    if commit:
        await db.db().commit()
        _wakeup.set()
        events.publish({
            "type": "outbound", "sim_id": sim_id, "device_mac": device_mac,
            "id": cur.lastrowid,
        })
    return int(cur.lastrowid)


def wakeup():
    _wakeup.set()


async def _resolve_bearer(sim_id: str):
    """返回 (runtime, mac) 或 (None, None)。卡片禁用/设备缺失/离线均返回 None。"""
    sim = await db.get_sim(sim_id)
    if not sim or not sim["enabled"]:
        return None, ""
    mac = sim["current_device_mac"] or ""
    if not mac:
        return None, mac
    dev = await db.get_device(mac)
    if not dev or not dev["enabled"]:
        return None, mac
    runtime = device_manager.get().get_runtime(mac)
    if runtime is None or not runtime.base_url:
        return None, mac
    return runtime, mac


async def process_once(limit: int = 3) -> int:
    """Process due outbound jobs once. Returns number of jobs touched."""
    now = time.time()
    async with db.db().execute(
        "SELECT * FROM outbound WHERE status IN ('pending','retry')"
        " AND next_attempt_ts<=? ORDER BY id LIMIT ?",
        (now, limit),
    ) as cur:
        jobs = await cur.fetchall()

    for job in jobs:
        sim_id = job["sim_id"]
        runtime, mac = await _resolve_bearer(sim_id)
        attempts = int(job["attempts"]) + 1

        if runtime is None:
            # 卡片无当前承载设备或设备离线/禁用 → 放弃(不故障转移,§9)
            await db.db().execute(
                "UPDATE outbound SET status='give_up', attempts=?,"
                " last_error=?, next_attempt_ts=0 WHERE id=?",
                (attempts, "发送卡片无当前承载设备或设备离线/禁用", job["id"]),
            )
            await db.db().commit()
            events.publish({"type": "outbound", "sim_id": sim_id,
                            "device_mac": mac, "id": job["id"]})
            log.warning("出站 #%s 放弃: 卡片 %s 无可用设备", job["id"], sim_id)
            continue

        try:
            result = await runtime.send(job["to_phone"], job["text"])
            ok = bool(result.get("ok"))
            err = "" if ok else result.get("error", "发送失败")
        except client.DeviceBusy:
            # 设备忙:短延迟重排,不消耗尝试次数
            await db.db().execute(
                "UPDATE outbound SET next_attempt_ts=? WHERE id=?",
                (now + 2, job["id"]),
            )
            await db.db().commit()
            continue
        except (client.DeviceUnknown, httpx.TransportError, httpx.HTTPError) as e:
            result, ok, err = {}, False, str(e)[:200]
        except Exception as e:
            result, ok, err = {}, False, str(e)[:200]

        if ok:
            await db.db().execute(
                "UPDATE outbound SET status='sent', device_mac=?, device_msg_id=?,"
                " parts=?, attempts=?, last_error='', next_attempt_ts=0 WHERE id=?",
                (
                    mac,
                    result.get("device_msg_id") or result.get("id"),
                    result.get("parts", job["parts"]),
                    attempts,
                    job["id"],
                ),
            )
            log.info("出站短信 #%s 已送达(%s 段, 设备 %s)",
                     job["id"], result.get("parts", job["parts"]), mac)
            await _notify_result(job, True, result.get("parts", job["parts"]), "")
        else:
            if attempts >= MAX_ATTEMPTS:
                await db.db().execute(
                    "UPDATE outbound SET status='give_up', attempts=?,"
                    " last_error=?, next_attempt_ts=0 WHERE id=?",
                    (attempts, err, job["id"]),
                )
                log.warning("出站短信 #%s 放弃: %s", job["id"], err)
                await _notify_result(job, False, 0, err)
            else:
                delay = RETRY_DELAYS_S[min(attempts - 1, len(RETRY_DELAYS_S) - 1)]
                await db.db().execute(
                    "UPDATE outbound SET status='retry', attempts=?,"
                    " last_error=?, next_attempt_ts=? WHERE id=?",
                    (attempts, err, now + delay, job["id"]),
                )
                log.debug("出站短信 #%s 重试排队(第%d次): %s", job["id"], attempts, err)
        await db.db().commit()
        events.publish({"type": "outbound", "sim_id": sim_id,
                        "device_mac": mac, "id": job["id"]})

    return len(jobs)


async def _notify_result(job, ok: bool, parts: int, err: str):
    if job["source"] not in ("admin_relay", "telegram"):
        return
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    await notifier.notify_telegram_direct(
        notifier.format_outgoing(
            to_phone=job["to_phone"], text=str(job["text"]),
            ok=ok, ts=ts, parts=parts, error=err,
        ),
        parse_mode="Markdown",
    )


async def worker():
    while True:
        try:
            n = await process_once()
            timeout = 1.0 if n else 5.0
            try:
                await asyncio.wait_for(_wakeup.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            _wakeup.clear()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("出站队列异常")
            await asyncio.sleep(5)
