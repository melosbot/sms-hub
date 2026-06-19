"""MMS 通知解析:UDH concat + WAP payload 解码。fixture 来自真实「PDU 解码失败」hex。"""
from core.sms import mms

# 同一 MMS 通知的两片 concat(ref=0x62,port=0x0B84 WAP Push)
PART1 = (
    "0891683108200805F04408A1212510510004626081710180238C0B05040B8423F0000362"
    "02010706246170706C69636174696F6E2F766E642E7761702E6D6D732D6D657373616765"
    "00B487AF848C82984E2E32774D4A415239447245008D908A808E04001848318805810303F4"
    "8183687474703A2F2F3231382E3230302E3233362E3135313A38302F32774D4A4152394472"
    "450089178031303038363430303838382F545950"
)
PART2 = (
    "0891683108200805F06408A121251051000462608171018023130B05040B8423F0000362"
    "0202453D504C4D4E00"
)


def test_parse_udh_concat_part1():
    u = mms.parse_udh(PART1)
    assert u is not None
    assert u["ref"] == 0x62
    assert u["total"] == 2
    assert u["seq"] == 1
    assert u["port"] == 0x0B84  # WAP Push dest port


def test_parse_udh_concat_part2():
    u = mms.parse_udh(PART2)
    assert u is not None
    assert u["ref"] == 0x62
    assert u["total"] == 2
    assert u["seq"] == 2


def test_decode_mms_notification_url_and_size():
    p1 = mms.parse_udh(PART1)
    p2 = mms.parse_udh(PART2)
    parts = [(p1["seq"], p1["payload"]), (p2["seq"], p2["payload"])]
    info = mms.decode_mms_notification(parts)
    assert info["url"] == "http://218.200.236.151:80/2wMJAR9DrE"
    assert info["size"] > 0


def test_parse_udh_invalid():
    assert mms.parse_udh("not-hex") is None
    assert mms.parse_udh("") is None
    assert mms.parse_udh("00") is None
