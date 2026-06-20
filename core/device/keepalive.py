"""保号定时任务(v2:每设备)。每隔 KEEPALIVE_INTERVAL_DAYS 天对每台启用设备
通过 /at 发起一次极小流量(MIPCALL=1 建连 → MPING → MIPCALL=0 省流量)。
每设备上次执行时间存 kv("keepalive_last_ts:<mac>"),hub 重启不重置。0 = 禁用。"""
import asyncio
import logging
import time

from core.infra import config
from core.infra import db
from core.device import client
from core.device import manager as device_manager
from core.notify import notifier

log = logging.getLogger("keepalive")

CHECK_INTERVAL_S = 3600  # 每小时检查一次是否到期


async def _run_for_device(runtime) -> str:
    """对单台设备执行保号流量,返回结果描述。"""
    def at_ok(r: dict) -> bool:
        return bool(r.get("ok")) and "OK" in (r.get("response") or "")

    r1 = await runtime.at("AT+MIPCALL=1,1", 10000)
    await asyncio.sleep(0.5)
    try:
        r2 = await runtime.at(
            f'AT+MPING="{config.KEEPALIVE_PING_HOST}",10,1', 15000
        )
        await asyncio.sleep(1.0)
    finally:
        await runtime.at("AT+MIPCALL=0,1", 5000)

    ok = at_ok(r1) and at_ok(r2)
    detail = (r2.get("response", "") or "").replace("\r", "").strip()
    if not ok:
        detail = (r1.get("response", "") or "").replace("\r", "").strip() + "\n" + detail
    title = "保号流量已发起" if ok else "保号流量执行异常"
    return notifier.format_system_event(
        f"{title} · {client.display_mac(runtime.mac)}",
        [("目标", config.KEEPALIVE_PING_HOST, True)],
        detail[:200],
    )


async def worker():
    while True:
        try:
            # 间隔可在 UI 运行时修改,每轮重读;0 = 禁用
            if config.KEEPALIVE_INTERVAL_DAYS > 0:
                interval_s = config.KEEPALIVE_INTERVAL_DAYS * 86400
                mgr = device_manager.get()
                for mac, runtime in list(mgr.runtimes.items()):
                    if not runtime.base_url:
                        continue
                    key = f"keepalive_last_ts:{mac}"
                    last = float(await db.get_kv(key, "0"))
                    if time.time() - last < interval_s:
                        continue
                    try:
                        result = await _run_for_device(runtime)
                        await db.set_kv(key, str(time.time()))
                        log.info("保号执行完成: %s", mac)
                        await notifier.notify_telegram_direct(result, parse_mode="Markdown")
                    except client.DeviceUnknown:
                        pass  # 设备离线,下个周期再试
                    except Exception as e:
                        log.warning("保号任务异常(%s): %s", mac, e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("保号任务异常: %s", e)

        await asyncio.sleep(CHECK_INTERVAL_S)
