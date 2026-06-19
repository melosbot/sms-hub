"""sim_id 解析工具:浏览器卡片业务 API 共用(§6.4)。

只有一张启用 SIM 卡时自动推断 sim_id;启用卡数大于 1 且请求无法推断时返回 400。
"""
from fastapi import HTTPException

from core.infra import db


async def resolve_sim_id_param(explicit: str | None) -> str:
    """返回确定的 sim_id(或 'all'/'online')。无法推断时 raise 400。

    - 'all'                -> 'all'(全部卡片时间线)
    - 'online'             -> 'online'(仅在线卡)
    - 显式有效 sim_id      -> 该 sim_id
    - 缺省且恰 1 启用卡    -> 该 sim_id(自动推断)
    - 缺省且 0 或 >1 启用卡-> 400
    """
    if explicit in ("all", "online"):
        return explicit
    if explicit:
        if not await db.get_sim(explicit):
            raise HTTPException(status_code=400, detail="sim_id 缺失或无效")
        return explicit
    enabled = [s for s in await db.list_sims() if s["enabled"]]
    if len(enabled) == 1:
        return enabled[0]["sim_id"]
    raise HTTPException(status_code=400, detail="sim_id 缺失或无效")


async def online_sim_ids() -> list[str]:
    """设备在线的卡片 sim_id 列表(单次 JOIN,避免逐卡 get_device 的 N+1)。"""
    from core.device import manager  # 延迟导入避免循环
    async with db.db().execute(
        "SELECT s.sim_id AS sim_id, d.* FROM sims s"
        " LEFT JOIN devices d ON d.mac=s.current_device_mac"
    ) as cur:
        rows = await cur.fetchall()
    return [
        r["sim_id"] for r in rows
        if r["current_device_mac"] and manager.compute_liveness(r)["overall_online"]
    ]


async def resolve_device_mac_for_sim(sim_id: str) -> str:
    """sim_id -> 当前承载设备 mac。无承载设备时 raise 409。"""
    sim = await db.get_sim(sim_id)
    if not sim:
        raise HTTPException(status_code=400, detail="sim_id 缺失或无效")
    mac = sim["current_device_mac"] or ""
    if not mac:
        raise HTTPException(status_code=409, detail="该卡片当前无承载设备")
    return mac
