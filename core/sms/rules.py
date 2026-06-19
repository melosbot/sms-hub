"""号码黑名单。规则逗号分隔:完整号码精确匹配,纯前缀(如 1069)前缀匹配。"""
from core.infra import config
from core.sms import phone as phone_module


def _normalize(phone: str) -> str:
    return phone_module.canonicalize(phone)


def _candidates(phone: str) -> set[str]:
    raw = str(phone or "").strip().replace(" ", "").replace("-", "")
    out = {raw, _normalize(raw)}
    if raw.startswith("+86"):
        out.add(raw[3:])
    return {x for x in out if x}


def is_admin(sender: str) -> bool:
    """检查发件人是否为管理员手机号。"""
    if not config.ADMIN_PHONE:
        return False
    return _normalize(sender) == _normalize(config.ADMIN_PHONE)


def is_blocked(sender: str) -> bool:
    s_raw = sender.strip()
    senders = _candidates(s_raw)
    for rule_raw in config.BLACKLIST:
        rule = _normalize(rule_raw)
        if not rule:
            continue
        rules = _candidates(rule_raw)
        if rule.endswith("*"):
            prefix = rule[:-1]
            if any(s.startswith(prefix) for s in senders):
                return True
        elif senders & rules:
            return True
        elif len(rule) < 11 and any(s.startswith(rule) for s in senders):
            # 短于完整手机号的规则按前缀匹配(106/955 类服务号段)
            return True
    return False
