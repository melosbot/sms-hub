"""Device webhook route request parsing tests."""

import asyncio
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from core.app.routes import webhook
from core.infra import config


def _request(raw_body: bytes) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw_body, "more_body": False}

    return Request({
        "type": "http",
        "method": "POST",
        "path": "/hook/test-token",
        "headers": [(b"content-type", b"application/json")],
        "client": ("10.0.0.216", 12345),
    }, receive)


@pytest.mark.parametrize(
    ("raw_body", "detail"),
    [
        (b"", "请求体不是合法 JSON"),
        (b'{"event":"heartbeat" "mac":"aabbccddeeff"}', "请求体不是合法 JSON"),
        (b"[]", "请求体必须是 JSON 对象"),
        (b"null", "请求体必须是 JSON 对象"),
        (b'["mac"]', "请求体必须是 JSON 对象"),
    ],
)
def test_hook_reports_invalid_json_body(monkeypatch, raw_body, detail):
    monkeypatch.setattr(config, "DEVICE_TOKEN", "test-token")

    async def run():
        with pytest.raises(HTTPException) as exc_info:
            await webhook.hook("test-token", _request(raw_body))
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == detail

    asyncio.run(run())


def test_hook_passes_json_object_to_manager(monkeypatch):
    monkeypatch.setattr(config, "DEVICE_TOKEN", "test-token")
    received = {}

    class Manager:
        async def handle_webhook(self, body, peer):
            received.update(body=body, peer=peer)
            return {"ok": True, "pull_scheduled": False}

    monkeypatch.setattr(webhook.device_manager, "get", lambda: Manager())
    body = {"event": "heartbeat", "mac": "aabbccddeeff"}

    result = asyncio.run(webhook.hook(
        "test-token", _request(json.dumps(body).encode())
    ))

    assert result == {"ok": True, "pull_scheduled": False}
    assert received == {"body": body, "peer": "10.0.0.216"}
