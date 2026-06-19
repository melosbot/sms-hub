"""Runtime configuration routes."""
import contextlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.app import auth
from core.infra import config as app_config
from core.infra import db

log = logging.getLogger("hub")
router = APIRouter(dependencies=[Depends(auth.require_auth)])


def _merge_notify_channels(existing: list[dict], incoming: list[dict]) -> list[dict]:
    by_id = {str(ch.get("id", "")): ch for ch in existing or []}
    merged = []
    for raw in incoming or []:
        item = dict(raw)
        cid = str(item.get("id", ""))
        cfg = dict(item.get("config") or {})
        old_cfg = dict((by_id.get(cid) or {}).get("config") or {})
        if item.get("type") == "telegram" and not str(cfg.get("bot_token", "")).strip():
            cfg["bot_token"] = old_cfg.get("bot_token") or app_config.TG_BOT_TOKEN
        if item.get("type") in ("dingtalk", "feishu") and not str(cfg.get("secret", "")).strip():
            cfg["secret"] = old_cfg.get("secret", "")
        item["config"] = cfg
        merged.append(item)
    return merged


@router.get("/api/config")
async def show_config():
    """当前配置(全局)。通知渠道 token 不回传明文,只回传是否已设置。"""
    return {
        "device_token_tail": app_config.DEVICE_TOKEN[-4:] if app_config.DEVICE_TOKEN else "",
        "tg_manage_enabled": app_config.TG_MANAGE_ENABLED,
        "notify_channels": app_config.sanitized_notify_channels(),
        "admin_phone": app_config.ADMIN_PHONE,
        "default_send_sim_id": app_config.DEFAULT_SEND_SIM_ID,
        "blacklist": ",".join(app_config.BLACKLIST),
        "poll_interval": app_config.POLL_INTERVAL,
        "keepalive_interval_days": app_config.KEEPALIVE_INTERVAL_DAYS,
        "keepalive_ping_host": app_config.KEEPALIVE_PING_HOST,
        "tombstone_keep_days": app_config.TOMBSTONE_KEEP_DAYS,
        "message_keep_days": app_config.MESSAGE_KEEP_DAYS,
    }


class ConfigBody(BaseModel):
    notify_channels: list[dict] | None = None
    tg_manage_enabled: bool | None = None
    admin_phone: str | None = None
    default_send_sim_id: str | None = None
    blacklist: str | None = None
    poll_interval: int | None = None
    keepalive_interval_days: float | None = None
    keepalive_ping_host: str | None = None
    tombstone_keep_days: int | None = None
    message_keep_days: int | None = None


@router.post("/api/config")
async def update_config(body: ConfigBody):
    """保存运行时配置:写 kv 并即时生效,无需重启容器。"""
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return {"ok": True}
    raw = await db.get_kv("cfg", "{}")
    cur = {}
    with contextlib.suppress(Exception):
        cur = json.loads(raw or "{}")
    if "notify_channels" in changes:
        changes["notify_channels"] = _merge_notify_channels(
            cur.get("notify_channels") or app_config.NOTIFY_CHANNELS,
            changes["notify_channels"],
        )
    cur.update(changes)
    try:
        app_config.apply_overrides(cur)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"配置值无效: {e}")
    await db.set_kv("cfg", json.dumps(cur, ensure_ascii=False))
    log.info("配置已更新: %s", ", ".join(changes.keys()))
    return {"ok": True}
