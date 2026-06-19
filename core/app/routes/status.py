"""Status, metrics, polling, and device debug routes (v2: per-sim / per-device)."""
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from core.app import auth
from core.app.simutil import resolve_sim_id_param, resolve_device_mac_for_sim
from core.infra import config
from core.infra import db
from core.device import client
from core.device import manager as device_manager
from core.device import manager
from core.device import poller
from core.infra import events

router = APIRouter()
authed_router = APIRouter(dependencies=[Depends(auth.require_auth)])


@authed_router.get("/api/status")
async def status(sim_id: str | None = None):
    """当前卡片及承载设备状态(§6.5 单卡结构)。读心跳缓存,不打扰设备。"""
    sim_id = await resolve_sim_id_param(sim_id)
    sim = await db.get_sim(sim_id)
    mac = sim["current_device_mac"] if sim else ""
    dev = await db.get_device(mac) if mac else None
    live = manager.compute_liveness(dev) if dev else {
        "heartbeat_online": False, "data_plane_online": False,
        "overall_online": False, "heartbeat_age_s": -1, "poll_age_s": -1,
    }
    mgr = device_manager.get()
    rt = mgr.get_runtime(mac) if mac else None
    device_obj = json.loads(dev["last_status_json"]) if dev and dev["last_status_json"] else {}
    async with db.db().execute(
        "SELECT COUNT(*) AS n FROM messages WHERE sim_id=?", (sim_id,)
    ) as cur:
        stored = (await cur.fetchone())["n"]
    now = time.time()
    return {
        "device": device_obj,
        "device_reachable": live["overall_online"],
        "overall_online": live["overall_online"],
        "heartbeat_online": live["heartbeat_online"],
        "data_plane_online": live["data_plane_online"],
        "device_status_age_s": live["heartbeat_age_s"],
        "hub": {
            "sim_id": sim_id,
            "sim_name": sim["name"] if sim else "",
            "device_mac": mac,
            "stored_total": stored,
            "cursor": int(dev["cursor"]) if dev else 0,
            "last_poll_ago_s": live["poll_age_s"],
            "last_hook_ago_s": int(now - dev["last_hook_ts"]) if dev and dev["last_hook_ts"] else -1,
            "poll_interval_s": config.POLL_INTERVAL,
            "device_busy": rt.busy_operation() if rt else "",
        },
    }


class SimBody(BaseModel):
    sim_id: str | None = None


async def _resolve_runtime(explicit_sim_id: str | None):
    """当前卡片 → 承载设备 mac → DeviceRuntime + 所属 DeviceManager。
    返回 (sim_id, mac, rt, mgr)。sim_id 不可推断 400;无承载设备 409;地址未知 409。"""
    sim_id = await resolve_sim_id_param(explicit_sim_id)
    mac = await resolve_device_mac_for_sim(sim_id)
    mgr = device_manager.get()
    rt = mgr.get_runtime(mac)
    if rt is None or not rt.base_url:
        raise HTTPException(status_code=409, detail="设备地址未知,等待设备上报")
    return sim_id, mac, rt, mgr


async def _device_call(rt, fn, *, pull_again_on_busy: bool = False):
    """执行一次设备 I/O 并统一异常映射:DeviceBusy→409(pull_again_on_busy 时置补拉标记)、
    DeviceUnknown→409 地址未知、其它→502 不可达。DeviceBusy 恒带消息,故 detail 用 str(e)。"""
    try:
        return await fn()
    except client.DeviceBusy as e:
        if pull_again_on_busy:
            rt.pull_again = True
        raise HTTPException(status_code=409, detail=str(e) or "设备忙")
    except client.DeviceUnknown:
        raise HTTPException(status_code=409, detail="设备地址未知,等待设备上报")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"设备不可达: {e}")


@authed_router.post("/api/poll")
async def force_poll(body: SimBody):
    """状态页"强制拉取":立即对该卡片承载设备拉一轮。"""
    sim_id, mac, rt, mgr = await _resolve_runtime(body.sim_id)
    n = await _device_call(rt, lambda: poller.poll_device(mgr, rt), pull_again_on_busy=True)
    return {"ok": True, "sim_id": sim_id, "device_mac": mac, "inserted": n}


@authed_router.post("/api/status/refresh")
async def status_refresh(body: SimBody):
    """主动向设备拉一次最新状态(include_status=1),刷新缓存。"""
    sim_id, mac, rt, _ = await _resolve_runtime(body.sim_id)
    dev = await db.get_device(mac)
    data = await _device_call(
        rt, lambda: rt.status_pull(after=int(dev["cursor"]) if dev else 0)
    )
    now = time.time()
    # pull 的 status 块只刷新 modem 身份;合并进现有快照,避免覆盖心跳写入的丰富遥测。
    new_block = data.get("status") or data
    existing = json.loads(dev["last_status_json"]) if dev and dev["last_status_json"] else {}
    merged = {
        **existing,
        **new_block,
        "modem": {**existing.get("modem", {}), **(new_block.get("modem") or {})},
    }
    await db.update_device_status_snapshot(mac, json.dumps(merged, ensure_ascii=False), now)
    rt.last_status_ts = now
    await db.update_device_timestamps(mac, last_status_ts=now, commit=True)
    events.publish({"type": "device", "device_mac": mac, "online": True})
    return {"ok": True, "sim_id": sim_id, "device_mac": mac, "age_s": 0}


@authed_router.post("/api/buffer/clear")
async def clear_buffer(body: SimBody):
    """手动排空设备缓冲:删除设备本地已同步到 Hub 的消息。
    设备缓冲默认保留作"Hub 刷机/丢库"兜底(近 50 条可重拉恢复),不自动排空。"""
    sim_id, mac, rt, _ = await _resolve_runtime(body.sim_id)
    n = await _device_call(rt, lambda: poller.clear_device_buffer(rt))
    return {"ok": True, "sim_id": sim_id, "device_mac": mac, "deleted": n}


def _metric_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")


@router.get("/metrics")
async def metrics():
    """Prometheus-style metrics,带 device_mac / sim_id label(§6.6)。LAN-only。"""
    mgr = device_manager.get()
    lines = [
        "# HELP sms_hub_device_reachable Device reachability snapshot.",
        "# TYPE sms_hub_device_reachable gauge",
    ]
    for d in await db.list_all_devices():
        live = manager.compute_liveness(d)
        rt = mgr.get_runtime(d["mac"])
        lbl = f'device_mac="{_metric_label(d["mac"])}",device_name="{_metric_label(d["name"])}"'
        lines.append(f"sms_hub_device_reachable{{{lbl}}} {1 if live['overall_online'] else 0}")
        lines.append(f"sms_hub_heartbeat_online{{{lbl}}} {1 if live['heartbeat_online'] else 0}")
        lines.append(f"sms_hub_data_plane_online{{{lbl}}} {1 if live['data_plane_online'] else 0}")
        lines.append(f"sms_hub_device_busy{{{lbl}}} {1 if (rt and rt.busy_operation()) else 0}")
    async with db.db().execute(
        "SELECT m.sim_id AS sim_id, s.name AS sim_name, COUNT(*) AS n"
        " FROM messages m LEFT JOIN sims s ON s.sim_id=m.sim_id"
        " GROUP BY m.sim_id"
    ) as cur:
        for r in await cur.fetchall():
            lbl = f'sim_id="{_metric_label(r["sim_id"])}",sim_name="{_metric_label(r["sim_name"] or "")}"'
            lines.append(f"sms_hub_messages_total{{{lbl}}} {r['n']}")
    async with db.db().execute(
        "SELECT status, COUNT(*) AS n FROM outbound GROUP BY status"
    ) as cur:
        for r in await cur.fetchall():
            lines.append(
                f'sms_hub_outbound_jobs{{status="{_metric_label(r["status"])}"}} {r["n"]}'
            )
    async with db.db().execute(
        "SELECT channel, status, COUNT(*) AS n FROM notify_jobs GROUP BY channel,status"
    ) as cur:
        for r in await cur.fetchall():
            lines.append(
                "sms_hub_notify_jobs"
                f'{{channel="{_metric_label(r["channel"])}",'
                f'status="{_metric_label(r["status"])}"}} {r["n"]}'
            )
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


class AtBody(BaseModel):
    sim_id: str | None = None
    cmd: str
    timeout_ms: int = 3000


@authed_router.post("/api/at")
async def at_proxy(body: AtBody):
    sim_id, mac, rt, _ = await _resolve_runtime(body.sim_id)
    return await _device_call(
        rt, lambda: rt.at(body.cmd, max(100, min(body.timeout_ms, 15000)), wait_busy=False)
    )


router.include_router(authed_router)
