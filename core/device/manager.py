"""DeviceManager:多设备运行时注册表与协调者。

职责(§10):
- 启动时加载启用设备,为每台建 DeviceRuntime 并拉起独立 poll 任务。
- webhook 到达时:MAC 规范化 → SSRF 校验地址 → upsert 设备行 →
  派生/合并 sim_id → 保存状态快照 → 按规则调度拉取。
- 维护全局设备 I/O 并发信号量(默认 4),每设备 poll 任务生命周期。
- 设备启用/禁用/删除。
"""
import asyncio
import json
import logging
import time

from core.device import client
from core.device import runtime as runtime_mod
from core.infra import config
from core.infra import db

log = logging.getLogger("device")


class WebhookError(Exception):
    """webhook 输入非法(MAC/SSRF),由路由映射为 HTTP 400。"""


def compute_liveness(row) -> dict:
    """双平面在线判定(§7.2,per-device)。row 为 devices 行。"""
    now = time.time()
    hb_interval = int(row["heartbeat_interval_s"]) if row["heartbeat_interval_s"] else 60
    hb_timeout = max(hb_interval * 2.5, config.HEARTBEAT_ONLINE_FLOOR_S)
    dp_timeout = max(config.POLL_INTERVAL * 2 + 30, config.DATA_PLANE_ONLINE_FLOOR_S)
    hb_age = int(now - row["last_status_ts"]) if row["last_status_ts"] else -1
    dp_age = int(now - row["last_poll_ok_ts"]) if row["last_poll_ok_ts"] else -1
    hb_online = hb_age >= 0 and hb_age <= hb_timeout
    dp_online = dp_age >= 0 and dp_age <= dp_timeout
    return {
        "heartbeat_online": hb_online,
        "data_plane_online": dp_online,
        "overall_online": hb_online or dp_online,
        "heartbeat_age_s": hb_age,
        "poll_age_s": dp_age,
        "heartbeat_timeout_s": int(hb_timeout),
        "data_plane_timeout_s": int(dp_timeout),
    }


class DeviceManager:
    def __init__(self, *, hub_self: set[str] | None = None):
        self.runtimes: dict[str, runtime_mod.DeviceRuntime] = {}
        self.io_sem = asyncio.Semaphore(config.MAX_DEVICE_IO_CONCURRENCY)
        self.hub_self: set[str] = hub_self if hub_self is not None else set()

    # ── 启动 ──
    async def load(self) -> None:
        if not self.hub_self:
            self.hub_self = client.compute_hub_self_addrs()
            config.HUB_SELF_ADDRS = self.hub_self
        for row in await db.list_enabled_devices():
            self.runtimes[row["mac"]] = self._build_runtime(row)
        for mac in list(self.runtimes):
            await self._spawn_poll(mac)
        log.info("DeviceManager 加载 %d 台启用设备", len(self.runtimes))

    def _build_runtime(self, row) -> runtime_mod.DeviceRuntime:
        rt = runtime_mod.DeviceRuntime(row["mac"], self)
        rt.base_url = row["base_url"] or ""
        rt.last_hook_ts = float(row["last_hook_ts"] or 0)
        rt.last_poll_ok_ts = float(row["last_poll_ok_ts"] or 0)
        rt.last_status_ts = float(row["last_status_ts"] or 0)
        return rt

    # ── 全局并发池:仅包裹网络段(由 DeviceRuntime 内部调用)──
    async def with_io(self, coro_fn, *args, **kwargs):
        async with self.io_sem:
            return await coro_fn(*args, **kwargs)

    # ── 运行时注册表 ──
    def get_runtime(self, mac: str) -> runtime_mod.DeviceRuntime | None:
        return self.runtimes.get(mac)

    async def ensure_runtime(self, mac: str) -> runtime_mod.DeviceRuntime:
        rt = self.runtimes.get(mac)
        if rt is None:
            rt = runtime_mod.DeviceRuntime(mac, self)
            self.runtimes[mac] = rt
        return rt

    # ── poll 任务 ──
    async def _spawn_poll(self, mac: str) -> None:
        rt = self.runtimes.get(mac)
        if rt is None:
            return
        if rt._task is not None and not rt._task.done():
            return
        from core.device import poller  # 延迟导入打破 manager ↔ poller 循环
        rt._task = asyncio.create_task(
            poller.device_loop(self, rt), name=f"poller:{mac}"
        )

    async def _cancel_poll(self, mac: str) -> None:
        rt = self.runtimes.get(mac)
        if rt is not None and rt._task is not None:
            rt._task.cancel()
            await asyncio.gather(rt._task, return_exceptions=True)
            rt._task = None

    async def trigger_pull(self, mac: str) -> bool:
        """立即触发该设备拉取(禁用设备不会拉)。返回是否已调度。"""
        row = await db.get_device(mac)
        if not row or not row["enabled"]:
            return False
        await self._spawn_poll(mac)
        rt = self.runtimes.get(mac)
        if rt is None:
            return False
        self._schedule_pull(rt)
        return True

    def _schedule_pull(self, rt: runtime_mod.DeviceRuntime) -> None:
        if rt._pull_in_progress:
            rt.pull_again = True
        else:
            rt.trigger.set()

    # ── webhook 主入口 ──
    async def handle_webhook(self, body: dict, peer_ip: str = "") -> dict:
        mac = client.normalize_mac(body.get("mac"))
        if not mac:
            raise WebhookError("缺少或非法的 mac")
        event = str(body.get("event", "")).strip()
        now = time.time()

        # 1) SSRF 校验并学习地址
        new_base = None
        ip = body.get("ip")
        if ip:
            try:
                new_base = client.validate_device_addr(
                    str(ip), body.get("port", 80), self.hub_self,
                    allow_loopback=config.ALLOW_LOOPBACK_DEVICE,
                )
            except ValueError as e:
                raise WebhookError(str(e))
            if peer_ip and peer_ip != str(ip):
                log.warning("设备 %s 自报 IP %s 与来源 %s 不一致", mac, ip, peer_ip)

        # 2) upsert 设备行
        hb_interval = body.get("heartbeat_interval_s")
        await db.upsert_device(
            mac,
            base_url=new_base,
            heartbeat_interval_s=int(hb_interval) if hb_interval else None,
            commit=True,
        )
        row = await db.get_device(mac)
        cursor = int(row["cursor"]) if row else 0

        # 3) 状态快照(boot/hello/heartbeat 携带完整状态)
        is_status_event = event in ("boot", "hello", "heartbeat") or bool(body.get("modem"))
        if is_status_event:
            await db.update_device_status_snapshot(mac, json.dumps(body, ensure_ascii=False), now, commit=True)

        # 4) 派生/合并 sim_id(boot/hello/heartbeat 的 modem.imsi 为权威 hint)
        modem = body.get("modem") or {}
        if modem.get("imsi"):
            await self.derive_and_merge_sim(mac, modem, commit=True)

        # 5) 运行时状态
        rt = await self.ensure_runtime(mac)
        if new_base:
            rt.base_url = new_base
        rt.last_hook_ts = now
        if is_status_event:
            rt.last_status_ts = now
        await db.update_device_timestamps(mac, last_hook_ts=now,
                                          last_status_ts=now if is_status_event else None,
                                          commit=True)

        # 6) 调度拉取(D6):禁用设备记录一切但不拉取
        pull_scheduled = False
        if row and row["enabled"]:
            schedule = False
            if event in ("boot", "hello", "sms"):
                schedule = True
            elif event == "heartbeat":
                buf = body.get("buffer") or {}
                latest = int(buf.get("latest_id", cursor))
                schedule = latest > cursor
            if schedule:
                await self._spawn_poll(mac)
                self._schedule_pull(rt)
                pull_scheduled = True

        log.info("webhook mac=%s event=%s pull=%s", mac, event or "?", pull_scheduled)
        return {"ok": True, "pull_scheduled": pull_scheduled}

    # ── 身份:sim_id 派生与临时卡合并(D3)──
    async def derive_and_merge_sim(self, mac: str, modem: dict, *, commit: bool = True) -> str:
        """由 modem.imsi 派生稳定 sim_id,upsert sims,合并该设备的临时卡。
        IMSI 非法时返回空串(调用方回退到临时卡)。完整 IMSI 不持久化。"""
        derived = client.derive_sim_id(modem.get("imsi") or "")
        if not derived:
            return ""
        sim_id, imsi_hash, imsi_tail = derived
        iccid = str(modem.get("iccid") or "")
        iccid_tail = iccid[-4:] if iccid else ""
        await db.upsert_sim(
            sim_id,
            identity_source="imsi",
            current_device_mac=mac,
            imsi_hash=imsi_hash,
            imsi_tail=imsi_tail,
            iccid_tail=iccid_tail,
            msisdn=str(modem.get("msisdn") or ""),
            operator=str(modem.get("operator") or ""),
            commit=commit,
        )
        # 合并该设备的临时卡业务到稳定卡
        temp_id = client.temp_sim_id(mac)
        if await db.get_sim(temp_id):
            await db.reassign_sim_business(temp_id, sim_id, commit=commit)
        await db.set_device_current_sim(mac, sim_id, commit=commit)
        return sim_id

    # ── 启用/禁用/删除 ──
    async def set_device_enabled(self, mac: str, enabled: bool) -> None:
        await db.set_device_enabled(mac, enabled, commit=True)
        if enabled:
            await self._spawn_poll(mac)
            rt = self.runtimes.get(mac)
            if rt:
                self._schedule_pull(rt)
        else:
            await self._cancel_poll(mac)


# ── 模块单例 ──
_singleton: DeviceManager | None = None


def get() -> DeviceManager:
    assert _singleton is not None, "DeviceManager 未初始化"
    return _singleton


def set_manager(m: DeviceManager) -> None:
    global _singleton
    _singleton = m
