"""Device keepalive traffic uses ML307 MIPCALL application connections."""

import asyncio

import pytest

from core.device import keepalive
from core.infra import config


def test_keepalive_opens_pings_and_closes_mipcall(monkeypatch):
    commands = []

    class Runtime:
        mac = "aabbccddeeff"

        async def at(self, command, timeout_ms):
            commands.append((command, timeout_ms))
            if command.startswith("AT+MPING"):
                return {"ok": True, "response": "+MPING: 1,0,31\nOK"}
            return {"ok": True, "response": "OK"}

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(keepalive.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(config, "KEEPALIVE_PING_HOST", "1.1.1.1")

    result = asyncio.run(keepalive._run_for_device(Runtime()))

    assert [command for command, _ in commands] == [
        "AT+MIPCALL=1,1",
        'AT+MPING="1.1.1.1",10,1',
        "AT+MIPCALL=0,1",
    ]
    assert "保号流量已发起" in result


def test_keepalive_closes_mipcall_when_ping_raises(monkeypatch):
    commands = []

    class Runtime:
        mac = "aabbccddeeff"

        async def at(self, command, timeout_ms):
            commands.append(command)
            if command.startswith("AT+MPING"):
                raise RuntimeError("device timeout")
            return {"ok": True, "response": "OK"}

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(keepalive.asyncio, "sleep", no_sleep)
    with pytest.raises(RuntimeError, match="device timeout"):
        asyncio.run(keepalive._run_for_device(Runtime()))

    assert commands[-1] == "AT+MIPCALL=0,1"
