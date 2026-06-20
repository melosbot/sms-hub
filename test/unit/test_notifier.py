"""Telegram 格式化测试:新收件/新发件/系统告警统一走 Markdown。"""

import asyncio
import json

import httpx

from core.notify import notifier


def _telegram_channel(*, enabled=True, token="tok", chat_id="42"):
    return {
        "id": "telegram",
        "type": "telegram",
        "name": "Telegram",
        "enabled": enabled,
        "config": {
            "bot_token": token,
            "chat_id": chat_id,
            "api_base": "https://api.telegram.org",
        },
    }


def test_format_incoming_markdown_layout():
    out = notifier.format_incoming({
        "sender": "13800138000",
        "received_at": "2026-06-14 10:00",
        "code": "114514",
        "text": "您的验证码是 114514",
    })

    # 验证码独立一行，反引号 code 可一点即复制
    assert out.startswith("📥 *新短信*\n发件人：*13800138000*")
    assert "验证码：`114514`" in out
    assert "时　间：2026-06-14 10:00" in out
    assert out.endswith("您的验证码是 114514")
    # Markdown 模式不应出现 HTML 标签
    assert "<" not in out and ">" not in out


def test_format_incoming_escapes_markdown_specials():
    # 正文里的 * _ ` [ 不能破坏解析
    out = notifier.format_incoming({
        "sender": "10086",
        "received_at": "2026-06-14 10:00",
        "text": "a*b_c`d[e",
    })
    assert "a\\*b\\_c\\`d\\[e" in out


def test_format_incoming_without_code():
    out = notifier.format_incoming({
        "sender": "10086",
        "received_at": "2026-06-14 10:00",
        "text": "欠费提醒",
    })
    # 无验证码:标题后直接跟发件人行
    assert "🔐" not in out
    assert out.startswith("📥 *新短信*\n发件人：*10086*")
    assert out.endswith("欠费提醒")


def test_format_outgoing_markdown_ok_and_fail():
    ok = notifier.format_outgoing(
        to_phone="13800138000", text="你的验证码 1234",
        ok=True, ts="2026-06-14 10:00", parts=2,
    )
    assert ok.startswith("📤 *新发件* · ✅ 已送达\n\n")
    assert "收件人:13800138000" in ok
    assert "段数:2" in ok
    assert ok.endswith("你的验证码 1234")

    fail = notifier.format_outgoing(
        to_phone="13800138000", text="hi", ok=False, ts="t", error="device offline",
    )
    assert "❌ 发送失败" in fail
    assert fail.endswith("⚠️ device offline")


def test_format_system_event_markdown_sections():
    out = notifier.format_system_event(
        "瘦终端失联",
        [("连续失败", "3 次"), ("地址", "http://x", True)],
        "All connection attempts failed",
    )

    # 🔔 新告警 标题;原 title 降为「类别」字段
    assert out.startswith("🔔 *新告警*\n\n类别:瘦终端失联\n")
    # 字段中文冒号
    assert "连续失败:3 次" in out
    # 第 3 元 code 标记生效:值用反引号包成 code
    assert "地址:`http://x`" in out
    # 详情作为内容,转义后原样(无特殊字符)
    assert out.endswith("All connection attempts failed")
    assert "<" not in out and ">" not in out


def test_format_system_event_blank_value_shows_dash():
    out = notifier.format_system_event("标题", [("字段", "")])
    # 空值显示 —,字段用中文冒号
    assert "字段:—" in out


def test_notify_telegram_direct_passes_text_through(monkeypatch):
    calls = []

    async def fake_send(channel, target, text, parse_mode=None):
        calls.append((target, text))

    monkeypatch.setattr(notifier.config, "NOTIFY_CHANNELS", [_telegram_channel()])
    monkeypatch.setattr(notifier, "_send_telegram_channel", fake_send)

    asyncio.run(notifier.notify_telegram_direct("告警文本"))

    assert calls == [("42", "告警文本")]


def test_attempt_telegram_uses_markdown(monkeypatch):
    sent = {}

    async def fake_send(channel, target, text, parse_mode=None):
        sent["target"] = target
        sent["parse_mode"] = parse_mode

    monkeypatch.setattr(notifier.config, "NOTIFY_CHANNELS", [_telegram_channel()])
    monkeypatch.setattr(notifier, "_send_telegram_channel", fake_send)

    job = {"channel": "telegram", "target": "42", "text": "📥 *新短信*"}
    ok, err = asyncio.run(notifier._attempt(job))

    assert ok is True and err == ""
    assert sent["target"] == "42"
    assert sent["parse_mode"] == "Markdown"


def test_attempt_telegram_tls_eof_has_actionable_error(monkeypatch):
    async def fake_send(channel, target, text, parse_mode=None):
        raise httpx.ConnectError("TLS/SSL connection has been closed (EOF) (_ssl.c:992)")

    monkeypatch.setattr(notifier.config, "NOTIFY_CHANNELS", [_telegram_channel()])
    monkeypatch.setattr(notifier, "_send_telegram_channel", fake_send)

    job = {"channel": "telegram", "target": "42", "text": "sms-hub 测试通知"}
    ok, err = asyncio.run(notifier._attempt(job))

    assert ok is False
    assert "Telegram API TLS 连接被对端关闭" in err
    assert "API Base" in err


def test_telegram_http_error_includes_description():
    req = httpx.Request("POST", "https://api.telegram.org/botTOKEN/sendMessage")
    resp = httpx.Response(
        401,
        request=req,
        json={"ok": False, "description": "Unauthorized"},
    )

    assert notifier._telegram_http_error(resp) == "Telegram API HTTP 401: Unauthorized"


def test_test_channel_reuses_attempt(monkeypatch):
    calls = []

    async def fake_attempt(job):
        calls.append(job)
        return True, ""

    monkeypatch.setattr(notifier.config, "NOTIFY_CHANNELS", [_telegram_channel()])
    monkeypatch.setattr(notifier, "_attempt", fake_attempt)

    out = asyncio.run(notifier.test_channel("telegram"))

    assert out == {"ok": True, "error": ""}
    assert calls == [
        {
            "channel": "telegram",
            "target": "42",
            "text": "sms-hub 测试通知",
        }
    ]


def test_test_channel_missing_target(monkeypatch):
    monkeypatch.setattr(
        notifier.config,
        "NOTIFY_CHANNELS",
        [_telegram_channel(token="", chat_id="42")],
    )

    out = asyncio.run(notifier.test_channel("telegram"))

    assert out["ok"] is False
    assert "通道未配置目标" in out["error"]


def test_test_channel_unknown(monkeypatch):
    monkeypatch.setattr(notifier.config, "NOTIFY_CHANNELS", [_telegram_channel()])

    out = asyncio.run(notifier.test_channel("missing"))

    assert out == {"ok": False, "error": "未知通道"}


def test_telegram_channel_requires_token_and_chat(monkeypatch):
    monkeypatch.setattr(
        notifier.config,
        "NOTIFY_CHANNELS",
        [_telegram_channel(token="", chat_id="42")],
    )
    assert notifier._enabled_channels() == []


def test_webhook_channel_formats_structured_payload():
    out = notifier._format_for_channel(
        {"type": "webhook_json"},
        {
            "sender": "10086",
            "received_at": "2026-06-14 10:00",
            "code": "1234",
            "text": "验证码 1234",
        },
    )
    assert '"sender": "10086"' in out
    assert '"message": "验证码 1234"' in out


# ── 推送模板占位符渲染 ──
def _ctx(**kw):
    base = {
        "gw_id": (1 << 32) | 114514,   # 高位代次 + 低位编号
        "sender": "13800138000",
        "text": "您的验证码是 114514",
        "received_at": "2026-06-14 10:00",
        "code": "114514",
        "sender_name": "测试号",
        "msg_id": 7,
    }
    base.update(kw)
    return base


def test_render_basic_and_aliases():
    ctx = _ctx()
    assert notifier._render_template("{sender}", ctx) == "13800138000"
    assert notifier._render_template("{fulltext}", ctx) == ctx["text"]
    assert notifier._render_template("{message}", ctx) == ctx["text"]
    assert notifier._render_template("{timestamp}", ctx) == "2026-06-14 10:00"
    assert notifier._render_template("{time}", ctx) == ctx["received_at"]
    assert notifier._render_template("{from}", ctx) == "13800138000"
    assert notifier._render_template("{name}", ctx) == "测试号"


def test_render_case_insensitive_and_fullwidth():
    ctx = _ctx()
    assert notifier._render_template("{Sender}", ctx) == "13800138000"
    assert notifier._render_template("{CODE}", ctx) == "114514"
    # 全角括号(输入法常见)与占位符内空格
    assert notifier._render_template("验证码 ｛code｝", ctx) == "验证码 114514"
    assert notifier._render_template("{ code }", ctx) == "114514"


def test_render_unknown_placeholder_kept_as_is():
    ctx = _ctx()
    assert notifier._render_template("{foo} {sender}", ctx) == "{foo} 13800138000"


def test_render_code_none_becomes_empty():
    assert notifier._render_template("码:{code}", _ctx(code=None)) == "码:"


def test_render_id_low32_and_raw_id():
    ctx = _ctx(device_msg_id=114514)
    # {id} -> 消息 id(msg_id);{raw_id} -> 设备消息编号 device_msg_id
    assert notifier._render_template("#{id}", ctx) == "#7"
    assert notifier._render_template("{raw_id}", ctx) == "114514"


def test_render_text_md_escapes_markdown():
    ctx = _ctx(text="a*b_c`d[e")
    assert notifier._render_template("{text_md}", ctx) == "a\\*b\\_c\\`d\\[e"
    # 原始 {text} 不转义
    assert notifier._render_template("{text}", ctx) == "a*b_c`d[e"


def test_escape_json_produces_valid_embedded_string():
    ctx = _ctx(text='含"引号"与\\斜杠\n换行')
    out = notifier._render_template('{"msg":"{text}","code":"{code}"}', ctx, escape="json")
    parsed = json.loads(out)   # 渲染结果必须是合法 JSON
    assert parsed["msg"] == '含"引号"与\\斜杠\n换行'
    assert parsed["code"] == "114514"


def test_escape_url_encodes_value():
    ctx = _ctx(text="你好 世界")
    out = notifier._render_template("http://x/p?c={code}&m={text}", ctx, escape="url")
    assert "c=114514" in out
    assert "你好" not in out        # 中文被编码
    assert " " not in out           # 空格被编码为 %20
    assert out.startswith("http://x/p?c=114514&m=")


def test_format_for_channel_sms_forward_template():
    ch = {"type": "sms_forward", "config": {"template": "{sender_name}({sender}): {code}"}}
    assert notifier._format_for_channel(ch, _ctx()) == "测试号(13800138000): 114514"


def test_format_for_channel_sms_forward_default_backcompat():
    ch = {"type": "sms_forward", "config": {}}
    out = notifier._format_for_channel(ch, _ctx())
    assert "📥 新短信" in out
    assert "发件人：13800138000" in out
    assert "验证码：114514" in out
    assert "您的验证码是 114514" in out


def test_format_for_channel_telegram_template_plain():
    ch = {"type": "telegram", "config": {"template": "*{sender}*\n{text_md}"}}
    assert notifier._format_for_channel(ch, _ctx(text="a_b")) == "*13800138000*\na\\_b"


def test_format_for_channel_telegram_default_backcompat():
    ch = {"type": "telegram", "config": {}}
    assert notifier._format_for_channel(ch, _ctx()).startswith("📥 *新短信*")


def test_format_for_channel_webhook_json_template_valid_json():
    ch = {"type": "webhook_json", "config": {"template": '{"text":"{text}","code":"{code}"}'}}
    out = notifier._format_for_channel(ch, _ctx(text='a"b'))
    assert json.loads(out) == {"text": 'a"b', "code": "114514"}


def test_render_target_webhook_get_encodes_url():
    ch = {"type": "webhook_get", "config": {"url": "http://x/p?code={code}&msg={text}"}}
    out = notifier._render_target(ch, _ctx(text="你好"))
    assert out.startswith("http://x/p?code=114514&msg=")
    assert "你好" not in out


def test_render_target_no_placeholder_unchanged():
    ch = {"type": "webhook_json", "config": {"url": "https://example.com/hook"}}
    assert notifier._render_target(ch, _ctx()) == "https://example.com/hook"


def test_render_target_non_webhook_uses_target_for():
    ch = {"type": "telegram", "config": {"chat_id": "42", "bot_token": "tok"}}
    assert notifier._render_target(ch, _ctx()) == "42"


def test_enqueue_for_message_uses_template(monkeypatch):
    enqueued = []

    async def fake_alias(phone):
        return "备注名"

    async def fake_get_sim(sim_id):
        return {"name": "主卡"} if sim_id == "sim_x" else None

    async def fake_get_device(mac):
        return {"name": "客厅网关"}

    async def fake_enqueue(msg_id, channel, target, text, *, commit=True):
        enqueued.append((channel, target, text))

    monkeypatch.setattr(notifier.db, "fetch_sender_alias", fake_alias)
    monkeypatch.setattr(notifier.db, "get_sim", fake_get_sim)
    monkeypatch.setattr(notifier.db, "get_device", fake_get_device)
    monkeypatch.setattr(notifier, "enqueue", fake_enqueue)
    monkeypatch.setattr(
        notifier.config,
        "NOTIFY_CHANNELS",
        [{"id": "sms_forward", "type": "sms_forward", "enabled": True,
          "config": {"to": "10086", "template": "{sender_name} 验证码 {code}"}}],
    )
    m = {"sim_id": "sim_x", "device_mac": "aabbccddeeff", "sender": "13800138000",
         "text": "码 114514", "received_at": "2026-06-14 10:00", "code": "114514"}
    asyncio.run(notifier.enqueue_for_message(5, m))
    assert enqueued == [("sms_forward", "10086", "备注名 验证码 114514")]


# ── 钉钉/飞书加签机器人 ──
def _bot_channel(typ, *, secret="SECtest"):
    return {
        "id": typ, "type": typ, "name": typ, "enabled": True,
        "config": {"url": "https://bot.example.com/hook", "secret": secret},
    }


def test_dingtalk_sign_matches_official_algorithm():
    # 官方:key=secret, msg=f"{ts}\n{secret}", sha256 → base64 → URL 编码
    import base64 as _b64, hashlib as _h, hmac as _hmac
    from urllib.parse import quote_plus
    secret, ts = "SECtest", 1577808000000
    expected = quote_plus(_b64.b64encode(
        _hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), _h.sha256).digest()
    ))
    assert notifier._dingtalk_sign(secret, ts) == expected


def test_feishu_sign_matches_official_algorithm():
    # 官方:key=f"{ts}\n{secret}", msg=secret(与钉钉相反), sha256 → base64
    import base64 as _b64, hashlib as _h, hmac as _hmac
    secret, ts = "123456", 1599360473
    expected = _b64.b64encode(
        _hmac.new(f"{ts}\n{secret}".encode(), secret.encode(), _h.sha256).digest()
    ).decode()
    assert notifier._feishu_sign(secret, ts) == expected


def test_format_for_channel_dingtalk_default_text_body():
    ch = {"type": "dingtalk", "config": {}}
    out = notifier._format_for_channel(ch, _ctx(text="验证码 114514"))
    content = json.loads(out)["text"]["content"]
    assert "📥 新短信" in content
    assert "发件人：13800138000" in content


def test_format_for_channel_feishu_default_text_body():
    ch = {"type": "feishu", "config": {}}
    out = notifier._format_for_channel(ch, _ctx(text="hi"))
    content = json.loads(out)["content"]["text"]
    assert "📥 新短信" in content
    assert "发件人：13800138000" in content


def test_format_for_channel_dingtalk_template_renders():
    ch = {"type": "dingtalk", "config": {"template": "{sender_name}: {code}"}}
    out = notifier._format_for_channel(ch, _ctx())
    assert json.loads(out)["text"]["content"] == "测试号: 114514"


class _FakeResp:
    status_code = 200

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self):
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.posts.append((url, json))
        return _FakeResp()


def test_send_signed_bot_dingtalk_appends_sign_to_url(monkeypatch):
    monkeypatch.setattr(notifier, "_dingtalk_sign", lambda s, t: "SIGN")
    monkeypatch.setattr(notifier.time, "time", lambda: 1700000000.0)
    client = _FakeClient()
    monkeypatch.setattr(notifier.httpx, "AsyncClient", lambda **kw: client)

    ok, err = asyncio.run(notifier._send_signed_bot(
        _bot_channel("dingtalk"),
        {"target": "https://oapi.dingtalk.com/robot/send", "text": "{}"},
    ))

    assert ok is True and err == ""
    url, body = client.posts[0]
    assert "timestamp=1700000000000" in url
    assert "sign=SIGN" in url


def test_send_signed_bot_feishu_puts_sign_in_body(monkeypatch):
    monkeypatch.setattr(notifier, "_feishu_sign", lambda s, t: "SIGN")
    monkeypatch.setattr(notifier.time, "time", lambda: 1700000000.0)
    client = _FakeClient()
    monkeypatch.setattr(notifier.httpx, "AsyncClient", lambda **kw: client)

    ok, err = asyncio.run(notifier._send_signed_bot(
        _bot_channel("feishu"),
        {"target": "https://open.feishu.cn/open-apis/bot/v2/hook/x", "text": "{}"},
    ))

    assert ok is True and err == ""
    url, body = client.posts[0]
    assert body["timestamp"] == "1700000000"
    assert body["sign"] == "SIGN"


def test_send_signed_bot_without_secret_posts_plain(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(notifier.httpx, "AsyncClient", lambda **kw: client)

    ok, err = asyncio.run(notifier._send_signed_bot(
        _bot_channel("dingtalk", secret=""),
        {"target": "https://oapi.dingtalk.com/robot/send", "text": "{}"},
    ))

    assert ok is True and err == ""
    url, body = client.posts[0]
    assert url == "https://oapi.dingtalk.com/robot/send"
    assert "sign" not in url


# ── Bark / PushPlus / Server酱 / Gotify ──
def test_format_for_channel_bark():
    ch = {"type": "bark", "config": {}}
    out = notifier._format_for_channel(ch, _ctx(text="验证码"))
    data = json.loads(out)
    assert data["title"] == "🔐 114514"
    assert "验证码" in data["body"]


def test_format_for_channel_pushplus_injects_token_and_channel():
    ch = {"type": "pushplus", "config": {"token": "TOK", "channel": "app"}}
    out = json.loads(notifier._format_for_channel(ch, _ctx(text="hi")))
    assert out["token"] == "TOK"
    assert out["channel"] == "app"
    assert out["title"] == "🔐 114514"
    assert "hi" in out["content"]


def test_format_for_channel_pushplus_default_channel():
    out = json.loads(notifier._format_for_channel(
        {"type": "pushplus", "config": {"token": "T"}}, _ctx()))
    assert out["channel"] == "wechat"


def test_format_for_channel_serverchan_title_desp():
    out = json.loads(notifier._format_for_channel(
        {"type": "serverchan", "config": {}}, _ctx(text="内容X")))
    assert out["title"] == "🔐 114514"
    assert "内容X" in out["desp"]


def test_format_for_channel_gotify_priority():
    out = json.loads(notifier._format_for_channel(
        {"type": "gotify", "config": {}}, _ctx(text="hi")))
    assert out["priority"] == 5
    assert out["title"] == "🔐 114514"
    assert "hi" in out["message"]


def test_target_for_pushplus_default_url_and_requires_token():
    assert notifier._target_for({"type": "pushplus", "config": {}}) == ""
    assert notifier._target_for({"type": "pushplus", "config": {"token": "T"}}) == "http://www.pushplus.plus/send"
    assert notifier._target_for({"type": "pushplus", "config": {"token": "T", "url": "https://x"}}) == "https://x"


def test_target_for_serverchan_default_url():
    assert notifier._target_for({"type": "serverchan", "config": {}}) == ""
    assert notifier._target_for({"type": "serverchan", "config": {"sendkey": "KEY"}}) == "https://sctapi.ftqq.com/KEY.send"
    assert notifier._target_for({"type": "serverchan", "config": {"sendkey": "KEY", "url": "https://x"}}) == "https://x"


def test_target_for_gotify_appends_token():
    assert notifier._target_for({"type": "gotify", "config": {"url": "https://g.com", "token": "T"}}) == "https://g.com/message?token=T"
    assert notifier._target_for({"type": "gotify", "config": {"url": "https://g.com/", "token": "T"}}) == "https://g.com/message?token=T"
    assert notifier._target_for({"type": "gotify", "config": {"url": "https://g.com"}}) == ""


def test_attempt_serverchan_posts_form_urlencoded(monkeypatch):
    sent = {}

    class _FormClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, data=None):
            sent["url"] = url
            sent["json"] = json
            sent["data"] = data
            return _FakeResp()

    monkeypatch.setattr(notifier.httpx, "AsyncClient", lambda **kw: _FormClient())
    monkeypatch.setattr(notifier.config, "NOTIFY_CHANNELS", [
        {"id": "sc", "type": "serverchan", "enabled": True, "config": {"sendkey": "K"}}])

    job = {"channel": "sc", "target": "https://sctapi.ftqq.com/K.send",
           "text": '{"title":"短信来自: 10086","desp":"**内容:** hi"}'}
    ok, err = asyncio.run(notifier._attempt(job))

    assert ok is True and err == ""
    assert sent["json"] is None                 # Server酱走 form 而非 JSON
    assert sent["data"] == {"title": "短信来自: 10086", "desp": "**内容:** hi"}
