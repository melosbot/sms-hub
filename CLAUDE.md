# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

`sms-hub` —— **瘦终端 + Hub** 架构的短信网关。ESP32C3 + ML307R-DC 固件只做 PDU 收发与本地缓冲，所有业务（收信、通知、规则、出站、Web UI、配置）集中在 Docker 里跑的 FastAPI + SQLite Hub。改业务只需重启 Hub，不刷固件。

> **v2（当前）**：支持**多台瘦终端 + 多张 SIM 卡**接入同一个 Hub。物理设备以 **MAC** 标识，SIM 卡以 **IMSI 派生的 `sim_id`** 标识，所有设备共用全局 `DEVICE_TOKEN`。v2 是**全新安装**（不兼容旧库，`SCHEMA_VERSION=4`，升级前删旧 `sms.db`）。完整设计见 `docs/guide.md`（设计与接口权威；前端源码在 `web/`，已纳入版本控制，仅 `node_modules`/`dist` 由 `web/.gitignore` 忽略）。

## 常用命令

```bash
# 单元测试（均跑在 mock 设备上）
.local/venv/bin/python -m pytest -q test/unit
.local/venv/bin/python -m pytest -q test/unit/test_poller.py::test_xxx   # 单个测试

# 浏览器冒烟（Playwright，需先起 demo 栈并已构建前端 web/dist）
NODE_PATH=$(npm root -g) node test/browser-smoke.cjs

# 一键 demo 栈（Hub + mock 设备，开发/调 UI 最方便）
test/demo/demo start        # stop / restart / status / logs / paths
#   Hub UI:        http://127.0.0.1:8025/   admin / demo-pass
#   mock 注信控制台: http://127.0.0.1:8080/
#   第二台 mock(验多设备): DEMO_SECOND_DEVICE_PORT=8081 test/demo/demo start

# 手动起 Hub（务必显式指定 DATA_DIR,否则写 /data）
DATA_DIR=/tmp/sms-hub-dev DEVICE_TOKEN=... WEBUI_PASS=... python -m core.main

# Docker（多阶段：先在 web/ 构前端,再装 Python 依赖）
docker compose up -d --build && docker compose logs -f sms-hub
```

**前端（Web UI）是独立项目**：源码在仓库根的 `web/`（Vite + React + shadcn/ui，**已纳入版本控制**，仅 `node_modules`/`dist` 由 `web/.gitignore` 忽略），`npm run build` 产物为 `web/dist`。Hub 用 `WEB_DIST` 环境变量定位它（Docker `/app/web/dist`，开发回退仓库内 `web/dist`）。`core/main.py` 把它作为 `StaticFiles(html=True)` 挂在**所有 API/hook 路由之后**（否则会遮蔽 `/api/*` 与 `/hook/*`）。没有 `web/dist` 时 Hub 照常起，仅不提供 UI。

固件编译/刷写、备份、Web OTA、真机回归清单见 `docs/operations.md`。真机行为（PDU、AT 时序）无法自动测试，改 poller/runtime/device/sender 关键链路后需按该文手测。

## 架构大图景

以下要点需读多个文件才能拼出，是这个系统最容易理解错的地方。

### 0. 两条身份轴：MAC 与 sim_id（v2 最易错点）

- **`device_mac`（MAC）** = 物理瘦终端 + 网络链路。Hub 规范化为小写无分隔 12 位十六进制（`client.normalize_mac`），展示用 `client.display_mac` 加冒号。
- **`sim_id` = `sim_<sha256(imsi)[0:16]>`** = SIM 卡的**业务归属**身份。短信/发件/通知/收件箱筛选/卡片备注全挂在 `sim_id`。**完整 IMSI 永不持久化/记日志/回传浏览器**——只保存 `imsi_hash`（全量去重）和 `imsi_tail`（末 4 位）。
- 无 IMSI 时建**临时卡 `sim_tmp_<mac>`**（只在本设备下可同步，不可设为 `DEFAULT_SEND_SIM_ID`）；后续上报 IMSI 时由 `DeviceManager.derive_and_merge_sim` 把临时卡业务合并到稳定 `sim_id`（`db.reassign_sim_business`）。
- **全局策略、设备归属**：`ADMIN_PHONE`/`BLACKLIST`/通知通道/模板/代发默认卡等都是全局的，**不提供** per-device/per-SIM 策略，**发信不故障转移到其他卡**（见 docs/guide.md §5.3）。

### 1. 游标拉取是唯一事实源，webhook 只是门铃（现在按设备隔离）

设备收到短信后 webhook `POST /hook/{token}` 只带 `mac` + `event`（+ 可选 buffer 最新 id），**正文永远由 Hub 主动 `GET /<token>/pull?after=<cursor>` 拉取**。`DeviceManager.handle_webhook`（`core/device/manager.py`）按 MAC：SSRF 校验地址 → upsert 设备 → 派生/合并 sim_id → 存状态快照 → 按规则决定是否调度该设备拉取（`/hook/{token}` 路由只转发到这里）。因此：webhook 丢失只损失时效（`POLL_INTERVAL` 兜底），重复幂等（按 `(device_mac, gw_epoch, device_msg_id)` 去重）。

### 2. Hub 是一组协作的 asyncio 后台 worker

`core/main.py` 的 `lifespan` 构造 `DeviceManager` 并拉起后台 worker，彼此通过 **SQLite 队列表 + `asyncio.Event`** 协作，不共享内存状态：

| worker | 文件 | 作用 | 唤醒方式 |
|--------|------|------|---------|
| poller | `device/poller.py` + `manager.py` | **每台启用设备一个 `device_loop` task**，游标拉取入库 | `runtime.trigger.set()`（webhook）+ `POLL_INTERVAL` 兜底 |
| notifier | `notify/notifier.py` | 消费 `notify_jobs` | `_wakeup` + 退避 |
| sender | `sms/sender.py` | 消费 `outbound` | `_wakeup`，poller 每轮也 `sender.wakeup()` |
| commands | `notify/commands.py` | Telegram getUpdates | 25s 长轮询 |
| keepalive | `device/keepalive.py` | 定时 AT 保号 | 到期检查 |

**核心一致性约束（承自 v1，现按设备隔离）**：`poller.poll_device` 把“插入消息 + 派发通知/代发 + 写游标/时间戳”放在**同一事务提交，之后才推进游标**。任意点崩溃，重启后要么幂等重拉，要么已完整入库——不会出现“消息入库但通知丢失”。`db.insert_message` **不 commit**，由调用方串事务。

### 3. 设备 I/O：全局并发池 + 单设备串行 + 优先级

`DeviceManager.io_sem`（`MAX_DEVICE_IO_CONCURRENCY`，默认 4）限跨设备并发；**单设备内部仍串行化**（`DeviceRuntime`，`core/device/runtime.py`），并让**短信收发（`/pull` `/send`）永远优先于 AT/诊断（`/status` `/delete` `/at`）**。A 设备超时不阻塞 B 设备。Web UI 手动 `/api/at` **不排队**，设备忙或已有短信任务在等时直接 `409`。发送超时按段数放大，否则多段短信会假失败。设备上报的 `ip:port` 经 `client.validate_device_addr` 做 **SSRF 校验**（仅 RFC1918/ULA；拒 loopback 除非 `ALLOW_LOOPBACK_DEVICE`、拒公网/保留/链路本地/`169.254.169.254`/Hub 自身/主机名 DNS rebinding）后才写 `base_url`。

### 4. 可靠性靠机制叠加（现在按设备隔离）

- **复合去重 `(device_mac, gw_epoch, device_msg_id)`**：`gw_epoch` **按设备**。设备 NVS 清/换机导致该设备 `latest_id < cursor` 且无新消息时，poller 自增**该设备** `gw_epoch` 并从 0 重拉（`poll_device` 内）。
- **删除墓碑 `deleted_messages`** 主键 `(device_mac, gw_epoch, device_msg_id)`：删本地记录后写墓碑，防设备重启后旧消息回流；**按设备隔离**——删 A 设备短信不影响 B 设备同编号短信。best-effort 调 `/delete` 清缓冲，失败由墓碑兜底。
- **双平面在线判定（per device）**：`heartbeat_online`（心跳/状态新鲜）与 `data_plane_online`（最近 `/pull` 成功）独立，任一新鲜即在线（`manager.compute_liveness`）。
- 设备侧三层缓冲（RAM 50 条 / NVS 16 条 / 模组存储）保证 Hub 不可达时不丢信。

### 5. 出站发送：sim_id → 承载设备，不故障转移

`sender._resolve_bearer` 按 `sim_id → sims.current_device_mac → DeviceRuntime(base_url)` 选目标；卡片禁用/设备缺失/离线 → 直接 `give_up`，**不转到其他卡**（见 docs/guide.md §5.3）。`outbound` 行记录实际承载 `device_mac` 与 `device_msg_id`。

### 6. 配置：env 是初始默认，运行时可热改

`core/infra/config.py` 在 **import 时就 `mkdir(DATA_DIR)`**（所以测试 `conftest.py` 必须先 `set DATA_DIR` 到临时目录）。env 值只是初始默认；可在 Web UI 改的键存 SQLite `kv("cfg")`，`apply_overrides()` 启动时与每次保存后调用，`_recompute()` 重算派生值（如 `TG_ENABLED`）。**改运行时配置不需重启容器。**

### 7. UI 实时刷新

`core/infra/events.py` 是进程内事件总线：poller/sender `publish()`，`/api/events`（SSE）订阅广播给浏览器，30s 轮询兜底。`/api/events` 用 **query token**（`EventSource` 无法设 `Authorization` 头）。v2 每设备独立 poll，突发事件多，队列调到 128。

## 关键约定与陷阱

- **发往设备的 JSON 必须 `ensure_ascii=False`**：固件极简解析器只认原始 UTF-8，不解 `\uXXXX`。所有设备请求体走 `client._json_body()`，别用 httpx 默认序列化。
- **手机号统一 `phone.canonicalize()`**：大陆号归一化为 11 位；`FORWARD_SMS_TO`/`ADMIN_PHONE` 在 `config.py` import 时已 canonicalize。
- **事务边界**：`db.insert_message` **不 commit**，所有写助手默认 `commit=True`，poller 内部传 `commit=False` 把“插入 + 关联入队 + 推进游标”串成**单事务**。新增涉及多表写入的链路要遵守此约定。
- **schema 版本**：`SCHEMA_VERSION = 4`（`infra/db.py`），**全新安装**写入 `PRAGMA user_version` 与 `kv.schema_version`，不写旧版迁移。改表结构需同步此处并加迁移/重建测试。
- **SQLite 备份**：开了 WAL，别直接 `cp`——用 `sqlite3 .backup` 或停服后 `tar`。
- **登录令牌**：`user:exp:sig` 的 HMAC-SHA256（`core/app/auth.py`），无第三方 JWT 依赖；密钥未配则生成落盘 `/data/.jwt_secret`。
- **安全前提是可信局域网**：设备 API 明文 HTTP，**全局共享 `DEVICE_TOKEN`** 仅防误扫（不匹配返回 404 空响应），MAC 才区分设备。学到的设备地址有 SSRF 防护。不要按公网/多租户安全模型改造。
- **前端在版本库的 `web/`**：源码已纳入版本控制（`node_modules`/`dist` 由 `web/.gitignore` 忽略），本仓库同时含 Hub（`core/`）+ 前端（`web/`）+ 固件 + 测试 + 文档。改 UI 在 `web/` 里 `npm run build` 产出 `web/dist`，Hub 用 `WEB_DIST` 定位。
