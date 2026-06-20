#!/usr/bin/env python3
"""Mock ESP32 SMS Gateway — 模拟设备端 HTTP API + webhook 推送。

运行方式:
  python test/demo/mock_device.py [--port PORT] [--hub HUB_URL]

设备端点:
  GET  /{token}/status  设备自检快照
  GET  /{token}/get     游标拉取消息
  POST /{token}/send    发送短信
  POST /{token}/at      AT 命令透传

管理端点:
  GET  /                简易控制面板（注入模拟短信）
  POST /inject          注入一条模拟短信
"""

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# ── Config ──
TOKEN = os.getenv("DEVICE_TOKEN", "demo-token-12345678")
PORT = int(os.getenv("MOCK_PORT", "8080"))
HUB_URL = os.getenv("HUB_URL", "http://localhost:8025")
WEBHOOK_ENABLED = True
HEARTBEAT_INTERVAL_S = int(os.getenv("MOCK_HEARTBEAT_INTERVAL_S", "15"))
# v2 身份:MAC(物理链路)+ IMSI(SIM 业务身份)。双设备 demo 时第二台用不同 MAC/IMSI。
MAC = os.getenv("MOCK_MAC", "aabbccddeeff").replace(":", "").replace("-", "").lower()
SEQ_ID = 0  # 同物理设备单调递增的 webhook 序号
_background_tasks: set[asyncio.Task] = set()

# ── Full-feature mock profile ──
FW_VERSION = os.getenv("MOCK_FW_VERSION", "2.0.0-fullmock")
PROTOCOL_VERSION = int(os.getenv("MOCK_PROTOCOL_VERSION", "1"))
MANUFACTURER = os.getenv("MOCK_MANUFACTURER", "China Mobile IoT")
MODEL = os.getenv("MOCK_MODEL", "ML307R-DC FULL MOCK")
REVISION = os.getenv("MOCK_REVISION", "V2.0.0.FULLMOCK")
IMEI = os.getenv("MOCK_IMEI", "867012345678901")
ICCID = os.getenv("MOCK_ICCID", "89860012345678901234")
IMSI = os.getenv("MOCK_IMSI", "460001234567890")
MSISDN = os.getenv("MOCK_MSISDN", "13800138000")
APN = os.getenv("MOCK_APN", "cmnet")
OPERATOR = os.getenv("MOCK_OPERATOR", "中国移动")
WIFI_SSID = os.getenv("MOCK_WIFI_SSID", "MockNet-5G")

# ── Simulated device state ──
BOOT_TIME = time.time()
NEXT_ID = 1
MESSAGES: list[dict] = []  # stored messages
SENT_COUNT = 0
RX_TOTAL = 0
DROPPED_TOTAL = 0
WEBHOOK_FAIL_TOTAL = 0
FLIGHT_MODE = int(os.getenv("MOCK_CFUN", "1"))
PDP_ACTIVE = os.getenv("MOCK_PDP_ACTIVE", "1").strip().lower() not in ("0", "false", "no")
OTA_UPLOADS = 0

# Predefined mock message templates
MOCK_TEMPLATES = [
    ("10690329", "【菜鸟驿站】取件码 5-2-8842，您的包裹已到小区南门快递柜，请凭此码取件。"),
    ("13800138000", "您好，您的快递已放在门卫处，请及时取走。"),
    ("95566", "【中国银行】您的尾号0525账户于{}转入 15,000.00 元，余额 86,420.00 元。"),
    ("10658000", "【中国移动】您本月的流量已用 8.5GB，剩余 11.5GB。查询详情请回复 CXLL。"),
    ("10086", "尊敬的客户，您的手机已欠费 3.50 元，为不影响您的正常使用，请及时充值。"),
    ("10690329", "【某站】验证码 114514，您正在登录账号，请勿向他人泄露验证码。"),
    ("95555", "【招商银行】您尾号6688的信用卡{date}消费 328.00 元，余额 12,450.00 元。"),
    ("10690123", "【淘宝】您的订单 3847291038472 已发货，快递单号：SF1234567890，点击查看物流详情。"),
    ("13900000000", "明天下午3点开会，地点在B座12层会议室，请大家准时参加。"),
    ("95599", "【微信支付】{date} 14:32 向商家付款 28.00 元，商户：杭州小厨餐饮店。"),
    ("10010", "【中国联通】您办理的冰激凌套餐月费99元，含国内流量40GB+国内语音1000分钟。"),
    ("15800000001", "爸，我今天晚上不回家吃饭了，同学过生日，我们在外面聚餐。"),
    ("10690555", "【支付宝】您的账单已生成，{date}应还 3,420.00 元，最低还款 342.00 元。"),
    ("17000000002", "老板，方案我已经发你邮箱了，麻烦看一下，有问题我随时改。"),
    ("10655012", "【京东】您购买的商品已到达北京分拣中心，预计明日送达。订单号：JD20260613001"),
    ("95533", "【建设银行】您尾号1234的储蓄卡{date} 09:15 ATM取款 2,000.00 元。"),
    ("18500000003", "这个周末有空吗？好久没聚了，一起吃个饭吧！"),
    ("10698000", "【美团】您的外卖订单 #20260613142 已由骑手接单，预计 30 分钟内送达。"),
    ("18800000005", "验证码 882493，用于登录企业微信管理后台，5分钟内有效。"),
    ("95528", "【浦发银行】您的信用卡本月账单已出，请于{date}前还款，以免影响信用记录。"),
]

# giffgaff(英国 MVNO)语料:英镑余额 / 英文验证码 / 套餐用量
GIFFGAFF_TEMPLATES = [
    ("giffgaff", "Your giffgaff balance is £12.50. Top up at giffgaff.com"),
    ("447700900123", "Your verification code is 482910. It expires in 10 minutes."),
    ("WhatsApp", "Your WhatsApp code: 193-847. Don't share this code with others."),
    ("giffgaff", "You've used 80% of your data this month. Buy a goodybag at giffgaff.com"),
    ("62828", "Your Tesco Bank code is 726153. Never share it with anyone."),
    ("447911123456", "Hey, running 10 mins late — see you soon!"),
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def next_seq() -> int:
    """v2 webhook 单调递增序号(同物理设备内)。"""
    global SEQ_ID
    SEQ_ID += 1
    return SEQ_ID


def local_now():
    return datetime.now()


def scts_now():
    """Generate a fake SMSC timestamp."""
    n = local_now()
    return n.strftime("%y/%m/%d,%H:%M:%S+32")


def radio_online() -> bool:
    return FLIGHT_MODE == 1


def current_cereg() -> int:
    return 1 if radio_online() else 0


def current_pdp() -> bool:
    return bool(PDP_ACTIVE and radio_online())


def rssi_code() -> int:
    return random.randint(18, 31) if radio_online() else 99


def csq_dbm() -> int:
    if not radio_online():
        return -113
    return -75 - random.randint(0, 12)


def at_ok(*lines: str) -> str:
    if not lines:
        return "OK"
    return "\n".join(lines) + "\n\nOK"


def at_error(code: int = 4) -> str:
    return f"+CME ERROR: {code}"


def modem_snapshot() -> dict:
    return {
        "ready": True,
        "model": MODEL,
        "fw": REVISION,
        "cereg": current_cereg(),
        "csq_dbm": csq_dbm(),
        "operator": OPERATOR if radio_online() else "",
        "sim": True,
        "imsi": IMSI,
        "imsi_tail": IMSI[-4:],
        "iccid": ICCID,
        "iccid_tail": ICCID[-4:],
        "msisdn": MSISDN,
        "flight_mode": FLIGHT_MODE,
        "pdp_active": current_pdp(),
        "data_connection_active": current_pdp(),
        "data_guard_ok": True,
        "data_guard_failures": 0,
        "sms_rx_config_known": True,
        "sms_rx_config_ok": True,
        "sms_rx_last_check_ms": int((time.time() - BOOT_TIME) * 1000),
        "sms_rx_last_config_ms": 0,
        "sms_rx_recoveries": 0,
        "sms_rx_failures": 0,
        "sms_rx_last_recovery_reason": 0,
        "apn": APN,
    }


def status_payload() -> dict:
    uptime = time.time() - BOOT_TIME
    return {
        "mac": MAC,
        "seq_id": SEQ_ID,
        "device_ts_ms": int(time.time() * 1000),
        "fw": FW_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "capabilities": 0x3F,  # bits 0-5: async_job|batch_delete|mipcall|watchdog|delete_queue|recovery_reason
        "heartbeat_interval_s": HEARTBEAT_INTERVAL_S,
        "uptime_s": int(uptime),
        "reset_reason": "POWERON",
        "free_heap": 180000 + random.randint(-10000, 10000),
        "wifi_rssi": -52 - random.randint(0, 10),
        "wifi_ssid": WIFI_SSID,
        "ip": "127.0.0.1",
        "port": PORT,
        "modem": modem_snapshot(),
        "buffer": {
            "oldest_id": MESSAGES[0]["id"] if MESSAGES else NEXT_ID - 1,
            "latest_id": NEXT_ID - 1,  # Counter high-water mark, same as firmware.
            "count": len(MESSAGES),
            "capacity": 50,
            "dropped_total": DROPPED_TOTAL,
        },
        "counters": {
            "rx_total": RX_TOTAL,
            "tx_total": SENT_COUNT,
            "webhook_fail_total": WEBHOOK_FAIL_TOTAL,
        },
        "delete_queue": {
            "depth": 0,
            "oldest_age_ms": 0,
            "failures": 0,
        },
        "last_error": "" if radio_online() else "radio is disabled by CFUN",
    }


# ── FastAPI app ──
app = FastAPI(title="mock-sms-device")


# ═══════════════════════════════════════════════
# Device API (what the hub polls)
# ═══════════════════════════════════════════════

@app.get("/{token}/status")
async def device_status(token: str):
    if token != TOKEN:
        raise HTTPException(status_code=404)
    return status_payload()


@app.get("/{token}/pull")
async def device_pull(token: str, after: int = 0, limit: int = 20,
                      include_status: int = 0):
    if token != TOKEN:
        raise HTTPException(status_code=404)
    limit = max(1, min(limit, 50))
    filtered = [m for m in MESSAGES if m["id"] > after]
    page = [{**m, "device_msg_id": m["id"]} for m in filtered[:limit]]
    out = {
        "mac": MAC,
        "buffer": {
            "oldest_id": MESSAGES[0]["id"] if MESSAGES else NEXT_ID - 1,
            "latest_id": NEXT_ID - 1,
            "count": len(MESSAGES),
            "capacity": 50,
            "dropped_total": DROPPED_TOTAL,
        },
        "messages": page,
        "status": status_payload() if int(include_status) else None,
    }
    return out


@app.post("/{token}/send")
async def device_send(token: str, request: Request):
    global SENT_COUNT
    if token != TOKEN:
        raise HTTPException(status_code=404)
    body = await request.json()
    to = body.get("to", "").strip()
    text = body.get("text", "").strip()
    if not to or not text:
        return {"ok": False, "error": "号码或内容为空", "device_msg_id": 0, "parts": 0}

    # Simulate send delay
    await asyncio.sleep(0.3 + random.random() * 0.5)
    chars = len(text)
    parts = 1 if chars <= 70 else (chars + 59) // 60
    SENT_COUNT += 1
    gid = SENT_COUNT + 10000
    print(f"  📤 [mock-device] SMS sent to {to}: {text[:40]}... ({parts} parts, id={gid})")
    return {"ok": True, "parts": parts, "device_msg_id": gid}


@app.post("/{token}/at")
async def device_at(token: str, request: Request):
    global FLIGHT_MODE, PDP_ACTIVE, APN
    if token != TOKEN:
        raise HTTPException(status_code=404)
    body = await request.json()
    raw_cmd = body.get("cmd", "AT").strip()
    cmd = raw_cmd.upper()
    timeout_ms = min(body.get("timeout_ms", 3000), 15000)

    await asyncio.sleep(0.1 + random.random() * 0.2)

    if cmd in ("AT", "ATE0", "ATE1"):
        resp = at_ok()
    elif cmd == "ATI" or cmd.startswith("ATI"):
        resp = at_ok(
            MANUFACTURER,
            MODEL,
            f"Revision: {REVISION}",
            f"IMEI: {IMEI}",
        )
    elif cmd in ("AT+GMI", "AT+CGMI"):
        resp = at_ok(MANUFACTURER)
    elif cmd in ("AT+GMM", "AT+CGMM"):
        resp = at_ok(MODEL)
    elif cmd in ("AT+GMR", "AT+CGMR"):
        resp = at_ok(REVISION)
    elif cmd in ("AT+GSN", "AT+CGSN"):
        resp = at_ok(IMEI)
    elif cmd.startswith("AT+CESQ"):
        rxlev = rssi_code()
        rsrq = random.randint(12, 24) if radio_online() else 255
        rsrp = random.randint(46, 67) if radio_online() else 255
        resp = at_ok(f"+CESQ: {rxlev},99,255,255,{rsrq},{rsrp}")
    elif cmd.startswith("AT+COPS"):
        resp = at_ok(f'+COPS: 0,0,"{OPERATOR}",7') if radio_online() else at_ok("+COPS: 0")
    elif cmd.startswith("AT+CSQ"):
        resp = at_ok(f"+CSQ: {rssi_code()},99")
    elif cmd == "AT+CPIN?":
        resp = at_ok("+CPIN: READY")
    elif cmd == "AT+CFUN?":
        resp = at_ok(f"+CFUN: {FLIGHT_MODE}")
    elif cmd.startswith("AT+CFUN="):
        try:
            target = int(cmd.split("=", 1)[1].split(",", 1)[0].strip())
        except ValueError:
            resp = at_error(50)
        else:
            if target not in (0, 1, 4):
                resp = at_error(50)
            else:
                FLIGHT_MODE = target
                PDP_ACTIVE = target == 1
                resp = at_ok()
    elif cmd in ("AT+CEREG?", "AT+CGREG?", "AT+CREG?"):
        name = cmd[3:].split("?", 1)[0]
        resp = at_ok(f"+{name}: 0,{current_cereg()}")
    elif cmd.startswith("AT+CEREG=") or cmd.startswith("AT+CGREG=") or cmd.startswith("AT+CREG="):
        resp = at_ok()
    elif cmd == "AT+CGATT?":
        resp = at_ok(f"+CGATT: {1 if current_pdp() else 0}")
    elif cmd.startswith("AT+CGATT="):
        PDP_ACTIVE = cmd.endswith("=1") and radio_online()
        resp = at_ok()
    elif cmd == "AT+CGACT?":
        resp = at_ok(f"+CGACT: 1,{1 if current_pdp() else 0}")
    elif cmd.startswith("AT+CGACT="):
        m = re.match(r"AT\+CGACT=(\d+),(\d+)", cmd)
        if not m:
            resp = at_error(50)
        else:
            PDP_ACTIVE = m.group(1) == "1" and radio_online()
            resp = at_ok()
    elif cmd == "AT+MIPCALL?":
        resp = at_ok(f'+MIPCALL: 1,{1 if current_pdp() else 0}' +
                     (',"10.66.88.12"' if current_pdp() else ""))
    elif cmd.startswith("AT+MIPCALL="):
        m = re.match(r"AT\+MIPCALL=(\d+),(\d+)", cmd)
        if not m:
            resp = at_error(50)
        else:
            PDP_ACTIVE = m.group(1) == "1" and radio_online()
            resp = at_ok()
    elif cmd == "AT+CGDCONT?":
        resp = at_ok(f'+CGDCONT: 1,"IP","{APN}","0.0.0.0",0,0,0,0')
    elif cmd.startswith("AT+CGDCONT="):
        m = re.search(r'AT\+CGDCONT=\d+,"[^"]*","([^"]*)"', raw_cmd, re.I)
        if m:
            APN = m.group(1).strip()
        resp = at_ok()
    elif cmd in ("AT+ICCID", "AT+CCID", "AT+QCCID"):
        prefix = "+QCCID" if cmd == "AT+QCCID" else "+ICCID"
        resp = at_ok(f"{prefix}: {ICCID}")
    elif cmd == "AT+CIMI":
        resp = at_ok(IMSI)
    elif cmd == "AT+CNUM":
        resp = at_ok(f'+CNUM: "","{MSISDN}",129,7,4')
    elif cmd.startswith("AT+CGPADDR"):
        ip = "10.66.88.12" if current_pdp() else "0.0.0.0"
        resp = at_ok(f'+CGPADDR: 1,"{ip}"')
    elif cmd.startswith("AT+MPING"):
        if not current_pdp():
            resp = at_error(30)
        else:
            await asyncio.sleep(min(0.5, timeout_ms / 1000))
            host = "119.29.29.29"
            m = re.search(r'"([^"]+)"', raw_cmd)
            if m:
                host = m.group(1)
            resp = at_ok(f'+MPING: "{host}",0,{random.randint(22, 58)},64')
    else:
        resp = at_ok()
    return {"ok": not resp.startswith("+CME ERROR"), "response": resp}


@app.post("/{token}/delete")
async def device_delete(token: str, request: Request):
    """批量删除缓冲消息(v2 /delete)。返回每条是否命中。"""
    if token != TOKEN:
        raise HTTPException(status_code=404)
    body = await request.json()
    ids = [int(x) for x in body.get("device_msg_ids", [])]
    result = []
    for gid in ids:
        found = False
        for i, m in enumerate(MESSAGES):
            if m["id"] == gid:
                MESSAGES.pop(i)
                found = True
                break
        result.append({"device_msg_id": gid, "found": found})
    print(f"  🗑️ [mock-device] delete ids={ids}, buffer={len(MESSAGES)}")
    return {"ok": True, "deleted": result}


@app.get("/{token}/update", response_class=HTMLResponse)
async def ota_form(token: str):
    if token != TOKEN:
        raise HTTPException(status_code=404)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Mock OTA</title></head>
<body style="font-family:system-ui,-apple-system,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;line-height:1.6">
  <h1>Mock OTA</h1>
  <p>设备: {MODEL} / {REVISION}</p>
  <p>已模拟上传次数: {OTA_UPLOADS}</p>
  <form method="post" enctype="application/octet-stream">
    <input type="file" name="firmware">
    <button type="submit">上传固件</button>
  </form>
</body>
</html>"""


@app.post("/{token}/update")
async def ota_upload(token: str, request: Request):
    global OTA_UPLOADS
    if token != TOKEN:
        raise HTTPException(status_code=404)
    data = await request.body()
    OTA_UPLOADS += 1
    return {
        "ok": True,
        "mock": True,
        "bytes": len(data),
        "uploads": OTA_UPLOADS,
        "fw": FW_VERSION,
    }


# ═══════════════════════════════════════════════
# Webhook sender (device → hub)
# ═══════════════════════════════════════════════

async def send_webhook(event: str, latest_id: int):
    """Send webhook to hub: POST /hook/{token} (v2:带 mac/seq_id/modem.imsi)。"""
    global WEBHOOK_FAIL_TOTAL
    url = f"{HUB_URL}/hook/{TOKEN}"
    # heartbeat/boot 携带完整状态(含 modem.imsi);sms/hello 只带轻量字段
    if event in ("heartbeat", "boot"):
        body = status_payload()
    else:
        body = {
            "mac": MAC, "fw": FW_VERSION, "ip": "127.0.0.1", "port": PORT,
            "buffer": {
                "oldest_id": MESSAGES[0]["id"] if MESSAGES else NEXT_ID - 1,
                "latest_id": latest_id, "count": len(MESSAGES),
                "capacity": 50, "dropped_total": DROPPED_TOTAL,
            },
        }
    body.update({
        "event": event,
        "seq_id": next_seq(),
        "device_ts_ms": int(time.time() * 1000),
    })
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.post(url, json=body)
            if r.status_code == 200:
                print(f"  🔔 [mock-device] webhook {event} sent, hub responded ok")
            else:
                print(f"  ⚠️ [mock-device] webhook {event} got status {r.status_code}")
                WEBHOOK_FAIL_TOTAL += 1
    except Exception as e:
        print(f"  ❌ [mock-device] webhook {event} failed: {e}")
        WEBHOOK_FAIL_TOTAL += 1


async def heartbeat_loop():
    while True:
        await asyncio.sleep(max(1, HEARTBEAT_INTERVAL_S))
        if WEBHOOK_ENABLED:
            await send_webhook("heartbeat", NEXT_ID - 1)


def _add_message(sender: str, text: str, code: str | None = None):
    global NEXT_ID, RX_TOTAL, DROPPED_TOTAL
    age_s = random.randint(1, 30)
    msg = {
        "id": NEXT_ID,
        "from": sender,
        "scts": scts_now(),
        "age_s": age_s,
        "text": text,
        "complete": True,
        "truncated": False,
    }
    MESSAGES.append(msg)
    NEXT_ID += 1
    RX_TOTAL += 1

    # Buffer overflow simulation
    while len(MESSAGES) > 50:
        MESSAGES.pop(0)
        DROPPED_TOTAL += 1

    return msg


# ═══════════════════════════════════════════════
# Management UI
# ═══════════════════════════════════════════════

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mock Device Console</title>
<style>
:root {
  --bg: #0f172a; --surface: #1e293b; --border: #334155;
  --text: #f1f5f9; --text2: #94a3b8; --accent: #f59e0b;
  --success: #22c55e; --error: #ef4444;
  --font: system-ui,-apple-system,sans-serif;
  --font-mono: ui-monospace,Menlo,Consolas,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font);background:var(--bg);color:var(--text);padding:20px;max-width:800px;margin:0 auto}
h1{font-size:20px;margin-bottom:4px}
h1 span{color:var(--accent)}
.sub{font-size:12px;color:var(--text2);margin-bottom:24px}
h2{font-size:14px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px}
.section{background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:16px;margin-bottom:16px}
.row{display:flex;gap:8px;align-items:center;margin-bottom:8px}
.row:last-child{margin-bottom:0}
input,select{flex:1;height:40px;padding:0 12px;font-size:14px;font-family:var(--font);background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:2px;outline:none}
input:focus,select:focus{border-color:var(--accent)}
button{height:40px;padding:0 16px;font-size:14px;font-weight:600;font-family:var(--font);background:var(--accent);color:var(--bg);border:0;border-radius:2px;cursor:pointer;transition:transform .08s}
button:active{transform:scale(.97)}
button.ghost{background:transparent;color:var(--text2);border:1px solid var(--border)}
.log{background:var(--bg);border:1px solid var(--border);border-radius:2px;padding:12px;font-family:var(--font-mono);font-size:12px;line-height:1.6;max-height:300px;overflow-y:auto;white-space:pre-wrap;color:var(--text2)}
.log .ok{color:var(--success)}
.log .err{color:var(--error)}
.log .info{color:var(--accent)}
.status-row{display:flex;gap:24px;flex-wrap:wrap}
.stat{display:flex;flex-direction:column}
.stat .v{font-size:24px;font-weight:700;font-family:var(--font-mono)}
.stat .l{font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:.4px}
.templates{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px}
.templates button{height:28px;font-size:11px;padding:0 10px;font-weight:500;background:var(--surface);color:var(--text2);border:1px solid var(--border);white-space:nowrap}
.templates button:hover{background:var(--bg);color:var(--text)}
</style>
</head>
<body>
<h1>🛰️ Mock <span>SMS Gateway</span> Console</h1>
<p class="sub">模拟 ESP32 瘦终端 · 向 Hub 上报 webhook · 提供 /get /status /send /at 端点</p>

<div class="section">
  <h2>📊 设备状态</h2>
  <div class="status-row">
    <div class="stat"><span class="v" id="rx">0</span><span class="l">收到短信</span></div>
    <div class="stat"><span class="v" id="tx">0</span><span class="l">发出短信</span></div>
    <div class="stat"><span class="v" id="buff">0</span><span class="l">缓冲消息</span></div>
    <div class="stat"><span class="v" id="whf">0</span><span class="l">Webhook 失败</span></div>
    <div class="stat"><span class="v" id="uptime">0</span><span class="l">运行秒数</span></div>
  </div>
</div>

<div class="section">
  <h2>✉️ 注入模拟短信</h2>
  <div class="row">
    <input type="text" id="sender" placeholder="发件人号码 (如 10690329)" style="flex:1">
    <input type="text" id="text" placeholder="短信内容..." style="flex:2">
    <button onclick="inject()">注入</button>
  </div>
  <div class="templates" id="tpls"></div>
</div>

<div class="section">
  <h2>📋 事件日志</h2>
  <div class="log" id="log">就绪 — 等待注入模拟短信或 Hub 拉取请求...</div>
</div>

<script>
let rx=0,tx=0,buff=0,whf=0;
const logEl = document.getElementById('log');
function log(msg, cls='') {
  const t = new Date().toLocaleTimeString();
  logEl.innerHTML += `\n<span class="${cls}">[${t}]</span> ${msg}`;
  logEl.scrollTop = logEl.scrollHeight;
}
async function refresh() {
  try {
    const r = await fetch('/demo-token-12345678/status');
    const d = await r.json();
    rx=d.counters.rx_total; tx=d.counters.tx_total; buff=d.buffer.count; whf=d.counters.webhook_fail_total;
    document.getElementById('rx').textContent = rx;
    document.getElementById('tx').textContent = tx;
    document.getElementById('buff').textContent = buff;
    document.getElementById('whf').textContent = whf;
    document.getElementById('uptime').textContent = d.uptime_s;
  } catch(e) {}
}
async function inject(sender, text) {
  const s = sender || document.getElementById('sender').value.trim();
  const t = text || document.getElementById('text').value.trim();
  if (!s || !t) return log('请输入号码和内容','err');
  try {
    const r = await fetch('/inject', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({sender:s, text:t})
    });
    const d = await r.json();
    log(`📩 注入 #${d.id}: ${s} — "${t.slice(0,50)}${t.length>50?'...':''}"`,'ok');
    document.getElementById('sender').value = '';
    document.getElementById('text').value = '';
    refresh();
  } catch(e) { log('注入失败: '+e.message,'err'); }
}
// Pre-populate template buttons
const templates = %TEMPLATES%;
tpls.innerHTML = templates.map((t,i) =>
  `<button onclick="inject('${t[0].replace(/'/g,"\\'")}','${t[1].replace(/'/g,"\\'")}')" title="${t[1].slice(0,60)}">${t[0]}</button>`
).join('');

setInterval(refresh, 2000);
refresh();
log('Mock 设备已就绪,端口 %PORT%, token=demo-token-12345678','info');
log('Hub 地址: %HUB_URL%','info');
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def console():
    tpls = json.dumps([[t[0], t[1]] for t in MOCK_TEMPLATES])
    return HTML.replace("%TEMPLATES%", tpls).replace("%PORT%", str(PORT)).replace("%HUB_URL%", HUB_URL)


class InjectBody(BaseModel):
    sender: str
    text: str


@app.post("/inject")
async def inject_sms(body: InjectBody):
    """Inject a mock SMS into the buffer and fire webhook."""
    msg = _add_message(body.sender.strip(), body.text.strip())
    if WEBHOOK_ENABLED:
        asyncio.create_task(send_webhook("sms", msg["id"]))
    return {"ok": True, "id": msg["id"]}


# ═══════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock ESP32 SMS Gateway")
    parser.add_argument("--port", type=int, default=PORT, help="Device listen port")
    parser.add_argument("--hub", type=str, default=HUB_URL, help="Hub base URL")
    args = parser.parse_args()
    PORT = args.port
    HUB_URL = args.hub.rstrip("/")

    async def startup():
        print(f"🚀 Mock SMS Device starting on port {PORT}")
        print(f"   Token: {TOKEN}")
        print(f"   Hub:   {HUB_URL}")
        print(f"   Console: http://localhost:{PORT}/")
        # 按 operator 选语料:giffgaff 用英文语料,其余用中文
        pool = GIFFGAFF_TEMPLATES if "giffgaff" in OPERATOR.lower() else MOCK_TEMPLATES
        for sender, text in pool[:6]:
            _add_message(sender, text.replace('{date}', local_now().strftime('%m月%d日')))
        if WEBHOOK_ENABLED:
            await asyncio.sleep(0.5)
            await send_webhook("boot", NEXT_ID - 1)
            await send_webhook("heartbeat", NEXT_ID - 1)
            task = asyncio.create_task(heartbeat_loop())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

    @app.on_event("startup")
    async def _startup():
        await startup()

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
