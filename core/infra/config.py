"""从环境变量读取配置。所有变量见仓库根目录 .env.example。"""
import os
import secrets
from pathlib import Path
from copy import deepcopy

from core.sms import phone

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "sms.db"

# 设备 token(与固件 config.h 的 API_TOKEN 一致)。
# 设备地址无需配置:设备会通过 webhook 上报自己的 IP,hub 学习并持久化。
# DEVICE_URL 可选,仅作为初始提示/webhook 不可用时的手工兜底。
DEVICE_URL = os.getenv("DEVICE_URL", "").rstrip("/")
DEVICE_TOKEN = os.getenv("DEVICE_TOKEN", "").strip() or (
    DEVICE_URL.rsplit("/", 1)[-1] if DEVICE_URL else ""
)

# 日志级别(默认 INFO;排障设 DEBUG)。应用与 uvicorn 共用,见 infra/logging_setup.py。
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))
ALERT_CONSECUTIVE_FAILS = int(os.getenv("ALERT_CONSECUTIVE_FAILS", "3"))

# v2 多设备:全局共享 DEVICE_TOKEN,每台瘦终端用 MAC 区分。
# POLL_INTERVAL 仍是每设备 poll 循环的兜底等待(生产默认 300s,§7.2;demo 可设 5s)。
# 跨设备并发 I/O 池上限(§7.2),超出排队;单设备内部仍串行化。
MAX_DEVICE_IO_CONCURRENCY = max(1, int(os.getenv("MAX_DEVICE_IO_CONCURRENCY", "4")))
# 在线判定下限(§7.2):heartbeat 与 data-plane 各自的新鲜度地板值(秒)。
# 在线判定下限(§7.2):heartbeat 与 data-plane 新鲜度地板。生产用默认;
# demo 可经 env 调小以快速观察离线切换(不影响生产默认值)。
HEARTBEAT_ONLINE_FLOOR_S = int(os.getenv("HEARTBEAT_ONLINE_FLOOR_S", "150"))
DATA_PLANE_ONLINE_FLOOR_S = int(os.getenv("DATA_PLANE_ONLINE_FLOOR_S", "630"))
# Hub 自身监听地址集合(SSRF 防护用),lifespan 启动时探测填充。
HUB_SELF_ADDRS: set[str] = set()
# 开发逃生:demo 栈 Hub 与 mock 同机时放行 loopback 设备地址。生产保持关闭。
ALLOW_LOOPBACK_DEVICE = os.getenv("ALLOW_LOOPBACK_DEVICE", "").strip().lower() in (
    "1", "true", "yes", "on"
)
# 管理员代发默认发送卡片(全局唯一,§9);未配置/不可用时管理员代发直接失败。
DEFAULT_SEND_SIM_ID = os.getenv("DEFAULT_SEND_SIM_ID", "").strip()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
TG_API_BASE = os.getenv("TG_API_BASE", "https://api.telegram.org").rstrip("/")
TG_ENABLED = bool(TG_BOT_TOKEN and TG_CHAT_ID)
TG_MANAGE_ENABLED = os.getenv("TG_MANAGE_ENABLED", "1") not in ("0", "false", "no")

FORWARD_SMS_TO = phone.canonicalize(os.getenv("FORWARD_SMS_TO", ""))
ADMIN_PHONE = phone.canonicalize(os.getenv("ADMIN_PHONE", ""))
# 黑名单:逗号分隔,支持完整号码与前缀(1069 即前缀)
BLACKLIST = [x.strip() for x in os.getenv("BLACKLIST", "").split(",") if x.strip()]

# 保号:每 N 天发起一次极小流量(0=禁用)
KEEPALIVE_INTERVAL_DAYS = float(os.getenv("KEEPALIVE_INTERVAL_DAYS", "0"))
KEEPALIVE_PING_HOST = os.getenv("KEEPALIVE_PING_HOST", "119.29.29.29")
TOMBSTONE_KEEP_DAYS = int(os.getenv("TOMBSTONE_KEEP_DAYS", "30"))
MESSAGE_KEEP_DAYS = int(os.getenv("MESSAGE_KEEP_DAYS", "0"))

WEBUI_USER = os.getenv("WEBUI_USER", "admin")
_DEFAULT_PASS = "admin123"
WEBUI_PASS = os.getenv("WEBUI_PASS", _DEFAULT_PASS)
IS_DEFAULT_PASS = WEBUI_PASS == _DEFAULT_PASS

LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8025"))

# 登录令牌签名密钥:未配置时生成一个并落盘,重启后会话依然有效
_secret_file = DATA_DIR / ".jwt_secret"
JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET:
    if _secret_file.exists():
        JWT_SECRET = _secret_file.read_text().strip()
    else:
        JWT_SECRET = secrets.token_hex(32)
        _secret_file.write_text(JWT_SECRET)
        _secret_file.chmod(0o600)

TOKEN_TTL_S = 30 * 86400  # 登录有效期 30 天


def _channel_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")


def _normalize_channel(raw: dict) -> dict | None:
    typ = str(raw.get("type", "")).strip()
    if typ not in ("telegram", "sms_forward", "webhook_json", "webhook_get",
                   "dingtalk", "feishu", "bark", "pushplus", "serverchan", "gotify"):
        return None
    cid = str(raw.get("id") or typ).strip()
    if not cid:
        cid = typ
    cfg = dict(raw.get("config") or {})
    out = {
        "id": cid,
        "type": typ,
        "name": str(raw.get("name") or cid).strip() or cid,
        "enabled": _channel_bool(raw.get("enabled", False)),
        "config": {},
    }
    if typ == "telegram":
        token = str(cfg.get("bot_token", "")).strip()
        chat_id = str(cfg.get("chat_id", "")).strip()
        api_base = str(cfg.get("api_base", "")).strip().rstrip("/") or "https://api.telegram.org"
        out["config"] = {"bot_token": token, "chat_id": chat_id, "api_base": api_base}
    elif typ == "sms_forward":
        out["config"] = {"to": phone.canonicalize(cfg.get("to", ""))}
    elif typ in ("webhook_json", "webhook_get"):
        out["config"] = {"url": str(cfg.get("url", "")).strip()}
    elif typ in ("dingtalk", "feishu"):
        # 加签机器人:webhook url + 加签 secret(可选,留空则不加签)。
        out["config"] = {
            "url": str(cfg.get("url", "")).strip(),
            "secret": str(cfg.get("secret", "")).strip(),
        }
    elif typ == "bark":
        out["config"] = {"url": str(cfg.get("url", "")).strip()}
    elif typ == "pushplus":
        out["config"] = {
            "token": str(cfg.get("token", "")).strip(),
            "channel": str(cfg.get("channel", "")).strip(),
        }
    elif typ == "serverchan":
        out["config"] = {"sendkey": str(cfg.get("sendkey", "")).strip()}
    elif typ == "gotify":
        out["config"] = {
            "url": str(cfg.get("url", "")).strip().rstrip("/"),
            "token": str(cfg.get("token", "")).strip(),
        }
    # 推送文案模板(占位符 {sender} {code} {text} 等);各类型统一保留,
    # 是否使用由 notifier 渲染层决定(webhook_get 不用——其 config.url 本身即模板)。
    out["config"]["template"] = str(cfg.get("template") or "").strip()
    return out


def _default_notify_channels() -> list[dict]:
    channels = [
        {
            "id": "telegram",
            "type": "telegram",
            "name": "Telegram",
            "enabled": bool(TG_BOT_TOKEN and TG_CHAT_ID),
            "config": {
                "bot_token": TG_BOT_TOKEN,
                "chat_id": TG_CHAT_ID,
                "api_base": TG_API_BASE,
            },
        },
        {
            "id": "sms_forward",
            "type": "sms_forward",
            "name": "短信转发",
            "enabled": bool(FORWARD_SMS_TO),
            "config": {"to": FORWARD_SMS_TO},
        },
        {
            "id": "webhook_json",
            "type": "webhook_json",
            "name": "POST JSON",
            "enabled": False,
            "config": {"url": ""},
        },
        {
            "id": "webhook_get",
            "type": "webhook_get",
            "name": "GET 请求",
            "enabled": False,
            "config": {"url": ""},
        },
        {
            "id": "dingtalk",
            "type": "dingtalk",
            "name": "钉钉机器人",
            "enabled": False,
            "config": {"url": "", "secret": ""},
        },
        {
            "id": "feishu",
            "type": "feishu",
            "name": "飞书机器人",
            "enabled": False,
            "config": {"url": "", "secret": ""},
        },
    ]
    return [c for c in (_normalize_channel(ch) for ch in channels) if c]


NOTIFY_CHANNELS = _default_notify_channels()


# ── 运行时可改配置 ──
# 以上 env 值只是初始默认;下列键可在 Web UI 修改,存 kv("cfg"),即时生效。
def _recompute():
    global TG_ENABLED, TG_BOT_TOKEN, TG_CHAT_ID, TG_API_BASE, FORWARD_SMS_TO
    tg = next((c for c in NOTIFY_CHANNELS if c["type"] == "telegram"), None)
    if tg:
        TG_BOT_TOKEN = str(tg["config"].get("bot_token", "")).strip()
        TG_CHAT_ID = str(tg["config"].get("chat_id", "")).strip()
        TG_API_BASE = str(tg["config"].get("api_base", "https://api.telegram.org")).strip().rstrip("/")
    else:
        TG_BOT_TOKEN = ""
        TG_CHAT_ID = ""
        TG_API_BASE = "https://api.telegram.org"
    fwd = next((c for c in NOTIFY_CHANNELS if c["type"] == "sms_forward"), None)
    if fwd:
        FORWARD_SMS_TO = phone.canonicalize(fwd["config"].get("to", ""))
    else:
        FORWARD_SMS_TO = ""
    TG_ENABLED = bool(TG_BOT_TOKEN and TG_CHAT_ID and (tg or {}).get("enabled", True))


def apply_overrides(d: dict):
    """把 kv 里保存的覆盖值应用到模块属性(启动时与每次保存后调用)。"""
    global ADMIN_PHONE, BLACKLIST
    global POLL_INTERVAL, ALERT_CONSECUTIVE_FAILS
    global KEEPALIVE_INTERVAL_DAYS, KEEPALIVE_PING_HOST
    global TG_MANAGE_ENABLED
    global TOMBSTONE_KEEP_DAYS
    global MESSAGE_KEEP_DAYS
    global NOTIFY_CHANNELS
    global DEFAULT_SEND_SIM_ID

    if "notify_channels" in d:
        channels = []
        for raw in d.get("notify_channels") or []:
            ch = _normalize_channel(raw)
            if ch:
                channels.append(ch)
        if channels:
            NOTIFY_CHANNELS = channels
    if "tg_manage_enabled" in d:
        TG_MANAGE_ENABLED = bool(d["tg_manage_enabled"])
    if "admin_phone" in d:
        ADMIN_PHONE = phone.canonicalize(d["admin_phone"])
    if "blacklist" in d:
        BLACKLIST = [x.strip() for x in str(d["blacklist"]).split(",") if x.strip()]
    if "poll_interval" in d:
        POLL_INTERVAL = max(5, int(d["poll_interval"]))
    if "alert_consecutive_fails" in d:
        ALERT_CONSECUTIVE_FAILS = max(1, int(d["alert_consecutive_fails"]))
    if "keepalive_interval_days" in d:
        KEEPALIVE_INTERVAL_DAYS = max(0.0, float(d["keepalive_interval_days"]))
    if "keepalive_ping_host" in d and str(d["keepalive_ping_host"]).strip():
        KEEPALIVE_PING_HOST = str(d["keepalive_ping_host"]).strip()
    if "tombstone_keep_days" in d:
        TOMBSTONE_KEEP_DAYS = max(1, int(d["tombstone_keep_days"]))
    if "message_keep_days" in d:
        MESSAGE_KEEP_DAYS = max(0, int(d["message_keep_days"]))
    if "default_send_sim_id" in d:
        DEFAULT_SEND_SIM_ID = str(d["default_send_sim_id"]).strip()
    _recompute()


def sanitized_notify_channels() -> list[dict]:
    out = []
    for ch in NOTIFY_CHANNELS:
        item = deepcopy(ch)
        if item["type"] == "telegram":
            token = item["config"].get("bot_token", "")
            item["config"]["bot_token"] = ""
            item["config"]["bot_token_set"] = bool(token)
        elif item["type"] in ("dingtalk", "feishu"):
            secret = item["config"].get("secret", "")
            item["config"]["secret"] = ""
            item["config"]["secret_set"] = bool(secret)
        out.append(item)
    return out
