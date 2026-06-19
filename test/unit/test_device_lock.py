"""Device I/O scheduling tests (v2:per-device DeviceRuntime 实例串行化器)。"""
import asyncio

import pytest

from core.device import client
from core.device import runtime as runtime_mod


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "response": "OK"}


class BlockingClient:
    def __init__(self, started, release):
        self.started = started
        self.release = release

    async def post(self, *_a, **_k):
        self.started.set()
        await self.release.wait()
        return FakeResponse()


class PriorityClient:
    def __init__(self, started, release):
        self.started = started
        self.release = release
        self.calls: list[str] = []

    async def post(self, *_a, **_k):
        self.calls.append("post")
        if len(self.calls) == 1:
            self.started.set()
            await self.release.wait()
        return FakeResponse()

    async def get(self, *_a, **_k):
        self.calls.append("get")
        return FakeResponse()


class FakeManager:
    """with_io 直通(无信号量),用于隔离测试每设备串行化器。"""
    async def with_io(self, fn, *a, **k):
        return await fn(*a, **k)


def test_interactive_at_fails_fast_when_device_busy(monkeypatch):
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        monkeypatch.setattr(client, "client", lambda: BlockingClient(started, release))
        rt = runtime_mod.DeviceRuntime("aabbccddeeff", FakeManager())
        rt.base_url = "http://dev/token"

        task = asyncio.create_task(rt.at("AT", wait_busy=True))
        await started.wait()

        with pytest.raises(client.DeviceBusy):
            await rt.at("AT+CESQ", wait_busy=False)
        assert rt.busy_operation() == "AT 命令"

        release.set()
        assert await task == {"ok": True, "response": "OK"}
        assert rt.busy_operation() == ""

    asyncio.run(run())


def test_sms_receive_jumps_ahead_of_waiting_at(monkeypatch):
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        fake = PriorityClient(started, release)
        monkeypatch.setattr(client, "client", lambda: fake)
        rt = runtime_mod.DeviceRuntime("aabbccddeeff", FakeManager())
        rt.base_url = "http://dev/token"

        active_at = asyncio.create_task(rt.at("AT+SLOW", wait_busy=True))
        await started.wait()
        waiting_at = asyncio.create_task(rt.at("AT+WAIT", wait_busy=True))
        sms_pull = asyncio.create_task(rt.pull(after=0))

        while rt._sms_waiters < 1:
            await asyncio.sleep(0)
        release.set()

        await asyncio.gather(active_at, waiting_at, sms_pull)
        # 先占用 AT(post) → SMS 拉取插队(get) → 排队的 AT 才执行(post)
        assert fake.calls == ["post", "get", "post"]

    asyncio.run(run())
