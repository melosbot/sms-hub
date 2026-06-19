"""sms-hub FastAPI application entrypoint (v2:多设备 DeviceManager)。"""
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from core.notify import commands
from core.infra import config
from core.infra import db
from core.infra import logging_setup
from core.device import client as device_client
from core.device import manager as device_manager
from core.device import keepalive
from core.notify import notifier
from core.sms import sender
from core.app.routes import auth as auth_routes
from core.app.routes import config as config_routes
from core.app.routes import contacts
from core.app.routes import devices
from core.app.routes import events
from core.app.routes import messages
from core.app.routes import notify
from core.app.routes import send
from core.app.routes import status
from core.app.routes import webhook

logging_setup.configure()
log = logging.getLogger("hub")

# Web UI 由 web/ 构建产物(Vite + React + shadcn)提供,是 core 之外的独立前端项目。
# 优先 WEB_DIST 环境变量(Docker 用 /app/web/dist),回退到仓库 web/dist(开发用)。
WEB_DIST = Path(os.environ.get("WEB_DIST") or (Path(__file__).resolve().parents[1] / "web" / "dist"))
_tasks: list[asyncio.Task] = []


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await db.open_db()
    raw = await db.get_kv("cfg", "")
    if raw:
        with contextlib.suppress(Exception):
            config.apply_overrides(json.loads(raw))
    if config.IS_DEFAULT_PASS:
        log.warning("WEBUI_PASS 仍是默认密码 admin123,请在 .env 中修改!")
    # 构造 DeviceManager:探测 Hub 自身地址 + 加载启用设备 + 拉起每设备 poll 任务
    hub_self = device_client.compute_hub_self_addrs()
    config.HUB_SELF_ADDRS = hub_self
    mgr = device_manager.DeviceManager(hub_self=hub_self)
    device_manager.set_manager(mgr)
    await mgr.load()
    if not config.DEVICE_TOKEN:
        log.warning("DEVICE_TOKEN 未配置,设备不接入(仅 Web UI 可用)")
    _tasks.append(asyncio.create_task(sender.worker(), name="sender"))
    _tasks.append(asyncio.create_task(notifier.worker(), name="notifier"))
    _tasks.append(asyncio.create_task(commands.worker(), name="tg-commands"))
    _tasks.append(asyncio.create_task(keepalive.worker(), name="keepalive"))
    log.info("hub 已启动(v2),监听 :%d", config.LISTEN_PORT)
    yield
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    await device_client.close()
    await db.close_db()


app = FastAPI(title="sms-hub", lifespan=lifespan)


@app.middleware("http")
async def _log_http_errors(request: Request, call_next):
    """正常 2xx 静默;仅 ≥400 记一条与应用日志同格式的访问日志(替代 uvicorn access log)。"""
    response = await call_next(request)
    if response.status_code >= 400:
        peer = request.client.host if request.client else "?"
        log.warning("%s %s → %d(来自 %s)", request.method, request.url.path,
                    response.status_code, peer)
    return response


@app.get("/api/health")
async def health():
    return {"ok": True}


app.include_router(webhook.router)
app.include_router(auth_routes.router)
app.include_router(events.router)
app.include_router(messages.router)
app.include_router(contacts.router)
app.include_router(send.router)
app.include_router(status.router)
app.include_router(devices.router)
app.include_router(config_routes.router)
app.include_router(notify.router)

# 静态前端(Web UI)——必须在所有 API/hook 路由 *之后* 挂载,否则会遮蔽 /api/* 与 /hook/*。
# 前端用 ?tab= query 参数切换视图,StaticFiles(html=True) 即让 / 返回 index.html,
# 无需 SPA 路径兜底中间件。
if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
else:  # pragma: no cover - 开发期尚未构建前端时给出明确提示
    log.warning("前端构建产物不存在:%s。请在 web/ 下运行 `npm run build`。", WEB_DIST)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.LISTEN_PORT,
                log_config=logging_setup.build_log_config(), access_log=False)
