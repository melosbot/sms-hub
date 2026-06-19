"""UI 事件总线:poller 发布,/api/events(SSE)广播给浏览器。"""
import asyncio
import logging

log = logging.getLogger("events")

listeners: set[asyncio.Queue] = set()


def publish(ev: dict):
    # put_nowait 同步不 yield,迭代期间 listeners 不会变,无需拷贝。
    for q in listeners:
        try:
            q.put_nowait(ev)
        except Exception:
            pass


def subscribe() -> asyncio.Queue:
    # v2 多设备:每设备独立 poll,突发时事件更多,队列调大到 128(D11)。
    q: asyncio.Queue = asyncio.Queue(maxsize=128)
    listeners.add(q)
    return q


def unsubscribe(q: asyncio.Queue):
    listeners.discard(q)
