"""DeviceRuntime:每台物理瘦终端的运行时(I/O 串行化 + 设备调用)。

v2 用 per-instance 状态替代 v1 的模块全局:每台设备有独立的
`asyncio.Condition` 串行化器(SMS>control>AT 优先级)、独立 `trigger` 事件、
`pull_again` 合并标记与忙碌操作名。跨设备并发由 `DeviceManager.io_sem`
(默认 4)限制,**只包裹每次调用的最内层网络段**(设备在自身 cond 上排队等待时
不占全局槽)。

约定:本类的 pull/send/delete/at 已自行用 `mgr.with_io(net)` 包裹网络段。
**外部调用方直接调用这些方法,不得再外套 `manager.with_io`**(会重复获取信号量死锁)。
"""
import asyncio
import logging

from core.device import client
from core.infra import config

log = logging.getLogger("device")


class DeviceRuntime:
    # 优先级:短信收发最高,控制/诊断次之,AT 最低。
    P_SMS = 0
    P_CONTROL = 1
    P_AT = 2

    def __init__(self, mac: str, manager):
        self.mac = mac
        self._mgr = manager
        self.base_url: str = ""
        self.trigger: asyncio.Event = asyncio.Event()
        # pull 合并:webhook 在拉取进行中到达时置位,本轮结束后补一轮。
        self.pull_again: bool = False
        self._pull_in_progress: bool = False
        # I/O 串行化状态(原样移植 v1 client._serialized 算法为实例字段)。
        self._cond: asyncio.Condition = asyncio.Condition()
        self._busy_op: str = ""
        self._sms_waiters: int = 0
        # 在线/告警状态(poller 维护)。
        self.last_poll_ok_ts: float = 0.0
        self.last_hook_ts: float = 0.0
        self.last_status_ts: float = 0.0
        self.consecutive_fails: int = 0
        self._alerted_down: bool = False
        # 该设备的 poll 任务,由 manager 持有。
        self._task: asyncio.Task | None = None

    # ── 串行化器(每设备实例版)──
    def busy_operation(self) -> str:
        return self._busy_op

    async def _serialized(self, operation: str, fn, *, priority: int, wait_busy: bool = True):
        """串行化本设备 I/O,并让 SMS 收发插到 AT/控制前面。"""
        registered_sms = priority == self.P_SMS
        async with self._cond:
            if registered_sms:
                self._sms_waiters += 1
            try:
                if not wait_busy:
                    if self._busy_op:
                        raise client.DeviceBusy(f"设备忙: 正在执行 {self._busy_op}")
                    if priority > self.P_SMS and self._sms_waiters > 0:
                        raise client.DeviceBusy("设备忙: 短信收发优先")
                while self._busy_op or (priority > self.P_SMS and self._sms_waiters > 0):
                    await self._cond.wait()
                if registered_sms:
                    self._sms_waiters -= 1
                    registered_sms = False
                self._busy_op = operation
            finally:
                if registered_sms:
                    self._sms_waiters -= 1
                    self._cond.notify_all()

        try:
            return await fn()
        finally:
            async with self._cond:
                self._busy_op = ""
                self._cond.notify_all()

    def _require_base(self) -> str:
        if not self.base_url:
            raise client.DeviceUnknown("设备地址未知:等待设备上线发送 webhook")
        return self.base_url

    # ── 设备调用(均自行包裹 mgr.with_io 的网络段)──

    async def pull(self, *, after: int, limit: int = 20, include_status: bool = False) -> dict:
        params = {
            "after": after,
            "limit": limit,
            "include_status": "1" if include_status else "0",
        }
        base = self._require_base()

        async def net():
            r = await client.client().get(f"{base}/pull", params=params)
            r.raise_for_status()
            return r.json()

        return await self._serialized(
            "拉取短信", lambda: self._mgr.with_io(net), priority=self.P_SMS
        )

    async def status_pull(self, *, after: int) -> dict:
        """手动刷新状态:include_status=1,顺带至多 1 条消息。"""
        return await self.pull(after=after, limit=1, include_status=True)

    async def send(self, to: str, text: str) -> dict:
        # 固件单段最坏约 36s,按段数给足超时,避免"hub 超时但设备已发"假失败。
        timeout = 20.0 + 36.0 * client.estimate_parts(text)
        base = self._require_base()

        async def net():
            r = await client.client().post(
                f"{base}/send",
                content=client._json_body({"to": to, "text": text}),
                headers=client.json_headers(),
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json()

        return await self._serialized(
            "发送短信", lambda: self._mgr.with_io(net), priority=self.P_SMS
        )

    async def delete(self, device_msg_ids: list[int]) -> dict:
        base = self._require_base()

        async def net():
            r = await client.client().post(
                f"{base}/delete",
                content=client._json_body({"device_msg_ids": [int(i) for i in device_msg_ids]}),
                headers=client.json_headers(),
                timeout=8.0,
            )
            r.raise_for_status()
            return r.json()

        return await self._serialized(
            "删除设备缓存", lambda: self._mgr.with_io(net), priority=self.P_CONTROL
        )

    async def at(self, cmd: str, timeout_ms: int = 3000, *, wait_busy: bool = True) -> dict:
        base = self._require_base()

        async def net():
            r = await client.client().post(
                f"{base}/at",
                content=client._json_body({"cmd": cmd, "timeout_ms": int(timeout_ms)}),
                headers=client.json_headers(),
                timeout=timeout_ms / 1000 + 5,
            )
            r.raise_for_status()
            return r.json()

        return await self._serialized(
            "AT 命令", lambda: self._mgr.with_io(net), priority=self.P_AT, wait_busy=wait_busy
        )
