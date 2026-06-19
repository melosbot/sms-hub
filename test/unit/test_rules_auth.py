"""黑名单规则与登录令牌的单元测试。"""
from core.infra import config
from core.sms import phone
from core.sms import rules
from core.app import auth


def test_phone_canonicalize_mainland_mobile_only():
    assert phone.canonicalize("+8613800138000") == "13800138000"
    assert phone.canonicalize("138-0013-8000") == "13800138000"
    assert phone.canonicalize("+8610690329") == "+8610690329"
    assert phone.canonicalize("+85261234567") == "+85261234567"


def test_blacklist(monkeypatch):
    monkeypatch.setattr(
        config, "BLACKLIST", ["1069", "+8613800138000", "95533*"]
    )
    assert rules.is_blocked("10690329")            # 短前缀
    assert rules.is_blocked("+8610690000")         # +86 归一化后前缀
    assert rules.is_blocked("13800138000")         # 完整号码(规则带 +86)
    assert rules.is_blocked("+8613800138000")
    assert rules.is_blocked("9553301")             # 显式通配
    assert not rules.is_blocked("13912345678")
    assert not rules.is_blocked("95588")


def test_blacklist_empty(monkeypatch):
    monkeypatch.setattr(config, "BLACKLIST", [])
    assert not rules.is_blocked("10690329")


def test_token_roundtrip():
    token = auth.make_token("admin")
    assert auth.verify_token(token)


def test_token_malformed():
    # 任何垃圾输入都应返回 False 而不是抛异常(曾导致 500)
    for bad in ["", "garbage", "a:b:c", "a:b", ":::", "user:notanumber:sig"]:
        assert auth.verify_token(bad) is False


def test_token_expired(monkeypatch):
    monkeypatch.setattr(config, "TOKEN_TTL_S", -10)
    assert auth.verify_token(auth.make_token("admin")) is False


def test_token_tampered():
    token = auth.make_token("admin")
    user, exp, sig = token.rsplit(":", 2)
    assert auth.verify_token(f"{user}:{exp}:{'0' * len(sig)}") is False
    assert auth.verify_token(f"other:{exp}:{sig}") is False
