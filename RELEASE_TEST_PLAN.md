# sms-hub v2 发布前测试计划与执行报告

> 本文件是 v2 发布前的测试计划 + 执行结果。覆盖前端与后端的契约一致性、后端合理性、终端（固件/mock）处理、文档同步、发布物可构建性，以及探索阶段甄别的误报。
>
> 状态图例：✅ 已完成且通过 · ⚠️ 受环境/硬件限制待补 · ➖ 可选补强

## 目录

- [一、测试范围与验收 Gate](#一测试范围与验收-gate)
- [二、测试矩阵（7 域）](#二测试矩阵7-域)
- [三、误报甄别（避免错误返工）](#三误报甄别避免错误返工)
- [四、剩余项与执行建议](#四剩余项与执行建议)

---

## 一、测试范围与验收 Gate

用户要求覆盖：前端各项请求在后端是否存在且一致、后端各项请求是否合理、终端是否处理得当、文档是否按最终代码修正、其他应完成项（发布物、安全、真实机回归）。

验收 Gate（发布准入）：

| # | 项 | 状态 |
|---|---|---|
| 1 | `pytest -q test/unit` 全绿（含新增契约测试） | ✅ 117 passed |
| 2 | 浏览器冒烟全绿、零 console/page error | ✅ 12/12 |
| 3 | 双设备 demo 栈端到端：收/发/通知/删除/多卡切换全通 | ✅ |
| 4 | API 契约：`openapi.yaml` 与运行时零漂移、前端调用全注册 | ✅ |
| 5 | 文档失真归零（全仓 `grep` 无旧引用残留） | ✅ |
| 6 | `docker compose up -d --build` 成功、UI 可用 | ⚠️ 需部署机（本机无 compose + 无外网） |
| 7 | 真机回归关键项（长短信/重启恢复/失联恢复） | ⚠️ 需硬件手测 |

---

## 二、测试矩阵（7 域）

### 域 1 · 前后端 API 契约一致性 ✅

目标：前端 `web/src/lib/api.ts` 调用的每个端点后端都存在、字段/类型/枚举一致。

方法：新增 `test/unit/test_contract.py`，三层断言：
1. `docs/openapi.yaml` 的 Hub 路径段 == 运行时 `app.openapi()` 路径（防文档漂移）；
2. 前端 23 个调用（method+path）都在 Hub 已注册（防 404）；
3. 设备协议段（`/{token}/*`）覆盖 Hub 向设备的全部调用；status 诊断路由点名。

结果：4 测试全绿。SSE 事件名/字段（`new_messages`/`device`/`outbound`）、鉴权（Bearer header + SSE query token）、分页参数（`sim_id`/`q`/`limit`/`offset`、特殊值 `all`/`online`）前后端一致。无 🔴/🟡 契约 bug。

执行：`.local/venv/bin/python -m pytest -q test/unit/test_contract.py`

### 域 2 · 终端协议（固件 + mock） ✅（真机部分 ⚠️）

目标：Hub 与瘦终端（固件/mock）双向通信契约正确。

方法：对照 `core/device/runtime.py` 发出的请求与 `firmware/firmware.ino` 路由和 `test/demo/mock_device.py` 实现。

结果：✅ 五个设备端点全对齐：`GET /{token}/pull`（after/limit/include_status）、`POST /{token}/send`（to/text）、`POST /{token}/delete`（device_msg_ids）、`POST /{token}/at`（cmd/timeout_ms）、`GET /{token}/status`。Hub 到设备所有请求体走 `client._json_body()`（`ensure_ascii=False`，固件极简解析器只认原始 UTF-8）。相关单测 `test_poller`/`test_sender`/`test_device`/`test_device_lock` 全绿。

真机手测（⚠️，mock 覆盖不了）：多段长短信合并、NVS 重启恢复、SIM 存储满清理、设备失联/恢复、webhook 丢失靠 `POLL_INTERVAL` 兜底。按 [`docs/operations.md`](docs/operations.md) 真机回归清单执行。

### 域 3 · 文档与最终代码同步 ✅

目标：文档反映最终代码，无 v1 遗留失真。

方法：`grep` 全仓扫描 + 逐条读代码核实。

结果：✅ 修正一批失真：`docs/architecture.md` 整篇曾是 v1 视角（接口名 `/get`→`/pull`、`/del`→`/delete`；表名 `deleted_gw_ids`→`deleted_messages`；文件名 `device.py`→`device/runtime.py`；数据模型 `gw_id` 单列→`(device_mac, gw_epoch, device_msg_id)` 复合键；schema 基线 3→4）；`CLAUDE.md`/`AGENTS.md`/`README.md`/`docs/development.md` 的 `core/static`、`PWA`、`design-system.md`、web gitignore 等过时引用清理。

后续：文档进一步合并精简为 `docs/guide.md`（设计+接口）+ `docs/operations.md`（运维），消除原 api.md 与 guidebook §6 约 70% 重叠漂移；全仓交叉引用同步（`grep` 验证 `guidebook`/旧文件名零残留）。

### 域 4 · 单元测试基线与补强 ✅

目标：高风险链路（并发、时序、边界、转义）有覆盖。

结果：✅ 117 passed。含新增：
- `test_contract.py`（4 测试，域 1）；
- `test_sender.py` 补强：v2 §9「不故障转移」首个显式断言（目标卡无承载→`give_up`，另一在线卡 `send` 从未被调用）+ `_resolve_bearer` 三个漏覆盖分支（卡片禁用/设备禁用/runtime 缺失）；
- `test_extractor.py`：连字符去 `-` 后超 8 位不提取边界。

执行：`.local/venv/bin/python -m pytest -q test/unit`

可选补强（➖）：extractor 更多验证码语料、mms UDH 三片用例。

### 域 5 · 浏览器冒烟自动化 ✅

目标：UI 关键流有自动化守护，不只截图。

方法：扩展 `test/browser-smoke.cjs`，用 `page.on('response')` 捕获真实 HTTP 往返并断言。

结果：✅ 12/12，0 error。从「登录+导航+截图」升级为断言 5 类契约往返：强制拉取（`POST /api/status/refresh` 2xx）、发送（含中文正文，`POST /api/send` 2xx）、SSE 实时推送（mock 注入→收件箱无刷新出现新消息）、删除（`DELETE /api/messages/{id}` 2xx + 列表移除）、401 错误态（清 token 后 401 且页面不崩）。

执行：`test/demo/demo start` → `cd web && npm run build` → `NODE_PATH=$(npm root -g) node test/browser-smoke.cjs`

### 域 6 · 双设备端到端集成 ✅

目标：v2 多设备/多卡核心在真实运行的 Hub+mock 栈跑通。

方法：`DEMO_SECOND_DEVICE_PORT=8081 test/demo/demo start` 起双 mock 设备，curl 验证。

结果：✅ 2 台设备（`aabbccddeeff` + `112233445566`）双平面在线（heartbeat + data_plane）、2 张稳定 sim_id（tail 7890/3210）各自归属、cursor 独立推进。`device_msg_id==1` 的消息两台设备都有（`(device_mac, gw_epoch, device_msg_id)` 复合键跨设备隔离），42 条消息按设备正确分区（36/6）。

### 域 7 · 发布物与部署核对 ✅（完整 build ⚠️）

目标：镜像/产物/配置可构建可部署。

结果：
- ✅ `core/Dockerfile`（三阶段 web→python→runtime）、`docker-compose.yml`（端口/volume/env/restart/healthcheck）、`.dockerignore` 配置正确；
- ✅ `.env.example` 与 `core/infra/config.py` 默认值逐项一致（POLL_INTERVAL=60/ALERT=3/KEEPALIVE=0/TOMBSTONE=30/MESSAGE=0/WEBUI/LISTEN）；
- ✅ `firmware/config.example.h` 模板与文档一致（HEARTBEAT 60 / FW 2.0.0 / API_TOKEN）；
- ✅ `web/dist` 本地构建通过（Vite，等价 Dockerfile Stage 0）；
- ✅ 安全前提复核：仍是「可信局域网」模型（全局 `DEVICE_TOKEN`、明文 HTTP、SSRF 校验），无误改向公网/多租户；
- ✅ 数据迁移提示：`SCHEMA_VERSION=4` 全新安装，README/operations 标注删旧 `sms.db`。
- ⚠️ 完整 `docker compose up -d --build`：本 dev box 无 docker compose 插件 + 无外网（`docker pull` 连不上 registry），须在 CI/部署机执行。本地有 2 天前构建的 `sms-hub:v2` 镜像证明构建链可用。

---

## 三、误报甄别（避免错误返工）

> 探索阶段 3 个 agent 并行核查时，在不理解架构的情况下报了一批看似严重的 🔴，逐条读代码证伪。不要浪费时间"修"这些。

| 误报 | 真相（证据） |
|---|---|
| `/del` vs `/delete` 命名不一致 | 假。Hub 调 `{base}/delete`（`runtime.py:134`）与固件注册 `/delete`（`firmware.ino:1875`）一致 |
| 超时放大不一致致多段假失败 | 假。固件 `firmware.ino:785` 的 30s 是单段 PDU 超时（多段循环）；Hub `20+36×parts`（`runtime.py:111`）是整个 HTTP 请求超时，反而更宽裕 |
| `gw_epoch` 固件侧缺失 | 架构误解。`gw_epoch` 由 Hub poller 按设备自增，固件无需感知 |
| `include_status` "1"/"0" vs 固件 `toInt()` 解析失败 | 假。`"1".toInt()` 返回 1，正常 |
| 6 个新通知渠道完全未测 | 假。`test_notifier.py` 全面覆盖：钉钉/飞书签名对照官方算法、6 渠道 format/target、Server酱 form 编码、加签机器人 URL/body 注入 |
| 临时卡合并/墓碑隔离/跨设备同编号/并发池未测 | 全假。`test_v2_multi.py` 逐项覆盖（sim_id 派生 L34/同编号 L79/墓碑隔离 L114/临时卡合并 L147/并发池 L218） |
| `GET /{token}/status` 端点缺失 | 误读。固件实现了（`/status`），Hub 选择走 `pull?include_status=1`，属设计取舍非缺陷 |

---

## 四、剩余项与执行建议

P2 手动/环境项（非阻塞，发布前补）：

1. 真机回归（硬件）：按 [`docs/operations.md`](docs/operations.md#真机回归) 清单跑。多段长短信合并、设备重启 NVS 恢复、SIM 存储满、设备失联/恢复。mock 是纯内存态，这些场景覆盖不了。
2. `docker compose up -d --build`（部署机）：本机缺 compose 插件 + 无外网。在 CI/部署机执行，确认镜像构建 + UI 可访问 + healthcheck 通过。
3. 死接口决策：`GET /api/sims`（卡片注册表）、`POST /api/poll`（手动入库，语义不同于 `/status/refresh`）前端未直接调用，但 `docs/guide.md` 已文档化用途。保留。

执行顺序建议：真机回归 → docker build（部署机）→ 打 tag 发布。

---

## 附：快速复跑命令

```bash
# 单元 + 契约测试
.local/venv/bin/python -m pytest -q test/unit

# 浏览器冒烟（先起 demo 栈 + 构前端）
test/demo/demo start && (cd web && npm run build)
NODE_PATH=$(npm root -g) node test/browser-smoke.cjs

# 双设备端到端
DEMO_SECOND_DEVICE_PORT=8081 test/demo/demo start
#   然后 curl http://127.0.0.1:8025/api/devices（登录后）验证 2 设备 2 卡

# 文档引用零残留自检
grep -rnE "guidebook|api\.md|architecture\.md|development\.md" . \
  --include="*.md" --include="*.py" --include="*.ts" --include="*.tsx" --include="*.yml" \
  | grep -vE "node_modules|/\.local/"   # 应无输出
```
