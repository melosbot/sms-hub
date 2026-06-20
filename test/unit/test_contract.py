"""API 契约防漂移测试(v2 发布前新增)。

三层契约,任何一层失配都说明「代码 / 文档 / 前端」三者之一漂移了:

1. **Hub API**:`docs/openapi.yaml` 中由 Hub 提供的路径(/api/*、/hook/*)
   必须 **==** 运行时 FastAPI `app.openapi()` 生成的路径集合。
2. **前端调用**:`web/src/lib/api.ts` 发出的每个 (method, path) 都必须在 Hub 已注册——
   防止前端调了一个后端不存在或已改名的端点(404)。
3. **设备协议**:`docs/openapi.yaml` 的 `/{token}/*` 段必须覆盖 Hub 向设备发起的全部调用
   (runtime.py 的 pull/send/delete/at)——这是 Hub↔固件/mock 的双向契约文档。

注:`openapi.yaml` 同时含 Hub API 与设备端协议两类路径;设备端 `/{token}/*` 由
固件/mock 提供、不在 Hub `app.routes` 内,故测试(1)只比对 Hub 部分,测试(3)单独保证设备段完整。
"""
from pathlib import Path

import yaml

from core.main import app

REPO = Path(__file__).resolve().parents[2]
OPENAPI_YAML = REPO / "docs" / "openapi.yaml"
_METHODS = {"get", "post", "put", "patch", "delete"}


# ── 运行时真相源:FastAPI 自动生成的 OpenAPI ──

def _runtime_paths() -> set[tuple[str, str]]:
    """Hub 运行时实际注册的 (METHOD, path) 集合。"""
    spec = app.openapi()
    out: set[tuple[str, str]] = set()
    for path, ops in spec.get("paths", {}).items():
        for method in ops:
            if method.lower() in _METHODS:
                out.add((method.upper(), path))
    return out


# ── 文档真相源:docs/openapi.yaml ──

def _openapi_yaml() -> dict:
    with open(OPENAPI_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _doc_paths() -> set[tuple[str, str]]:
    spec = _openapi_yaml()
    out: set[tuple[str, str]] = set()
    for path, ops in (spec.get("paths") or {}).items():
        for method in ops:
            if method.lower() in _METHODS:
                out.add((method.upper(), path))
    return out


def _is_hub_path(path: str) -> bool:
    """Hub 提供的路径(非设备端 /{token}/*)。/hook/{token} 仍属 Hub。"""
    return not path.startswith("/{token}")


# ── 前端调用清单(源自 web/src/lib/api.ts,前端新增端点时同步此处)──

FRONTEND_CALLS: set[tuple[str, str]] = {
    ("POST", "/api/login"),
    ("GET", "/api/devices"),
    ("GET", "/api/config"),
    ("POST", "/api/config"),
    ("POST", "/api/notify/test"),
    ("GET", "/api/status"),
    ("POST", "/api/status/refresh"),
    ("POST", "/api/buffer/clear"),
    ("POST", "/api/at"),
    ("GET", "/api/messages"),
    ("GET", "/api/messages/export"),
    ("GET", "/api/messages/{msg_id}"),
    ("DELETE", "/api/messages/{msg_id}"),
    ("DELETE", "/api/messages"),
    ("POST", "/api/send"),
    ("GET", "/api/outbound"),
    ("DELETE", "/api/outbound/{ob_id}"),
    ("GET", "/api/contacts"),
    ("PUT", "/api/contacts/{phone}"),
    ("DELETE", "/api/contacts/{phone}"),
    ("PATCH", "/api/devices/{mac}"),
    ("PATCH", "/api/sims/{sim_id}"),
    ("GET", "/api/events"),
}

# Hub 向设备发起的调用(core/device/runtime.py)——设备端必须实现且 openapi.yaml 必须文档化。
HUB_TO_DEVICE_CALLS: set[tuple[str, str]] = {
    ("GET", "/{token}/pull"),
    ("POST", "/{token}/send"),
    ("POST", "/{token}/delete"),
    ("POST", "/{token}/at"),
}


# ── 测试 ──

def test_hub_openapi_matches_runtime():
    """openapi.yaml 的 Hub 路径段 == 运行时路由集合(防文档漂移)。"""
    rt = _runtime_paths()
    doc_hub = {p for p in _doc_paths() if _is_hub_path(p[1])}
    extra_in_doc = doc_hub - rt          # 文档写了 Hub 路径但运行时没有 → 过期声明
    missing_in_doc = rt - doc_hub        # 运行时有但文档没写 → 漏文档
    assert not extra_in_doc, f"openapi.yaml 声明了不存在的 Hub 路由: {sorted(extra_in_doc)}"
    assert not missing_in_doc, f"Hub 路由未写进 openapi.yaml: {sorted(missing_in_doc)}"


def test_frontend_calls_exist_in_hub():
    """前端 api.ts 调用的每个端点都必须在 Hub 运行时注册。"""
    rt = _runtime_paths()
    missing = FRONTEND_CALLS - rt
    assert not missing, (
        f"前端调用了 Hub 未注册的端点(会 404): {sorted(missing)}。\n"
        "若是新增前端调用,请在 core/app/routes/ 实现;若是改名,同步 web/src/lib/api.ts。"
    )


def test_device_protocol_documented():
    """Hub 向设备发起的调用必须在 openapi.yaml 设备段(/{token}/*)有文档。"""
    doc_dev = {p for p in _doc_paths() if not _is_hub_path(p[1])}
    missing = HUB_TO_DEVICE_CALLS - doc_dev
    assert not missing, (
        f"Hub→设备的调用未在 openapi.yaml 设备段文档化: {sorted(missing)}。\n"
        "设备端协议改动时须同步 docs/openapi.yaml 与 firmware/firmware.ino。"
    )


def test_status_routes_present():
    """status.py 的 5 个诊断端点易被遗漏(用 router.add_api_route 注册),显式点名。"""
    rt = _runtime_paths()
    for expected in [
        ("GET", "/api/status"),
        ("POST", "/api/status/refresh"),
        ("POST", "/api/buffer/clear"),
        ("POST", "/api/poll"),
        ("POST", "/api/at"),
    ]:
        assert expected in rt, f"诊断端点 {expected} 未注册"
