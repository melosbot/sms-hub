"""Notification test route:对指定通道即时发一条测试通知(不入队、不写库)。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.app import auth
from core.notify import notifier

router = APIRouter(dependencies=[Depends(auth.require_auth)])


class TestBody(BaseModel):
    channel: str


@router.post("/api/notify/test")
async def notify_test(body: TestBody):
    """测试通道是否可达。返回 {ok, error}。"""
    return await notifier.test_channel(body.channel)
