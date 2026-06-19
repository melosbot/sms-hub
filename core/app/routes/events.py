"""Server-sent event routes."""
import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from core.app import auth
from core.infra import events

router = APIRouter()


@router.get("/api/events")
async def sse_events(token: str = ""):
    """新短信/设备状态的实时推送。EventSource 无法带 header,token 走查询参数。"""
    if not auth.verify_token(token):
        raise HTTPException(status_code=401)

    q = events.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
