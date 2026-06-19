"""Device webhook routes (v2)。

POST /hook/{token}:token 只认证不区分设备;body 必须带 mac。Hub 只做
token 校验、MAC/IMSI 规范化、状态快照保存和异步拉取排队,**不在请求内同步
执行设备 pull**(§6.2)。响应 {ok, pull_scheduled}。
"""
import contextlib
import logging
import secrets

from fastapi import APIRouter, HTTPException, Request

from core.infra import config
from core.device import manager as device_manager
from core.device.manager import WebhookError

log = logging.getLogger("hub")
router = APIRouter()


@router.post("/hook/{token}")
async def hook(token: str, request: Request):
    if not config.DEVICE_TOKEN or not secrets.compare_digest(token, config.DEVICE_TOKEN):
        raise HTTPException(status_code=404)
    body = {}
    with contextlib.suppress(Exception):
        body = await request.json()
    peer = request.client.host if request.client else ""
    try:
        return await device_manager.get().handle_webhook(body, peer)
    except WebhookError as e:
        # 只记诊断用字段(mac/event/ip/port);webhook body 可能携带 modem.imsi
        # (boot/hello/heartbeat),按"完整 IMSI 不入日志"铁律绝不整包打印。
        safe = {k: body.get(k) for k in ("mac", "event", "ip", "port") if k in body}
        log.warning("webhook 拒绝(400):%s | 字段=%s(来自 %s)", e, safe, peer)
        raise HTTPException(status_code=400, detail=str(e))
