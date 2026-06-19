"""Login routes."""
import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.app import auth
from core.infra import config

router = APIRouter()


class LoginBody(BaseModel):
    user: str
    password: str


@router.post("/api/login")
async def login(body: LoginBody):
    if body.user == config.WEBUI_USER and secrets.compare_digest(
        body.password, config.WEBUI_PASS
    ):
        return {"token": auth.make_token(body.user)}
    raise HTTPException(status_code=401, detail="账号或密码错误")
