"""Device & SIM management routes (v2:多瘦终端 + 多卡)。"""
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.app import auth
from core.infra import db
from core.device import client
from core.device import manager as device_manager
from core.device import manager

router = APIRouter(dependencies=[Depends(auth.require_auth)])


def _device_view(row, mgr) -> dict:
    live = manager.compute_liveness(row)
    rt = mgr.get_runtime(row["mac"])
    snap = json.loads(row["last_status_json"]) if row["last_status_json"] else {}
    now = time.time()
    return {
        "mac": row["mac"],
        "display_mac": client.display_mac(row["mac"]),
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "online": live["overall_online"],
        "heartbeat_online": live["heartbeat_online"],
        "data_plane_online": live["data_plane_online"],
        "last_heartbeat_ago_s": live["heartbeat_age_s"],
        "last_poll_ago_s": live["poll_age_s"],
        "last_hook_ago_s": int(now - row["last_hook_ts"]) if row["last_hook_ts"] else -1,
        "cursor": int(row["cursor"]),
        "busy": rt.busy_operation() if rt else "",
        "buffer": snap.get("buffer", {}),
        "modem": snap.get("modem", {}),
        "current_sim_id": row["current_sim_id"],
    }


def _sim_view(row) -> dict:
    return {
        "sim_id": row["sim_id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "current_device_mac": row["current_device_mac"],
        "identity_source": row["identity_source"],
        "msisdn": row["msisdn"],
        "imsi_tail": row["imsi_tail"],
        "iccid_tail": row["iccid_tail"],
        "operator": row["operator"],
    }


@router.get("/api/devices")
async def list_devices():
    mgr = device_manager.get()
    devices = [_device_view(r, mgr) for r in await db.list_all_devices()]
    sims = [_sim_view(r) for r in await db.list_sims()]
    return {"devices": devices, "sims": sims}


@router.get("/api/sims")
async def list_sims():
    return {"sims": [_sim_view(r) for r in await db.list_sims()]}


class PatchDeviceBody(BaseModel):
    name: str | None = None
    enabled: bool | None = None


@router.patch("/api/devices/{mac}")
async def patch_device(mac: str, body: PatchDeviceBody):
    mac_n = client.normalize_mac(mac)
    if not mac_n or not await db.get_device(mac_n):
        raise HTTPException(status_code=404, detail="设备不存在")
    mgr = device_manager.get()
    if body.name is not None:
        await db.set_device_name(mac_n, body.name, commit=True)
    if body.enabled is not None:
        await mgr.set_device_enabled(mac_n, body.enabled)
    row = await db.get_device(mac_n)
    return {"ok": True, "device": {
        "mac": mac_n, "name": row["name"], "enabled": bool(row["enabled"]),
    }}


class PatchSimBody(BaseModel):
    name: str | None = None
    enabled: bool | None = None


@router.patch("/api/sims/{sim_id}")
async def patch_sim(sim_id: str, body: PatchSimBody):
    sim = await db.get_sim(sim_id)
    if not sim:
        raise HTTPException(status_code=404, detail="卡片不存在")
    mgr = device_manager.get()
    if body.name is not None:
        await db.upsert_sim(sim_id, name=body.name, commit=True)
    if body.enabled is not None:
        await db.upsert_sim(sim_id, enabled=body.enabled, commit=True)
        # 禁用→启用:立即对其当前承载设备补拉(§6.4)
        if body.enabled and not sim["enabled"] and sim["current_device_mac"]:
            await mgr.trigger_pull(sim["current_device_mac"])
    sim = await db.get_sim(sim_id)
    return {"ok": True, "sim": {
        "sim_id": sim_id, "name": sim["name"], "enabled": bool(sim["enabled"]),
    }}
