"""MMS 彩信通知解析(WAP Push over SMS)。

固件识别 WAP Push(UDH port 2948)后上报原始 PDU hex(每片标记 mms)。
本模块负责:
- parse_udh:提 concat 分片信息(ref/seq/total)+ WAP port + payload 片段;
- decode_mms_notification:拼齐 payload 后提下载 URL / 大小。

只识别通知,不下载彩信内容(那需运营商 MMS APN,超出 sms-hub 范畴)。
偏移逻辑移植自 firmware.ino isMmsNotificationPdu(615-670)。
"""
import re


def _uintvar(b: bytes, pos: int) -> int:
    """WAP uintvar:每字节高 7 位为值,最高位为续传标志。"""
    val = 0
    while pos < len(b):
        byte = b[pos]
        pos += 1
        val = (val << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            break
    return val


def parse_udh(hex_str: str) -> dict | None:
    """解 SMS-DELIVER PDU hex 的 UDH。

    返回 {ref,seq,total,port,payload};非 concat 片 seq=1/total=1。
    payload 是 UDH 之后的 WAP payload 片段(供拼齐 decode)。解析失败返回 None。
    """
    try:
        b = bytes.fromhex(hex_str)
    except (ValueError, TypeError):
        return None
    if len(b) < 12:
        return None
    smsc_len = b[0]
    pos = 1 + smsc_len
    if pos + 12 >= len(b):
        return None
    pos += 1  # first octet(SMS-DELIVER)
    oa_len = b[pos]
    toa = b[pos + 1]
    pos += 2
    addr_bytes = ((oa_len * 7 + 7) // 8) if (toa & 0x70) == 0x50 else (oa_len + 1) // 2
    pos += addr_bytes
    pos += 1  # PID
    pos += 1  # DCS
    pos += 7  # SCTS
    if pos >= len(b):
        return None
    pos += 1  # UDL
    ud_start = pos
    udhl = b[pos]
    pos += 1
    udh_end = ud_start + 1 + udhl
    if udh_end > len(b):
        return None
    ie_pos = ud_start + 1
    ref = seq = total = None
    port = None
    while ie_pos + 1 < udh_end:
        iei = b[ie_pos]
        ie_len = b[ie_pos + 1]
        ie_pos += 2
        if ie_pos + ie_len > udh_end:
            break
        if iei == 0x00 and ie_len == 3:        # concat,8-bit ref
            ref, total, seq = b[ie_pos], b[ie_pos + 1], b[ie_pos + 2]
        elif iei == 0x08 and ie_len == 4:      # concat,16-bit ref
            ref = (b[ie_pos] << 8) | b[ie_pos + 1]
            total, seq = b[ie_pos + 2], b[ie_pos + 3]
        elif iei == 0x05 and ie_len == 4:      # application port
            port = (b[ie_pos] << 8) | b[ie_pos + 1]
        ie_pos += ie_len
    if ref is None:
        ref, total, seq = 0, 1, 1
    return {"ref": ref, "seq": seq, "total": total, "port": port, "payload": b[udh_end:]}


def decode_mms_notification(parts: list[tuple[int, bytes]]) -> dict:
    """拼齐 concat 片的 WAP payload,提取下载 URL 与彩信大小。

    parts: [(seq, payload_bytes), ...]。最小解析——不做完整 WBXML 树,
    只扫 Content-Location(URL,ASCII)与 X-Mms-Message-Size(0x8E 后 uintvar)。
    """
    payload = b"".join(p for _, p in sorted(parts))
    m = re.search(rb"https?://[\x20-\x7e]+", payload)
    url = m.group().decode("ascii", "replace").rstrip("\x00") if m else ""
    size = 0
    for i in range(len(payload) - 1):
        if payload[i] == 0x8E:  # X-Mms-Message-Size
            size = _uintvar(payload, i + 1)
            break
    return {"url": url, "size": size}
