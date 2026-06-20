"""设备 I/O 的纯工具层(无全局状态)。

v2 把单设备全局(base_url / 串行化器 / device-call)拆分:
- 共享 httpx 客户端持有者(进程级,所有 DeviceRuntime 复用)在本模块保留。
- 每设备串行化与 device-call 移到 `core/device/runtime.py::DeviceRuntime`。
- 身份与安全纯函数(MAC 规范化、sim_id 派生、SSRF 校验、Hub 自身地址探测)在此提供。

注意:固件极简 JSON 解析器只认原始 UTF-8,不解 \\uXXXX 转义,故发往设备的请求体
一律 `_json_body`(ensure_ascii=False)。完整 IMSI 只允许在受控通道用于派生,
**永不**持久化/记日志/回传——这里只产出 imsi_hash 与 imsi_tail。
"""
import hashlib
import ipaddress
import json
import logging
import math
import re
import socket

import httpx

from core.infra import config

log = logging.getLogger("device")

_JSON_HEADERS = {"Content-Type": "application/json"}


class DeviceUnknown(Exception):
    """还不知道设备地址(没收到 webhook 且 base_url 为空)。"""


class DeviceBusy(Exception):
    """设备 I/O 正忙。交互式请求应快速失败,避免把 poller 长时间排队。"""


# ── 共享 httpx 客户端 ──
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


async def close():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ── 设备请求构造工具 ──

def _json_body(payload: dict) -> bytes:
    """发往设备的 JSON 体:强制 ensure_ascii=False(固件只认原始 UTF-8)。"""
    return json.dumps(payload, ensure_ascii=False).encode()


def json_headers() -> dict:
    return dict(_JSON_HEADERS)


def estimate_parts(text: str) -> int:
    """按固件的 UCS2 单元算法估算短信段数(4 字节字符如 emoji 计 2 单元)。"""
    units = sum(2 if ord(c) > 0xFFFF else 1 for c in text)
    if units <= 70:
        return 1
    return max(1, math.ceil(units / 60))


def _redact(url: str) -> str:
    """日志里隐藏 token 段。"""
    return url.rsplit("/", 1)[0] + "/…" if "/" in url else url


# ── 身份:MAC 规范化 ──

_MAC_STRIP = re.compile(r"[\s:.\-]")


def normalize_mac(raw) -> str | None:
    """规范化 MAC 为小写无分隔 12 位十六进制。非法返回 None。
    接受 aa:bb:cc:dd:ee:ff / AABBCCDDEEFF / aa-bb-cc-dd-ee-ff 等。"""
    if not raw:
        return None
    s = _MAC_STRIP.sub("", str(raw)).lower()
    if len(s) == 12 and all(c in "0123456789abcdef" for c in s):
        return s
    return None


def display_mac(mac: str) -> str:
    """aabbccddeeff -> aa:bb:cc:dd:ee:ff(假定已规范化)。"""
    return ":".join(mac[i:i + 2] for i in range(0, 12, 2)) if mac else ""


# ── 身份:sim_id 派生(完整 IMSI 仅在此瞬时使用,不外泄) ──

def derive_sim_id(imsi: str) -> tuple[str, str, str] | None:
    """由完整 IMSI 派生 (sim_id, imsi_hash, imsi_tail)。
    sim_id = sim_<sha256(imsi)[0:16]>;imsi_hash = sha256(imsi) 全量(去重用);
    imsi_tail = 末 4 位。IMSI 非法(非数字/过短)返回 None。"""
    if not imsi:
        return None
    s = str(imsi).strip()
    if not s.isdigit() or len(s) < 6:
        return None
    digest = hashlib.sha256(s.encode()).hexdigest()
    return ("sim_" + digest[:16], digest, s[-4:])


def temp_sim_id(mac: str) -> str:
    """无 IMSI 时为物理设备生成的临时卡 id(仅当前设备下可同步)。"""
    return f"sim_tmp_{mac}"


# ── 身份脱敏:设备快照清洗(§1.5) ──

_SENSITIVE_MODEM_KEYS = {"imsi", "iccid"}


def sanitize_device_snapshot(body: dict) -> dict:
    """从设备状态快照中删除完整身份字段(imsi/iccid),只保留脱敏尾号。
    幂等:多次调用结果一致。就地修改并返回原 dict。"""
    modem = body.get("modem")
    if isinstance(modem, dict):
        # 从 imsi/iccid 提取尾号(如果 tail 字段尚未存在)
        imsi = str(modem.pop("imsi", "") or "")
        if imsi and not modem.get("imsi_tail"):
            modem["imsi_tail"] = imsi[-4:]
        iccid = str(modem.pop("iccid", "") or "")
        if iccid and not modem.get("iccid_tail"):
            modem["iccid_tail"] = iccid[-4:]
    return body


def sanitize_modem_block(modem: dict | None) -> dict | None:
    """防御性过滤:从 modem 块中删除完整 imsi/iccid,保留尾号。
    用于 API 响应前清洗(不信任 DB 历史快照已干净)。返回传入 dict 或其副本。"""
    if not isinstance(modem, dict):
        return modem
    # 浅拷贝避免影响缓存中的原始对象
    clean = {k: v for k, v in modem.items() if k not in _SENSITIVE_MODEM_KEYS}
    return clean


# ── 设备能力协商(§3.4) ──

# 能力位掩码(与 firmware/config.h 同步)
CAP_ASYNC_JOB = 1 << 0
CAP_BATCH_DELETE = 1 << 1
CAP_MIPCALL = 1 << 2
CAP_SMS_RX_WATCHDOG = 1 << 3
CAP_DELETE_QUEUE_STAT = 1 << 4
CAP_RECOVERY_REASON = 1 << 5

_CAP_LABELS = {
    CAP_ASYNC_JOB: "async_job",
    CAP_BATCH_DELETE: "batch_delete",
    CAP_MIPCALL: "mipcall",
    CAP_SMS_RX_WATCHDOG: "sms_rx_watchdog",
    CAP_DELETE_QUEUE_STAT: "delete_queue_stat",
    CAP_RECOVERY_REASON: "recovery_reason",
}


def device_capabilities(device_snapshot: dict) -> int:
    """从设备快照中提取 capability 位掩码。设备未上报时返回 0。"""
    return int(device_snapshot.get("capabilities") or 0)


def device_has_cap(device_snapshot: dict, cap: int) -> bool:
    """检查设备是否具备某项能力。"""
    return bool(device_capabilities(device_snapshot) & cap)


def device_protocol_version(device_snapshot: dict) -> int:
    """从设备快照中提取协议版本。设备未上报时返回 0(无版本)。"""
    return int(device_snapshot.get("protocol_version") or 0)


def describe_capabilities(mask: int) -> list[str]:
    """将能力位掩码转为可读标签列表(用于诊断/日志)。"""
    return [label for bit, label in _CAP_LABELS.items() if mask & bit]


# ── SSRF 防护 ──

def validate_device_addr(ip: str, port: int, hub_self: set[str], *,
                         allow_loopback: bool = False) -> str:
    """校验设备上报的 ip:port,返回可用的 base_url,非法 raise ValueError。
    规则(§6.2):仅 RFC1918(IPv4)/ ULA fc00::/7(IPv6);拒 loopback(除非
    allow_loopback,供 demo 栈同机)/unspecified/multicast/link-local(含
    169.254.169.254)/reserved/公网/Hub 自身监听地址+端口/非法端口。
    只接受字面量 IP(拒主机名,防 DNS rebinding)。"""
    try:
        p = int(port)
    except (TypeError, ValueError):
        raise ValueError("端口非法")
    if not (1 <= p <= 65535):
        raise ValueError("端口超出范围")
    try:
        addr = ipaddress.ip_address(str(ip))
    except ValueError:
        raise ValueError("IP 地址非法(仅接受字面量 IP)")
    if addr.is_unspecified or addr.is_multicast or addr.is_link_local or addr.is_reserved:
        raise ValueError("地址不在可信局域网范围")
    if addr.is_loopback:
        if not allow_loopback:
            raise ValueError("地址不在可信局域网范围")
    elif not addr.is_private:
        raise ValueError("地址不在可信局域网范围")
    # Hub 自身监听地址+端口(自调用风险)
    if str(addr) in hub_self and p == config.LISTEN_PORT:
        raise ValueError("地址指向 Hub 自身")
    if p != 80:
        return f"http://{addr}:{p}/{config.DEVICE_TOKEN}"
    return f"http://{addr}/{config.DEVICE_TOKEN}"


def compute_hub_self_addrs() -> set[str]:
    """启动时探测 Hub 自身监听地址(SSRF 防护用)。best-effort,失败返回已得集合。"""
    out: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # 不实际发包:UDP connect 仅用于让协议栈选出主出口 IP
            s.connect(("8.8.8.8", 80))
            out.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), config.LISTEN_PORT):
            out.add(info[4][0])
    except OSError:
        pass
    return out
