"""登录令牌:HMAC 签名的 user:exp:sig,无第三方 JWT 依赖。"""
import hashlib
import hmac
import time

from fastapi import Header, HTTPException

from core.infra import config


def make_token(user: str) -> str:
    exp = str(int(time.time()) + config.TOKEN_TTL_S)
    sig = hmac.new(
        config.JWT_SECRET.encode(), f"{user}:{exp}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{user}:{exp}:{sig}"


def verify_token(token: str) -> bool:
    try:
        user, exp, sig = token.rsplit(":", 2)
        if int(exp) < time.time():
            return False
    except ValueError:  # 格式不对或 exp 不是数字
        return False
    expect = hmac.new(
        config.JWT_SECRET.encode(), f"{user}:{exp}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expect)


async def require_auth(authorization: str = Header(default="")):
    """FastAPI 依赖:校验 Authorization: Bearer <token>。"""
    if not authorization.startswith("Bearer ") or not verify_token(authorization[7:]):
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
