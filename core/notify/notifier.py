"""通知:多通道推送 + SQLite 重试队列。"""
import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import re
import time
from urllib.parse import quote, urlparse

import httpx

from core.infra import config
from core.infra import db
from core.device import client
from core.device import manager as device_manager
from core.sms.extractor import extract_brand

log = logging.getLogger("notifier")

RETRY_BASE_S = 15
RETRY_MAX_S = 900
MAX_ATTEMPTS = 8

_wakeup = asyncio.Event()


def _retry_delay(attempts: int) -> float:
    return min(RETRY_BASE_S * (2 ** attempts), RETRY_MAX_S)


def _val(value) -> str:
    return str(value) if value not in (None, "") else "—"


def _field(label: str, value, *, code: bool = False) -> str:
    v = _val(value)
    # code=True 时值用反引号包成 code span(等宽、可点复制),用于目标/IP 等
    return f"{label}:`{v}`" if code else f"{label}:{_md(v)}"


# Telegram Markdown(legacy)需转义的字符。legacy 模式只需这几种,不像 MarkdownV2
# 要转义 . ! - ) 等几十种——短信正文里这些字符极常见,用 legacy 对送达最稳。
_MD_ESCAPE = re.compile(r"([\\*_`\[])")


def _md(value) -> str:
    """转义 Markdown 特殊字符,正文里的 * _ ` [ 不会破坏解析。"""
    s = "" if value is None else str(value)
    return _MD_ESCAPE.sub(r"\\\1", s)


# ── 推送模板占位符渲染 ──
# {sender} {code} {text} 等,支持别名/大小写不敏感/全角 ｛｝ 括号;未知占位符原样保留。
# 占位符名仅限字母数字下划线——避免把 webhook_json 模板里的 JSON 花括号误当占位符。
# 各通道 template 留空时走默认格式(向后兼容),见 _format_for_channel。
_PLACEHOLDERS = {
    "sender": "sender", "from": "sender",
    "text": "text", "fulltext": "text", "message": "text",
    "text_md": "text_md",
    "code": "code",
    "time": "received_at", "timestamp": "received_at", "received_at": "received_at",
    "id": "_lo_id", "msg_id": "_lo_id",
    "raw_id": "device_msg_id",
    "sender_name": "sender_name", "name": "sender_name",
    "sim_name": "sim_name", "sim_id": "sim_id",
    "device_name": "device_name", "device_mac": "device_mac",
}
_PLACEHOLDER_RE = re.compile(r"[{｛]\s*([A-Za-z0-9_]+)\s*[}｝]")


def _placeholder_value(key: str, ctx: dict) -> str:
    """按规范键取占位符原始值(未转义)。"""
    if key == "_lo_id":
        return str(ctx.get("msg_id") or ctx.get("id") or "")
    if key == "text_md":
        return _md(ctx.get("text", ""))
    if key == "code":
        return ctx.get("code") or ""
    if key == "sender_name":
        return ctx.get("sender_name") or ""
    v = ctx.get(key, "")
    return "" if v is None else str(v)


def _escape(value, kind: str) -> str:
    """按上下文转义占位符值:json=字符串内容(去外层引号,可嵌入 JSON 字符串);
    url=百分号编码;plain=原样(telegram/sms 自负 Markdown)。"""
    if kind == "json":
        return json.dumps(str(value), ensure_ascii=False)[1:-1]
    if kind == "url":
        return quote(str(value), safe="")
    return str(value)


def _render_template(template: str, ctx: dict, *, escape: str = "plain") -> str:
    """把模板里的占位符替换为 ctx 中的值;未知占位符原样保留。"""
    def repl(m):
        canon = _PLACEHOLDERS.get(m.group(1).strip().lower())
        if not canon:
            return m.group(0)
        return _escape(_placeholder_value(canon, ctx), escape)
    return _PLACEHOLDER_RE.sub(repl, template or "")


def format_system_event(title: str, fields: list[tuple] | None = None,
                        detail: str = "") -> str:
    """统一系统告警模板(Markdown):🔔 新告警 → 类别(+其他字段) → 内容。"""
    parts = [f"🔔 *{_md('新告警')}*"]
    lines = [f"类别:{_md(title)}"]
    for item in (fields or []):
        label, value = item[0], item[1]
        code = len(item) > 2 and item[2]   # 第 3 元 code 标记:值用反引号
        lines.append(_field(label, value, code=code))
    parts.append("\n".join(lines))
    if detail:
        parts.append(_md(detail))
    return "\n\n".join(parts)


async def tg_api(method: str, payload: dict, timeout: float = 15.0) -> dict:
    url = f"{config.TG_API_BASE}/bot{config.TG_BOT_TOKEN}/{method}"
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, json=payload)
        r.raise_for_status()
        return r.json()


async def _tg_api_with(channel: dict, method: str, payload: dict,
                       timeout: float = 15.0) -> dict:
    cfg = channel["config"]
    api_base = cfg.get("api_base", "https://api.telegram.org").rstrip("/")
    token = cfg.get("bot_token", "")
    url = f"{api_base}/bot{token}/{method}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(url, json=payload)
    except httpx.HTTPError as e:
        raise RuntimeError(_httpx_error_hint(e, api_base=api_base)) from e
    if r.is_error:
        raise RuntimeError(_telegram_http_error(r))
    return r.json()


def _telegram_http_error(r: httpx.Response) -> str:
    """Telegram Bot API 非 2xx:保留 description,方便区分 token/chat_id 问题。"""
    detail = ""
    with contextlib.suppress(Exception):
        data = r.json()
        if isinstance(data, dict):
            detail = str(data.get("description") or data.get("error") or "")
    if not detail:
        detail = (r.text or "").strip()
    suffix = f": {detail[:240]}" if detail else ""
    return f"Telegram API HTTP {r.status_code}{suffix}"


def _httpx_error_hint(e: httpx.HTTPError, *, api_base: str = "") -> str:
    raw = str(e)
    low = raw.lower()
    target = f" API Base={api_base}" if api_base else ""
    if "tls/ssl" in low or "_ssl.c" in low or "eof" in low or "ssl" in low:
        return (
            f"Telegram API TLS 连接被对端关闭{target}。通常是 Hub 到 Telegram API 的网络"
            "被拦截,或 API Base/反代的 HTTPS 配置不兼容。请确认 API Base 是 Bot API"
            "根地址(例如 https://api.telegram.org 或你的 HTTPS 反代根地址),并在 Hub"
            f"所在机器测试连通性。原始错误: {raw}"
        )
    if isinstance(e, httpx.ConnectTimeout):
        return f"Telegram API 连接超时{target}: {raw}"
    if isinstance(e, httpx.ReadTimeout):
        return f"Telegram API 响应超时{target}: {raw}"
    if isinstance(e, httpx.ConnectError):
        return f"Telegram API 连接失败{target}: {raw}"
    return f"Telegram API 请求失败{target}: {raw}"


def _format_delivery_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPError):
        return _httpx_error_hint(e)
    return str(e)[:500]


def format_incoming(m: dict) -> str:
    """新收件通知(Markdown):标题加粗(有验证码则挂右侧) → 发件人/时间 → 正文。"""
    title = f"📥 *{_md('新收件')}*"
    if m.get("code"):
        # 验证码挂在标题右侧(与发件 ·✅已送达 同款),反引号 code 可一点即复制
        title += f" · 🔐 `{m['code']}`"
    parts = [
        title, "",
        f"发件人:{_md(m['sender'])}",
        f"时间:{_md(m['received_at'])}",
        "", _md(m["text"]),
    ]
    return "\n".join(parts)


def format_outgoing(*, to_phone: str, text: str, ok: bool, ts: str,
                    parts: int = 0, error: str = "") -> str:
    """新发件通知(Markdown):与 format_incoming 同款排版,带送达/失败状态。"""
    badge = "✅ 已送达" if ok else "❌ 发送失败"
    lines = [
        f"📤 *{_md('新发件')}* · {_md(badge)}",
        "",
        f"收件人:{_md(to_phone)}",
        f"时间:{_md(ts)}",
    ]
    if ok and parts:
        lines.append(f"段数:{parts}")
    out = "\n".join(lines) + "\n\n" + _md(text)
    if not ok and error:
        out += "\n\n⚠️ " + _md(error)
    return out


async def enqueue(msg_id: int | None, channel: str, target: str, text: str,
                  *, commit: bool = True):
    """commit=False 时由调用方提交事务(poller 把消息与通知任务一起落盘)。"""
    await db.db().execute(
        "INSERT INTO notify_jobs(msg_id, channel, target, text, next_attempt_ts)"
        " VALUES (?,?,?,?,?)",
        (msg_id, channel, target, text, time.time()),
    )
    if commit:
        await db.db().commit()
    _wakeup.set()


async def enqueue_for_message(msg_id: int, m: dict, *, commit: bool = True):
    """新短信入库后调用:按配置投递到各通道。"""
    # 查一次发件人备注名 + 卡片/设备名,注入渲染上下文(浅拷贝,不污染 poller 的 row)
    alias = await db.fetch_sender_alias(m.get("sender", ""))
    brand = extract_brand(m.get("text", ""))
    sim = await db.get_sim(m.get("sim_id", ""))
    dev = await db.get_device(m.get("device_mac", ""))
    ctx = {
        **m,
        "sender_name": alias or brand,
        "brand": brand,
        "msg_id": msg_id,
        "sim_name": (sim["name"] if sim and sim["name"] else (m.get("sim_id") or "")),
        "device_name": dev["name"] if dev else "",
    }
    for channel in _enabled_channels():
        target = _render_target(channel, ctx)
        if not target:
            continue
        if channel["type"] == "sms_forward" and m["sender"] == _target_for(channel):
            continue
        await enqueue(
            msg_id,
            channel["id"],
            target,
            _format_for_channel(channel, ctx),
            commit=commit,
        )


async def notify_telegram_direct(text: str, *, parse_mode: str | None = None):
    """系统告警类即时通知到 Telegram,不走队列。失败只记日志。
    parse_mode=None 走纯文本(系统告警);新收件/新发件传 'Markdown'。"""
    for channel in _enabled_channels("telegram"):
        try:
            await _send_telegram_channel(channel, _target_for(channel), text,
                                         parse_mode=parse_mode)
        except Exception as e:
            log.warning("告警通知发送失败: %s", e)


def _enabled_channels(typ: str | None = None) -> list[dict]:
    out = []
    for channel in config.NOTIFY_CHANNELS:
        if typ and channel["type"] != typ:
            continue
        if not channel.get("enabled"):
            continue
        if _target_for(channel):
            out.append(channel)
    return out


def _find_channel(channel_id: str) -> dict | None:
    for channel in config.NOTIFY_CHANNELS:
        if channel["id"] == channel_id:
            return channel
    return None


def _target_for(channel: dict) -> str:
    cfg = channel.get("config") or {}
    typ = channel.get("type")
    if typ == "telegram":
        chat_id = str(cfg.get("chat_id", "")).strip()
        token = str(cfg.get("bot_token", "")).strip()
        return chat_id if chat_id and token else ""
    if typ == "sms_forward":
        return str(cfg.get("to", "")).strip()
    if typ in ("webhook_json", "webhook_get", "dingtalk", "feishu", "bark"):
        url = str(cfg.get("url", "")).strip()
        parsed = urlparse(url)
        return url if parsed.scheme in ("http", "https") and parsed.netloc else ""
    if typ == "pushplus":
        # token 必填(放 body);url 留空用官方默认。
        if not str(cfg.get("token", "")).strip():
            return ""
        url = str(cfg.get("url", "")).strip()
        return url if url else "http://www.pushplus.plus/send"
    if typ == "serverchan":
        url = str(cfg.get("url", "")).strip()
        if url:
            return url
        sendkey = str(cfg.get("sendkey", "")).strip()
        return f"https://sctapi.ftqq.com/{sendkey}.send" if sendkey else ""
    if typ == "gotify":
        url = str(cfg.get("url", "")).strip().rstrip("/")
        token = str(cfg.get("token", "")).strip()
        if not url or not token:
            return ""
        return f"{url}/message?token={token}"
    return ""


def _render_target(channel: dict, ctx: dict) -> str:
    """通道投递目标:webhook 的 url 支持占位符(值做 URL 编码),其余原样。
    target 在 enqueue 时渲染并落盘,worker 只拿到渲染后的 url。"""
    target = _target_for(channel)
    if channel["type"] in ("webhook_json", "webhook_get"):
        url = (channel.get("config") or {}).get("url", "")
        return _render_template(url, ctx, escape="url") if url else target
    return target


def _format_for_channel(channel: dict, m: dict) -> str:
    """按通道类型格式化推送内容。config.template 非空则用占位符模板渲染,
    留空走各通道默认格式(向后兼容)。webhook_get 不读 template——其 url
    本身即模板,由 _render_target 处理。"""
    typ = channel["type"]
    cfg = channel.get("config") or {}
    tpl = str(cfg.get("template") or "").strip()
    if typ == "telegram":
        return _render_template(tpl, m) if tpl else format_incoming(m)
    if typ == "sms_forward":
        return _render_template(tpl, m) if tpl else f"[{m['sender']}] {m['text']}"
    if typ == "webhook_json" and tpl:
        return _render_template(tpl, m, escape="json")
    if typ in ("dingtalk", "feishu"):
        # 钉钉/飞书文本消息;content 走 template(默认: 发件人 + 正文)。
        content = _render_template(tpl, m) if tpl else f"[{m.get('sender', '')}] {m.get('text', '')}"
        if typ == "dingtalk":
            return json.dumps({"msgtype": "text", "text": {"content": content}},
                              ensure_ascii=False)
        return json.dumps({"msg_type": "text", "content": {"text": content}},
                          ensure_ascii=False)
    if typ == "bark":
        body = _render_template(tpl, m) if tpl else m.get("text", "")
        return json.dumps({"title": m.get("sender", ""), "body": body}, ensure_ascii=False)
    if typ == "pushplus":
        channel = str(cfg.get("channel", "")).strip() or "wechat"
        content = (_render_template(tpl, m) if tpl
                   else f"<b>发送者:</b> {m.get('sender', '')}<br><b>内容:</b><br>{m.get('text', '')}")
        return json.dumps({"token": str(cfg.get("token", "")).strip(),
                           "title": f"短信来自: {m.get('sender', '')}",
                           "content": content, "channel": channel}, ensure_ascii=False)
    if typ == "serverchan":
        # form-urlencoded:返回 {"title","desp"} JSON,_attempt 以 data= 发送。
        desp = (_render_template(tpl, m) if tpl
                else f"**发送者:** {m.get('sender', '')}\n\n**内容:**\n\n{m.get('text', '')}")
        return json.dumps({"title": f"短信来自: {m.get('sender', '')}", "desp": desp},
                          ensure_ascii=False)
    if typ == "gotify":
        message = (_render_template(tpl, m) if tpl
                   else f"{m.get('text', '')}\n\n时间: {m.get('received_at', '')}")
        return json.dumps({"title": f"短信来自: {m.get('sender', '')}",
                           "message": message, "priority": 5}, ensure_ascii=False)
    # webhook_json(无模板) 与 webhook_get 的 body 都用固定 JSON
    return json.dumps({
        "sender": m.get("sender", ""),
        "message": m.get("text", ""),
        "received_at": m.get("received_at", ""),
        "code": m.get("code", ""),
    }, ensure_ascii=False)


async def _send_telegram_channel(channel: dict, target: str, text: str,
                                 parse_mode: str | None = None):
    payload = {"chat_id": target, "text": text, "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    await _tg_api_with(channel, "sendMessage", payload)


def _json_payload(text: str) -> dict:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"text": text}


def _dingtalk_sign(secret: str, ts_ms: int) -> str:
    """钉钉机器人加签:base64(hmac_sha256(key=secret, msg=f"{ts}\n{secret}")) → URL 编码。"""
    string_to_sign = f"{ts_ms}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                      hashlib.sha256).digest()
    return quote(base64.b64encode(digest).decode("utf-8"), safe="")


def _feishu_sign(secret: str, ts_s: int) -> str:
    """飞书机器人加签:base64(hmac_sha256(key=f"{ts}\n{secret}", msg=secret))。

    注意与钉钉相反:飞书 key 是时间戳拼接串,msg 是 secret。"""
    string_to_sign = f"{ts_s}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), secret.encode("utf-8"),
                      hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


async def _send_signed_bot(channel: dict, job) -> tuple[bool, str]:
    """钉钉/飞书加签机器人:按 secret 算签名后 POST webhook。secret 留空则不加签。"""
    cfg = channel.get("config") or {}
    secret = str(cfg.get("secret", "")).strip()
    typ = channel["type"]
    url = job["target"]
    body = _json_payload(job["text"])
    try:
        if typ == "dingtalk":
            if secret:
                ts = int(time.time() * 1000)
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}timestamp={ts}&sign={_dingtalk_sign(secret, ts)}"
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(url, json=body)
                r.raise_for_status()
        else:  # feishu
            if secret:
                ts = int(time.time())
                body = {**body, "timestamp": str(ts), "sign": _feishu_sign(secret, ts)}
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(url, json=body)
                r.raise_for_status()
    except Exception as e:
        return False, _format_delivery_error(e)
    return True, ""


async def _attempt(job) -> tuple[bool, str]:
    try:
        channel = _find_channel(job["channel"])
        if not channel:
            return False, f"未知通道 {job['channel']}"
        if not channel.get("enabled"):
            return False, f"通道未启用 {job['channel']}"
        if channel["type"] == "telegram":
            # telegram 队列里的文本来自 format_incoming(Markdown),按 Markdown 发
            cfg = channel.get("config") or {}
            target = job["target"] or str(cfg.get("chat_id", "")).strip()
            if not target or not str(cfg.get("bot_token", "")).strip():
                return False, f"通道未配置 {job['channel']}"
            await _send_telegram_channel(
                channel,
                target,
                job["text"],
                parse_mode="Markdown",
            )
            return True, ""
        if channel["type"] == "sms_forward":
            # 短信转发走全局默认发送卡片(§9)
            sim_id = config.DEFAULT_SEND_SIM_ID
            sim = await db.get_sim(sim_id) if sim_id else None
            mac = sim["current_device_mac"] if sim else ""
            runtime = device_manager.get().get_runtime(mac) if mac else None
            if not runtime or not runtime.base_url:
                return False, "转发卡片无可用设备"
            try:
                result = await runtime.send(job["target"], job["text"])
            except client.DeviceBusy:
                return False, "设备忙"
            return bool(result.get("ok")), result.get("error", "")
        if channel["type"] == "webhook_json":
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(job["target"], json=_json_payload(job["text"]))
                r.raise_for_status()
            return True, ""
        if channel["type"] == "webhook_get":
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(job["target"], params=_json_payload(job["text"]))
                r.raise_for_status()
            return True, ""
        if channel["type"] in ("dingtalk", "feishu"):
            return await _send_signed_bot(channel, job)
        if channel["type"] in ("bark", "pushplus", "gotify"):
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(job["target"], json=_json_payload(job["text"]))
                r.raise_for_status()
            return True, ""
        if channel["type"] == "serverchan":
            # Server酱用 form-urlencoded(_format 返回的 JSON 当 form data 发)。
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(job["target"], data=_json_payload(job["text"]))
                r.raise_for_status()
            return True, ""
        return False, f"未知通道类型 {channel['type']}"
    except Exception as e:
        return False, _format_delivery_error(e)


async def test_channel(channel_id: str) -> dict:
    """测试发送一条通知到指定通道(不入队、不写 notify_jobs)。返回 {ok, error}。
    复用 _attempt 投递逻辑;webhook 占位符不渲染(用原始 URL,仅验可达性)。"""
    channel = _find_channel(channel_id)
    if not channel:
        return {"ok": False, "error": "未知通道"}
    target = _target_for(channel)
    if not target:
        return {"ok": False, "error": "通道未配置目标(token/号码/URL 缺失)"}
    job = {"channel": channel_id, "target": target, "text": "sms-hub 测试通知"}
    ok, err = await _attempt(job)
    return {"ok": ok, "error": err}


async def worker():
    """通知重试循环:指数退避 15s→…→15min,最多 8 次。"""
    while True:
        try:
            now = time.time()
            async with db.db().execute(
                "SELECT * FROM notify_jobs WHERE status IN ('pending','retry')"
                " AND next_attempt_ts<=? ORDER BY id LIMIT 5",
                (now,),
            ) as cur:
                jobs = await cur.fetchall()

            for job in jobs:
                ok, err = await _attempt(job)
                if ok:
                    await db.db().execute(
                        "UPDATE notify_jobs SET status='ok', last_error='' WHERE id=?",
                        (job["id"],),
                    )
                    log.debug("通知送达 #%s 通道 %s", job["id"], job["channel"])
                else:
                    attempts = job["attempts"] + 1
                    if attempts >= MAX_ATTEMPTS:
                        await db.db().execute(
                            "UPDATE notify_jobs SET status='give_up', attempts=?,"
                            " last_error=? WHERE id=?",
                            (attempts, err, job["id"]),
                        )
                        log.warning("通知放弃 #%s: %s", job["id"], err)
                        # 走到 give_up 说明通道坏了很久,补一条告警
                        # (telegram 通道自身坏掉时这条也发不出,只会留日志)
                        await notify_telegram_direct(
                            format_system_event(
                                "通知最终失败",
                                [
                                    ("通道", job["channel"]),
                                    ("重试次数", f"{attempts} 次"),
                                ],
                                err,
                            ),
                            parse_mode="Markdown",
                        )
                    else:
                        await db.db().execute(
                            "UPDATE notify_jobs SET status='retry', attempts=?,"
                            " next_attempt_ts=?, last_error=? WHERE id=?",
                            (attempts, now + _retry_delay(attempts), err, job["id"]),
                        )
                        log.debug("通知重试 #%s(第%d次): %s", job["id"], attempts, err)
                await db.db().commit()

            # 有任务刚处理过就快轮,否则等唤醒/兜底 5s
            try:
                await asyncio.wait_for(_wakeup.wait(), timeout=1.0 if jobs else 5.0)
            except asyncio.TimeoutError:
                pass
            _wakeup.clear()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("通知循环异常")
            await asyncio.sleep(5)
