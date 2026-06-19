# sms-hub 设计与接口指南（v2）

> 本文是「理解系统设计 + 对接 HTTP 接口」的单一权威，合并自原架构设计 / API 参考 / v2 设计指南三份文档。运维操作（刷机 / Docker / 部署 / 测试 / 备份）见 [operations.md](operations.md)；机器可读契约（前端类型可据此生成）见 [`openapi.yaml`](openapi.yaml)。
>
> v2 是**全新安装**，不兼容旧库与旧固件 API（`SCHEMA_VERSION=4`，升级前删旧 `sms.db`）。支持**多台瘦终端 + 多张 SIM 卡**接入同一个 Hub。

## 目录

- [一、设计概览](#一设计概览)
- [二、可靠性模型](#二可靠性模型)
- [三、身份与 API 契约](#三身份与-api-契约)
- [四、数据模型](#四数据模型)
- [五、产品设计与运行时](#五产品设计与运行时)
- [附录 A：v2 改造历程](#附录-av2-改造历程)
- [附录 B：请求示例精选](#附录-b请求示例精选)
- [附录 C：取舍与边界备注](#附录-c取舍与边界备注)

---

## 一、设计概览

### 1.1 双身份模型：MAC 与 sim_id

v2 最核心的概念是两条正交的身份轴（也是最容易理解错的地方）：

- **`device_mac`（MAC）** = 物理瘦终端 + 网络链路。Hub 规范化为小写无分隔 12 位十六进制（`aabbccddeeff`），展示用 `client.display_mac` 加冒号（`aa:bb:cc:dd:ee:ff`）。
- **`sim_id` = `sim_<sha256(imsi)[0:16]>`** = SIM 卡的**业务归属**身份。短信 / 发件 / 通知 / 收件箱筛选 / 卡片备注全部挂在 `sim_id` 上。**完整 IMSI 永不持久化 / 记日志 / 回传浏览器**——只保存 `imsi_hash`（全量去重）和 `imsi_tail`（末 4 位）。
- 无 IMSI 时建**临时卡 `sim_tmp_<mac>`**（只在本设备下可同步，不可设为 `DEFAULT_SEND_SIM_ID`）；后续上报 IMSI 时由 `DeviceManager.derive_and_merge_sim` 把临时卡业务合并到稳定 `sim_id`。
- **全局策略、设备归属**：`ADMIN_PHONE` / `BLACKLIST` / 通知通道 / 模板 / 代发默认卡等都是**全局**的，**不提供** per-device / per-SIM 策略，**发信不故障转移到其他卡**（见 [§5.3](#53-业务策略与风险边界)）。

**卡片为中心**：`sim_id`（IMSI 派生）是短信/发件/通知/收件筛选/状态查询的**业务主键**；`device_mac`（物理瘦终端）只是承载卡片的载体，用于审计「这条短信由哪台瘦终端收发」。

### 1.2 真机响应基线（身份模型依据）

基于 ML307A 真机响应，v2 身份模型按以下实测事实设计：

| 实测项 | 结论 | 设计影响 |
|--------|------|----------|
| `GET /{token}/status` | `modem.msisdn` 可有值，`modem.iccid` 可能为空，`modem.imsi_tail` 可用 | 旧状态字段不足以单独完成卡片唯一识别 |
| `AT+CNUM` | 可能只返回 `OK` | 本机号码不能作为自动识别主键，只能作为配置值和展示字段 |
| `AT+ICCID` | 可能返回含 `A/F` 字符的 ICCID | ICCID 解析必须按字符串处理（不能只提取连续数字）；但不作为主身份 |
| `AT+CIMI` | 能返回完整 IMSI | **v2 使用 IMSI 派生 `sim_id`** |

### 1.3 设计目标

| 目标 | 说明 |
|------|------|
| 低维护 | 业务逻辑（通知、规则、UI、配置）集中在 Hub，改这些不用刷固件 |
| 消息可靠 | 短信正文以「游标拉取」为唯一事实源，webhook 丢失只损失时效 |
| 弱网容忍 | Hub 宕机 / WiFi 断 / 设备重启的任一故障下都不丢已收短信 |
| 多设备多卡（v2） | 多台瘦终端 + 多张 SIM 接入同一 Hub；物理设备以 MAC 标识，SIM 以 IMSI 派生 sim_id；游标/代次/墓碑按设备隔离 |
| 局域网内运行 | 不追求公网级安全，只做基础隔离与误操作防护 |

### 1.4 产品目标与非目标

**必须达成**：支持多张 SIM 卡和多台瘦终端同时接入同一个 Hub；所有设备共用唯一 `DEVICE_TOKEN`；后台持续接收所有设备的 webhook/heartbeat 并同步所有启用设备；前端设置页提供 SIM 卡与瘦终端列表、备注和当前卡片切换；收件箱和发件记录覆盖全部卡片，回复按原短信 `sim_id` 发送；推送/管理员/转发/黑名单/通知模板等配置保持 Hub 全局统一；优先使用 webhook/heartbeat/SSE/缓存状态降低常态请求频率。

**非目标**：不做旧版本数据迁移；不支持每设备或每卡独立的通知渠道/管理员/转发目标/黑名单/模板；不支持多设备群发；不把 Hub 改造成多租户平台；不改变「可信局域网运行」的基础安全边界。

### 1.5 总体架构

系统分为两个进程，通过局域网 HTTP 通信。

**瘦终端固件**（`firmware/firmware.ino`，运行在 ESP32C3 + ML307R-DC）：

- PDU 收发与长短信合并；
- RAM 环形缓冲（50 条）+ NVS 镜像（最近 16 条）两层本地存储；
- 极简 HTTP 服务，所有端点挂在 `/<token>/` 下；
- 通过 webhook 主动向 Hub 上报事件（boot / hello / sms / heartbeat）；
- 不维护墙上时钟，不解析业务规则，不渲染 UI。

**Hub**（`core/`，FastAPI + SQLite，跑在 Docker）：

- 游标拉取短信、入库、提取验证码；
- 通知（Telegram + 6 类推送渠道）、出站发送、Telegram 命令、保号等后台任务；
- Web UI、运行时配置、`/metrics`；
- 设备地址自学习、失联/恢复告警。

职责切分的核心收益：固件保持极简稳定，所有高频变更面收敛到 Hub 一次 `docker compose restart` 即可生效。

**三个通信平面**：

- **设备 → Hub**：瘦终端 → Hub，局域网明文 HTTP，设备主动 `POST /hook/{token}` 上报。
- **Hub → 设备**：Hub → 瘦终端，请求挂在 `http://<device-ip>:<port>/<token>/...` 下（端口 80 时无 `:port`）。`<token>` 即全局 `DEVICE_TOKEN`。
- **浏览器 → Hub**：Web UI → Hub，`/api/*` 前缀；除 `health` / `login` / `metrics` 外需 `Authorization: Bearer <token>`。

---

## 二、可靠性模型

这是整个系统最关键的设计，由多个机制叠加组成。

### 2.1 webhook 门铃 + 游标拉取事实源

设备收到短信后，webhook 只上报事件 + 可选 `buffer.latest_id`（门铃），**消息正文始终由 Hub 主动 `GET /<token>/pull?after=<cursor>` 拉取**。这样：

- webhook 丢失 → 仅损失「即时性」，`POLL_INTERVAL` 兜底轮询会拉齐；
- webhook 重复 → 幂等（入库按 `(device_mac, gw_epoch, device_msg_id)` 去重）；
- 短信正文的可靠性只依赖拉取链路，与通知链路解耦。

### 2.2 三层缓冲兜底

设备侧三层存储，任意一层都能在 Hub 不可达时保留短信：

| 层 | 容量 | 用途 |
|----|------|------|
| RAM 环形缓冲 | 50 条 | 主缓冲，Hub 实时拉取 |
| NVS 镜像 | 最近 16 条（正文截断 512 字节） | 掉电恢复最近短信 |
| 模组自身存储 | 受限于 SIM/模组 | 背压时的最后兜底 |

设备有**背压判断**：当 RAM 环形缓冲即将覆盖 Hub 尚未拉取的消息时，暂停从模组存储读取新短信，避免覆盖未拉取数据。

### 2.3 删除墓碑防回流

Hub 删除短信时：① 先删本地 SQLite 记录；② 写墓碑表 `deleted_messages`（防设备重启后旧消息重新入库）；③ best-effort 调设备 `POST /<token>/delete` 清缓冲（失败不影响正确性，墓碑兜底）。

墓碑主键 `(device_mac, gw_epoch, device_msg_id)`，**按设备隔离**——删 A 设备短信不影响 B 设备同编号短信。按 `TOMBSTONE_KEEP_DAYS`（默认 30 天）清理。

### 2.4 换机 / NVS 清空自愈（gw_epoch）

设备 NVS 被清或换机后，设备侧消息 ID 会从头计数，与 Hub 游标冲突。Hub 检测到该设备 `latest_id < cursor` 且无新消息时：

- **仅对该设备**自增 `gw_epoch`（代次）；
- 游标重置为 0，从设备缓冲最旧一条重新拉起；
- 入库按 `(device_mac, gw_epoch, device_msg_id)` 复合 UNIQUE 去重，跨代 / 跨设备不冲突；
- 该规则不影响同一 SIM 卡在其他设备上的历史短信归属。

### 2.5 双平面在线判定

设备「在线」拆成两个独立信号，任一新鲜即在线：

| 平面 | 信号 | 来源 | 含义 |
|------|------|------|------|
| 状态面 | `heartbeat_online` | 设备定时 heartbeat webhook | 设备活着、模组正常 |
| 数据面 | `data_plane_online` | 最近一次 `/pull` 成功 | 短信拉取链路通 |

判定公式（随 `POLL_INTERVAL`/心跳间隔自适应）：

```text
heartbeat_online = now - last_status_ts <= max(heartbeat_interval_s × 2.5, HEARTBEAT_ONLINE_FLOOR_S=150)
data_plane_online = now - last_poll_ok_ts <= max(POLL_INTERVAL × 2 + 30, DATA_PLANE_ONLINE_FLOOR_S=630)
overall_online = heartbeat_online OR data_plane_online
```

这比单一「ping」健壮：心跳在但拉取失败（数据面断）或拉取在但心跳停（设备假死）都能被分别识别。

### 2.6 设备 I/O 调度

瘦终端是**单线程 HTTP + AT 命令**模型：模组命令通道同一时刻只能处理一件事。因此：

- **跨设备**由 `DeviceManager.io_sem`（`MAX_DEVICE_IO_CONCURRENCY`，默认 4）限并发——A 设备超时不阻塞 B 设备，也不会让低配 Hub 同时打开过多设备连接。
- **单设备内部串行化**（`DeviceRuntime`，`core/device/runtime.py`），短信收发永远优先：

| 优先级 | 请求 |
|--------|------|
| 高（SMS） | `/pull` `/send` |
| 中（control） | `/delete` |
| 低（AT） | `/at` |

低优先级已排队时，只要有 `/pull`/`/send` 入队，下次设备空闲先执行短信收发。**Web UI 手动 `/api/at` 不排队**：设备正忙或已有短信任务等待时直接返回 `409`，避免诊断 AT 把收信拉取长时间压住：

```json
{ "detail": "设备忙: 正在执行 拉取短信" }
{ "detail": "设备忙: 短信收发优先" }
```

发送超时按段数放大（`20s + 36s × 段数`），避免「Hub 超时但设备实际发出」的假失败。

设备上报的 `ip:port` 经 `client.validate_device_addr` 做 **SSRF 校验**（仅 RFC1918 私有地址；拒 loopback 除非 `ALLOW_LOOPBACK_DEVICE`、拒公网/保留/链路本地/`169.254.169.254`/Hub 自身/主机名 DNS rebinding）后才写 `base_url`。

### 2.7 故障模式

| 故障 | 影响 | 恢复行为 | 剩余风险 |
|------|------|----------|----------|
| webhook 丢失 | 新短信延迟 | poller 定时拉取 | 延迟最多约 `POLL_INTERVAL` |
| Hub 宕机 | 通知与 UI 暂停 | 设备 RAM/NVS/模组存储兜底；恢复后游标拉齐 | 长时间高频短信可能超设备/模组容量 |
| 设备 WiFi 断 | Hub 拉取失败 | 连续失败后告警；WiFi 恢复后 `hello` 上报新 IP | 断网期间无法远程发送 |
| 设备重启 | RAM 丢失 | NVS 恢复最近 16 条；模组未读短信重新入队；`boot` 触发对账 | NVS 只保留截断镜像 |
| 设备 NVS 清空/换机 | 设备 ID 回退 | Hub 检测 `latest_id < cursor`，自增该设备 `gw_epoch` 并重拉 | 只能重拉设备当前缓冲 |
| Hub DB 丢失 | 历史与游标丢失 | 设备当前缓冲可重新拉取 | 更早历史不可恢复，依赖备份 |
| 通知通道不可达 | 通知延迟 | `notify_jobs` 指数退避，最终 `give_up` | 短信仍正常入库，仅通知受阻 |
| 删除设备侧失败 | 设备仍留消息 | Hub 墓碑拦截重新入库；设备下次自然覆盖 | 墓碑过期后极旧 NVS 镜像理论可能回流 |
| 保号 AT 异常 | 保号流量未产生 | 日志与告警 | 无历史趋势记录 |

### 2.8 安全边界

本项目前提是**严格运行在可信局域网内**，无公网暴露、陌生客户端或外部攻击面。安全设计只保留基础隔离与误操作防护：

- 设备 API 使用**明文 HTTP**，不引入 TLS / 证书 / mTLS；
- token 保留用于避免局域网误扫、误调用；token 不匹配返回 **404 空响应**（低暴露面），MAC 才区分设备；
- Web UI 使用**单账号 + HMAC 令牌**（`user:exp:sig`，TTL 30 天），不做多用户、多角色、审计日志；
- `/api/events` 使用 **query token**（`EventSource` 无法设 `Authorization` 头）；
- 默认 `WEBUI_PASS=admin123` 只告警不阻断，方便首次部署（生产必须改）；
- 通知渠道密钥可存 SQLite，备份时按普通运维数据处理。

**不作为本项目需求**：公网部署、多租户权限、外部身份认证、合规审计。

### 2.9 设计取舍

- 不在固件里做通知、规则引擎、Web UI；
- 设备 API 不上 TLS，依赖可信 LAN 与长 token；
- 不恢复固件 SoftAP 配网，WiFi 配错时通过 USB 重刷；
- 不在设备上维护墙上时钟，绝对时间由 Hub 用 `age_s` / `scts` 计算；
- 单容器 SQLite 优先于外部数据库。

---

## 三、身份与 API 契约

### 3.0 身份与 ID 作用域

| 标识 | 作用域 | 用途 |
|------|--------|------|
| `device_mac`（MAC） | 物理瘦终端 + 网络链路 | 规范化为小写无分隔 12 位 hex，展示加冒号；区分物理设备 |
| `sim_id` = `sim_<sha256(imsi)[0:16]>` | SIM 卡业务归属 | 短信/发件/通知/收件筛选/备注的主键。**完整 IMSI 永不持久化/记日志/回传**，只存 `imsi_hash` + `imsi_tail` |
| `sim_tmp_<mac>` | 临时卡 | 无 IMSI 时建；仅当前设备下可同步，不可设为 `DEFAULT_SEND_SIM_ID`；上报 IMSI 后合并到稳定 `sim_id` |
| `device_msg_id` | 每台设备独立递增 | 瘦终端缓冲编号，用于 `/pull` 游标与设备 `/delete` |
| `messages.id` | Hub 全局自增 | Web UI 详情/删除/导出/通知关联使用 |
| `(device_mac, gw_epoch, device_msg_id)` | Hub 全局唯一约束 | 防不同设备同编号短信冲突（`gw_epoch` 按设备） |

**请求携带身份的固定规则**：

| 请求类型 | 身份规则 |
|----------|----------|
| 设备上报 `POST /hook/{token}` | body 必须带 `mac`；boot/heartbeat 应带 `modem.imsi` 和 `modem.imsi_tail`（token 只认证，不区分设备） |
| Hub 调设备 `/{token}/*` | **不带 MAC/IMSI**（Hub 已按 `sim_id → current_device_mac → base_url` 选中目标） |
| `GET /api/devices` / `GET /api/sims` | 不带身份参数，返回全部 |
| `PATCH /api/devices/{mac}` / `PATCH /api/sims/{sim_id}` | path 带目标标识 |
| 当前卡片 API（`status`/`poll`/`status/refresh`/`at`） | query 或 body 带 `sim_id` |
| 收件/发件查询 | `sim_id=all`（全部卡片时间线，默认）/ `online`（仅在线卡）/ 具体 sim_id |
| `GET/DELETE /api/messages/{id}` | 不带身份（Hub 用全局 `id` 反查所属卡片和设备） |
| `POST /api/send` | body 必须带 `sim_id` |
| 全局 API（`config`/`notify/test`/`events`/`metrics`/认证/健康） | 不带 MAC |

**sim_id 自动推断**（`core/app/simutil.py`）：当前卡片类 API 的 `sim_id` 缺省时——恰 1 张启用卡 → 自动推断；0 或 >1 张启用卡 → **400**。

### 3.1 设备 → Hub：`POST /hook/{token}`

设备主动上报事件（boot / hello / sms / heartbeat）。**token 只认证、不区分设备**；body 必须带 `mac`。token 不匹配返回 **404 空响应**（低暴露面）。

请求体（boot/heartbeat 带完整状态含 `modem.imsi`；sms/hello 可只带轻量字段）：

```json
{
  "event": "heartbeat", "mac": "aabbccddeeff",
  "seq_id": 12, "device_ts_ms": 1760000000000,
  "ip": "192.168.1.88", "port": 80, "fw": "2.1.0", "uptime_s": 3600,
  "heartbeat_interval_s": 60,
  "modem": { "ready": true, "operator": "CMCC", "csq_dbm": -78,
             "imsi": "460001234567731", "imsi_tail": "7731",
             "msisdn": "13800138000", "iccid": "898600A00000F0217731",
             "iccid_tail": "7731", "apn": "cmnet", "flight_mode": 1, "pdp_active": true },
  "buffer": { "oldest_id": 88, "latest_id": 123, "count": 4, "capacity": 50, "dropped_total": 0 },
  "counters": { "rx_total": 123, "tx_total": 7, "webhook_fail_total": 0 },
  "last_error": ""
}
```

| event | 触发时机 | Hub 行为 |
|-------|----------|----------|
| `boot` | 设备启动完成 | 学习地址(SSRF 校验)、保存状态、派生/合并 sim_id、调度 pull |
| `hello` | IP 变化/网络恢复 | 学习地址、保存轻量状态、调度 pull |
| `sms` | 收到新短信入缓冲 | 保存 buffer 摘要、调度 pull |
| `heartbeat` | 定时心跳（默认 60s） | 保存完整状态；仅当 `buffer.latest_id > cursor` 才调度 pull |

**响应**：`200 {"ok": true, "pull_scheduled": <bool>}`。`pull_scheduled=false`（禁用设备/heartbeat 无新消息）仍记录状态。

| 状态码 | 含义 |
|--------|------|
| 200 | 正常（看 `pull_scheduled`） |
| 400 | MAC 缺失/非法，或 `ip:port` 未过 SSRF 校验 |
| 404 | token 不匹配（空响应） |

> `/hook/{token}` 必须快速返回，**不在请求内同步执行设备 pull**——只做 token 校验、MAC/IMSI 规范化、状态快照保存、异步拉取排队。同一设备 300–1000ms 内多次 hook 合并为一次 pull；正在 pull 时新 hook 置 `pull_again=true`，本轮结束后补一轮，不并发拉同一设备。`seq_id`/`device_ts_ms` 仅用于乱序诊断，Hub 不依赖设备时间排序。高频 `sms` hook 不要求带完整 imsi——若 Hub 尚不知该设备 sim_id，该次 hook 触发的 pull 会用 `include_status=1` 从响应取 IMSI。

### 3.2 Hub → 设备

所有端点在 `http://<device-ip>:<port>/<token>/...` 下。**token 不匹配返回 404 空响应**。固件极简 JSON 解析器**只认原始 UTF-8，不解 `\uXXXX`**——Hub 发往设备的请求体一律 `ensure_ascii=False`（走 `client._json_body()`）。

| 方法 | 路径 | 用途 | Hub 调用方 | 超时 |
|------|------|------|-----------|------|
| GET | `/{token}/pull` | 拉短信 + buffer 摘要（**唯一事实源**） | poller / 状态刷新 / 缓冲排空 | 10s |
| POST | `/{token}/send` | 发送短信 | sender worker | `20 + 36×段数` s |
| POST | `/{token}/delete` | 批量删除设备缓存 | 消息删除 best-effort | 8s |
| POST | `/{token}/at` | AT 透传（诊断 + 保号） | `/api/at` / keepalive | `timeout_ms/1000 + 5` s |
| GET/POST | `/{token}/update` | OTA 页面与上传（**浏览器直连设备，非 Hub**） | 用户浏览器 | — |

> 设备固件还实现了 `GET /{token}/status`（完整快照），但 **Hub 不再调用**——状态刷新统一走 `GET /{token}/pull?include_status=1`。`/status` 仅用于设备本地自检/调试，不属于 Hub 契约。

#### `GET /{token}/pull`

短信正文唯一事实源。设备按 `device_msg_id` 升序返回 `device_msg_id > after` 的消息。

```
GET /{token}/pull?after=<cursor>&limit=<n>&include_status=<0|1>
```

```json
{
  "mac": "aabbccddeeff",
  "buffer": { "oldest_id": 88, "latest_id": 125, "count": 4, "capacity": 50, "dropped_total": 0 },
  "messages": [
    { "device_msg_id": 124, "from": "10690329", "scts": "26061210000032",
      "age_s": 5, "text": "验证码 114514", "complete": true, "truncated": false }
  ],
  "status": null
}
```

- `include_status=1` 时 `status` 非空，仅含 `modem` 身份块（`ready/imsi/imsi_tail/msisdn/iccid/iccid_tail/operator`）——用于首拉解析 sim_id 或手动刷新 modem 身份，**不覆盖心跳写入的丰富遥测**（信号/计数仍以心跳快照为准）。
- `age_s = -1` 表示设备断电恢复（时长未知），Hub 用 `scts` 兜底计算接收时间，防旧短信冒充新短信。

#### `POST /{token}/send`

```json
// 请求
{ "to": "13800138000", "text": "测试短信" }
// 成功
{ "ok": true, "device_msg_id": 126, "parts": 1 }
// 失败
{ "ok": false, "device_msg_id": 0, "parts": 0, "error": "发送失败，请检查模组状态" }
```

固件同步等待 `>` 提示与发送回执，多段短信最重；Hub 按段数放大超时避免「Hub 超时但设备已发」假失败。

#### `POST /{token}/delete`

批量删除设备环形缓冲与 NVS 镜像里的消息。逐条返回是否命中，`found=false` 视为幂等成功；Hub 墓碑是最终防回流兜底。

```json
// 请求
{ "device_msg_ids": [121, 122, 123] }
// 响应
{ "ok": true, "deleted": [ { "device_msg_id": 121, "found": true }, { "device_msg_id": 122, "found": false } ] }
```

#### `POST /{token}/at`

AT 透传，用于状态页诊断与保号任务。`timeout_ms` 限制在 `100..15000`。**失败原因统一放在 `response`，没有独立 `error` 字段**。

```json
// 请求
{ "cmd": "AT+CESQ", "timeout_ms": 3000 }
// 成功
{ "ok": true, "response": "+CESQ: 99,99,255,255,17,62\r\nOK\r\n" }
// 失败
{ "ok": false, "response": "TIMEOUT" }
```

> AT 请求占用模组命令通道，期间 URC 被拦截；UI 不应频繁自动触发。

### 3.3 浏览器 → Hub：`/api/*`

除 `health` / `login` / `metrics` 外，所有接口需 `Authorization: Bearer <token>`。令牌通过 `POST /api/login` 获取（HMAC 签名，TTL 30 天）。`/api/events`(SSE) 因 EventSource 无法带头，走 query `?token=`。

#### 3.3.1 认证 / 公开 / 实时

| 端点 | 方法 | 鉴权 | 说明 |
|------|------|------|------|
| `/api/health` | GET | 无 | `{ok: true}` |
| `/api/login` | POST | 无 | `{user, password}` → `{token}`；账密错 `401` |
| `/api/events` | GET(SSE) | query `token` | 实时事件流，见 [§3.5](#35-sse-事件apievents) |
| `/metrics` | GET | 无（LAN-only） | Prometheus：`sms_hub_device_reachable/heartbeat_online/data_plane_online/device_busy/messages_total/outbound_jobs/notify_jobs`，带 `device_mac`/`sim_id` label |

#### 3.3.2 卡片与设备

| 端点 | 方法 | 请求 | 响应 | 状态码 |
|------|------|------|------|--------|
| `/api/sims` | GET | — | `{sims: [SimView]}`（卡片注册表） | 200 |
| `/api/devices` | GET | — | `{devices: [DeviceView], sims: [SimView]}`（瘦终端 + 各卡承载快照） | 200 |
| `/api/sims/{sim_id}` | PATCH | `{name?, enabled?}` | `{ok, sim:{...}}`；`enabled` 由 false→true 立即补拉 | 200 / 404 |
| `/api/devices/{mac}` | PATCH | `{name?, enabled?}` | `{ok, device:{...}}` | 200 / 404 |

`SimView` = `{sim_id, name, enabled, current_device_mac, identity_source, msisdn, imsi_tail, iccid_tail, operator}`。
`DeviceView` = `{mac, display_mac, name, enabled, online, heartbeat_online, data_plane_online, last_heartbeat_ago_s, last_poll_ago_s, last_hook_ago_s, cursor, busy, buffer, modem, current_sim_id}`。

> `enabled=false` 的设备/卡片仍记录 webhook/heartbeat，但 Hub 不主动 pull、不允许发送、不执行 AT。卡片不提供删除——上报即注册、离线即隐藏、重新上报自动回归。

#### 3.3.3 当前卡片控制

均按 `sim_id` 定位承载设备（单卡自动推断，多卡须显式，否则 400）。设备忙 `409`、不可达 `502`。

| 端点 | 方法 | 请求 | 响应 |
|------|------|------|------|
| `/api/status` | GET | `?sim_id=` | 读心跳缓存合成状态（不打扰设备），结构见 [§3.4](#34-apistatus-响应结构) |
| `/api/poll` | POST | `{sim_id?}` | `{ok, sim_id, device_mac, inserted}` 强制拉一批入库 |
| `/api/status/refresh` | POST | `{sim_id?}` | `{ok, sim_id, device_mac, age_s:0}` 刷新 modem 身份（`pull?include_status=1`） |
| `/api/buffer/clear` | POST | `{sim_id?}` | `{ok, sim_id, device_mac, deleted}` 排空设备缓冲里已同步消息 |
| `/api/at` | POST | `{sim_id?, cmd, timeout_ms?}` | 透传 AT `{ok, response}`；**不排队，忙即 409** |

> `/api/poll`（拉一批入库，返回 `inserted`）与 `/api/status/refresh`（只刷 modem 身份，返回 `age_s`）语义不同，故保留两个端点。

#### 3.3.4 收件

| 端点 | 方法 | 请求 | 响应 |
|------|------|------|------|
| `/api/messages` | GET | `?sim_id=&q=&limit=&offset=`（limit ≤200） | `{total, messages:[Message], readonly}` |
| `/api/messages/export` | GET | `?sim_id=&q=&fmt=csv\|json&limit=`（≤20000） | 文件下载（`Content-Disposition: attachment`） |
| `/api/messages/{id}` | GET | — | `Message` + `notify:[{channel,status,attempts,last_error,created_at}]`；404 不存在 |
| `/api/messages/{id}` | DELETE | — | `{ok, device_deleted}` 删本地+写墓碑+best-effort 设备删除 |
| `/api/messages` | DELETE | `{ids:[…]}` | `{ok, deleted, device_deleted}`；空 ids `400` |

`Message` = `{id, sim_id, sim_name, device_mac, device_msg_id, gw_epoch, sender, sender_alias?, text, scts?, received_at, code, complete, truncated, blocked, created_at, brand}`（`code` 由 `extract_code` 提取可 `null`；`brand` 由 `extract_brand` 提取【品牌签名】）。

#### 3.3.5 发件

| 端点 | 方法 | 请求 | 响应 |
|------|------|------|------|
| `/api/send` | POST | `{sim_id?, to, text}` | `{ok, queued:true, id, parts, sim_id, device_mac}`；空号码/内容 `400`；`DEVICE_TOKEN` 未配 `503` |
| `/api/outbound` | GET | `?sim_id=&limit=`（≤100；`sim_id` 支持 `online`/具体/`all`） | `{outbound:[Outbound]}` |
| `/api/outbound/{id}` | DELETE | — | `{ok}`；不存在 `404` |

`Outbound` = `{id, sim_id, device_mac, to_phone, text, device_msg_id, status, parts, attempts, next_attempt_ts, source, last_error, created_at}`（`status` ∈ pending/retry/sent/give_up）。

> 发送 sim_id → 承载设备，**不故障转移到其他卡**：卡片禁用/设备缺失/离线 → 直接 `give_up`（§5.3）。

#### 3.3.6 联系人

| 端点 | 方法 | 请求 | 响应 |
|------|------|------|------|
| `/api/contacts` | GET | — | `{contacts:[{phone, alias, updated_at}]}` |
| `/api/contacts/{phone}` | PUT | `{alias}` | `{ok, phone, alias}`；空别名删除返回 `{ok, deleted:true}`；空号码 `400` |
| `/api/contacts/{phone}` | DELETE | — | `{ok}` |

#### 3.3.7 配置与通知健康

| 端点 | 方法 | 请求 | 响应 |
|------|------|------|------|
| `/api/config` | GET | — | `Config`（`device_token_tail`、通知通道 token 只回 `bot_token_set` 不回明文） |
| `/api/config` | POST | `Partial<Config>` | `{ok}`；无效值 `400`。写 `kv("cfg")` 即时生效，无需重启 |
| `/api/notify/test` | POST | `{channel}` | `{ok,error}`；即时测试已保存通道，不入队、不写库 |

`Config` = `{device_token_tail, tg_manage_enabled, notify_channels, admin_phone, default_send_sim_id, blacklist, poll_interval, keepalive_interval_days, keepalive_ping_host, tombstone_keep_days, message_keep_days}`。

### 3.4 `/api/status` 响应结构

读心跳缓存合成，不打扰设备。`device` 为最近 heartbeat/boot 的完整快照。

```json
{
  "device": { /* 最近 heartbeat body：modem/buffer/counters/uptime_s/… */ },
  "device_reachable": true,
  "overall_online": true,
  "heartbeat_online": true,
  "data_plane_online": true,
  "device_status_age_s": 12,
  "hub": {
    "sim_id": "sim_0123456789abcdef", "sim_name": "主卡",
    "device_mac": "aabbccddeeff", "stored_total": 120, "cursor": 123,
    "last_poll_ago_s": 5, "last_hook_ago_s": 10,
    "poll_interval_s": 60, "device_busy": ""
  }
}
```

| 字段 | 语义 |
|------|------|
| `heartbeat_online` | 最近 heartbeat/boot 新鲜 → 状态面在线 |
| `data_plane_online` | 最近 `/pull` 成功 → 数据面在线 |
| `overall_online` | 上述任一为真（`device_reachable` 为其兼容别名） |
| `device_status_age_s` | 最近状态快照距今秒数 |
| `hub.device_busy` | Hub 正占用该设备的操作名，空串表示空闲 |

### 3.5 SSE 事件（`/api/events`）

连接后先发 `retry: 3000`，空闲 25s 发保活注释行 `: ping`。事件 payload 固定含 `type`；卡片相关含 `sim_id`，设备相关含 `device_mac`：

```json
{ "type": "new_messages", "sim_id": "sim_…", "device_mac": "aabbccddeeff", "count": 2 }
{ "type": "device", "device_mac": "aabbccddeeff", "online": true }
{ "type": "outbound", "sim_id": "sim_…", "device_mac": "aabbccddeeff", "id": 7 }
```

前端按 `type` 失效对应 React Query 缓存：`new_messages`→messages，`device`→devices/status，`outbound`→outbound。SSE 断线时降级轮询。

### 3.6 请求频率与刷新策略

**设备侧**：`boot` 启动后立即发；`hello` IP/网络变化时发；`sms` 新短信入缓冲后发；`heartbeat` 默认 60s（配置范围 60–300s），仅 `latest_id > cursor` 才触发 Hub pull；webhook 失败按 2s/5s/15s 退避+抖动，仍失败则放弃依赖兜底 pull。

**Hub → 设备**：收到 `boot/hello/sms` 或 heartbeat 发现新消息立即 pull；兜底同步每台启用设备每 `POLL_INTERVAL`（默认 60s，生产建议 300s）一次；状态页普通刷新读心跳缓存不请求设备；手动刷新走 `pull?include_status=1`；批量删除一次 `delete` 携带多 id；设备缓冲 GC 在兜底同步后批量 `delete` 已入库无待处理通知的消息。主动 I/O 受全局并发池限制（默认 4）。

**前端**：页面加载拉 config/devices/status/messages/outbound/events；SSE 正常时设备/卡片列表和当前状态每 60s 刷新，SSE 断开时降级 30s 轮询（含全部卡片收件箱）。

### 3.7 数据约定

- **号码归一化**（`phone.canonicalize`）：仅大陆手机号 `+861xxxxxxxxxx` → `1xxxxxxxxxx`；服务号与国际号原样保留。
- **发往设备的 JSON 必须 `ensure_ascii=False`**：固件解析器只认原始 UTF-8，支持 `\n \r \t \" \\`，**不支持 `\uXXXX`**。
- **短信分段（UCS2 单元）**：单段 ≤ 70 单元，超出按 60 单元/段拆，最多 5 段；4 字节字符（emoji）计 2 单元。Hub 与固件同算法（`client.estimate_parts`）。
- **SCTS 时间戳**：`YYMMDDhhmmsszz` 共 14 位数字，`zz` 为时区刻钟数（×15 分钟）。正常用 `age_s` 回推，断电恢复用 SCTS 兜底。
- **消息去重**：复合键 `(device_mac, gw_epoch, device_msg_id)`；NVS 清/换机时 poller 自增该设备 `gw_epoch` 从 0 重拉。

### 3.8 错误响应

| 状态码 | 场景 | 响应 |
|--------|------|------|
| 400 | `sim_id` 缺失/格式错误、空 ids、MAC 非法 | `{"detail":"sim_id 缺失或无效"}` |
| 401 | Web 登录失败或 bearer token 无效 | `{"detail":"账号或密码错误"}` |
| 404 | 设备 token 错误或资源不存在 | 空响应或 `{"detail":"Not Found"}` |
| 409 | 设备地址未知 / 设备忙 | `{"detail":"设备地址未知,等待设备上报"}` / `{"detail":"设备忙: …"}` |
| 502 | Hub 调设备失败 | `{"detail":"设备不可达: timeout"}` |

---

## 四、数据模型

v2 使用「共享表 + `sim_id` 业务分区 + `device_mac` 物理审计」的关系模型，**不为每张 SIM 卡或每台设备创建独立表**（动态建表会增加迁移、查询、导出、通知关联和测试复杂度）。卡片与终端均**不提供删除**——卡片以 IMSI 派生的 `sim_id` 标识、可在终端间漫游，终端仅为承载载体；上报即注册、离线即隐藏，重新上报自动回归。完整建表语句见 `core/infra/db.py`（`SCHEMA_VERSION=4`）。

```sql
devices(
  mac TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  base_url TEXT NOT NULL DEFAULT '',
  current_sim_id TEXT NOT NULL DEFAULT '',
  cursor INTEGER NOT NULL DEFAULT 0,
  gw_epoch INTEGER NOT NULL DEFAULT 0,
  heartbeat_interval_s INTEGER NOT NULL DEFAULT 60,
  last_hook_ts REAL NOT NULL DEFAULT 0,
  last_poll_ok_ts REAL NOT NULL DEFAULT 0,
  last_status_ts REAL NOT NULL DEFAULT 0,
  last_status_json TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)

sims(
  sim_id TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  current_device_mac TEXT NOT NULL DEFAULT '',
  identity_source TEXT NOT NULL DEFAULT 'imsi',   -- imsi | temporary
  imsi_hash TEXT NOT NULL DEFAULT '',              -- 全量去重；临时卡为空
  imsi_tail TEXT NOT NULL DEFAULT '',              -- 末 4 位
  iccid_tail TEXT NOT NULL DEFAULT '',
  msisdn TEXT NOT NULL DEFAULT '',
  operator TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)
CREATE UNIQUE INDEX idx_sims_imsi_hash ON sims(imsi_hash) WHERE imsi_hash <> '';

messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sim_id TEXT NOT NULL REFERENCES sims(sim_id),
  device_mac TEXT NOT NULL,
  device_msg_id INTEGER NOT NULL,
  gw_epoch INTEGER NOT NULL DEFAULT 0,
  sender TEXT NOT NULL,
  text TEXT NOT NULL,
  received_at TEXT NOT NULL,
  code TEXT,                                        -- 提取的验证码，可空
  complete INTEGER NOT NULL DEFAULT 1,
  truncated INTEGER NOT NULL DEFAULT 0,
  blocked INTEGER NOT NULL DEFAULT 0,
  UNIQUE(device_mac, gw_epoch, device_msg_id)      -- 跨代/跨设备去重
)

deleted_messages(                                   -- 删除墓碑，按设备隔离
  sim_id TEXT NOT NULL,
  device_mac TEXT NOT NULL,
  gw_epoch INTEGER NOT NULL,
  device_msg_id INTEGER NOT NULL,
  deleted_at REAL NOT NULL DEFAULT (strftime('%s','now')),
  PRIMARY KEY(device_mac, gw_epoch, device_msg_id)
)

outbound(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sim_id TEXT NOT NULL REFERENCES sims(sim_id),
  device_mac TEXT NOT NULL DEFAULT '',             -- 发送时实际承载设备
  to_phone TEXT NOT NULL,
  text TEXT NOT NULL,
  device_msg_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',          -- pending/retry/sent/give_up
  parts INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_ts REAL NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'webui',            -- webui/admin_relay/telegram
  last_error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)

notify_jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  msg_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
  channel TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'           -- pending/retry/ok/give_up
)
```

其他表：`kv(key, value)`——游标 `cursor`、代次 `gw_epoch`、运行时配置 `cfg`、`tg_offset`、`keepalive_last_ts`、`schema_version` 等；`contacts(phone, alias, updated_at)`——号码别名。

SQLite 连接必须开启 `PRAGMA foreign_keys=ON`，并使用 WAL + `synchronous=NORMAL` + `busy_timeout=5000`。

> `deleted_messages` 墓碑仅服务于**短信**删除（防设备缓冲旧短信回流），与卡片/终端生命周期无关，不依赖 `sims` 外键，按 `TOMBSTONE_KEEP_DAYS` 清理。

---

## 五、产品设计与运行时

### 5.1 核心产品原则

| 原则 | 说明 |
|------|------|
| 后台全量同步 | Hub 同步所有启用设备，不受前端当前选择影响 |
| 全局时间线 + 单卡诊断 | 收件箱/发件记录展示全部卡片；状态、设置、AT 聚焦当前 SIM 卡 |
| 双身份模型 | `device_mac` 标识物理链路，`sim_id` 标识短信业务归属 |
| 业务策略全局统一 | 推送、转发、管理员、黑名单、模板只有一套配置 |
| 卡片不是租户 | SIM 卡只是短信入口/出口和状态对象 |
| 常态少请求 | 状态靠 heartbeat，消息靠 webhook 触发 pull，前端靠 SSE |
| 手动操作显式请求 | AT、状态刷新、强制拉取、OTA 不做后台自动探测 |
| 全新安装优先 | schema 和 API 直接按 v2 设计，不保留旧字段兼容包袱 |
| 禁用只停主动链路 | 禁用设备仍记录 webhook/heartbeat，但不 pull、不发送、不执行 AT |

### 5.2 用户体验设计

设置页「SIM 卡与瘦终端」板块展示所有已发现 SIM 卡及其当前承载瘦终端：备注名（绑 `sim_id`，如「主卡」）、SIM 标识（sim_id/IMSI 尾号/本机号码）、当前瘦终端（`device_mac` 展示为 `aa:bb:cc:dd:ee:ff`）、在线状态、运营商/信号、缓冲状态、最近活动、启用状态。用户可切换「当前卡片」，选择保存在浏览器 `localStorage`（不写 Hub 全局，避免多浏览器/标签页互相影响）。

| 页面 | v2 行为 |
|------|---------|
| 收件箱 | 默认显示全部卡片短信；列表显示卡片备注，详情回复按原短信 `sim_id` 发送 |
| 发送页 | 新建短信需选/默认选定一张可用卡，提交 body 必带 `sim_id`；发送记录显示全部卡片 |
| 状态页 | 展示当前卡片及当前承载设备状态 |
| 设置页 | 维护全局配置、卡片备注和瘦终端状态 |
| AT 控制台 | 只对当前卡片的当前承载设备发起 AT，设备忙返回 409 |

单卡场景前端自动选唯一启用卡；多卡场景收件箱/发件记录是全部卡片时间线，删除通过全局 id 反查所属卡片和设备，回复必须用原短信的 `sim_id`。

### 5.3 业务策略与风险边界

v2 采用「全局策略、设备归属」设计。

**全局统一配置**：`ADMIN_PHONE`、`BLACKLIST`、`FORWARD_SMS_TO`、所有通知通道（Telegram / Webhook JSON / Webhook GET / 钉钉 / 飞书 / Bark / PushPlus / Server酱 / Gotify）、推送模板、管理员短信代发开关、管理员代发默认卡片 `DEFAULT_SEND_SIM_ID`、通知失败重试策略、出站发送限制。

**不提供 per-device / per-SIM**：独立管理员号码、独立转发目标、独立通知渠道、独立黑名单、独立通知模板、多设备群发、**发信自动故障转移到其他 SIM 卡**。

管理员代发默认关闭。启用后只使用全局默认卡片 `DEFAULT_SEND_SIM_ID`；未配置/不存在/被禁用/无承载设备/承载设备离线时，代发直接失败并记录原因。浏览器配置字段名 `default_send_sim_id`，运行时配置项名 `DEFAULT_SEND_SIM_ID`，语义一致。

**通知模板占位符**：`{sender}` `{sender_name}` `{text}`/`{fulltext}` `{code}` `{time}`/`{timestamp}` `{id}`/`{raw_id}` `{text_md}`（已转义） `{sim_name}` `{sim_id}` `{device_name}` `{device_mac}`。占位符不区分大小写、兼容全角 `｛｝`，未识别的原样保留。各通道：Telegram/短信转发直接替换为纯文本；POST JSON 的模板是整个 JSON（占位符放双引号内，值自动转义）；GET 请求的地址本身就是模板（占位符自动 URL 编码）。默认推送内容应同时包含卡片来源和设备来源，避免多卡/换设备误判。

### 5.4 后端运行时架构

新增 `DeviceManager`（`core/device/manager.py`）与每设备 `DeviceRuntime`（`core/device/runtime.py`）。

**`DeviceManager` 职责**：从 `devices` 表加载启用设备；webhook 到达时创建/更新设备；按 MAC 获取运行时对象；管理每台设备的 poller task；维护全局设备 I/O 并发池（默认 4）；对 webhook 上报的 `ip`/`port` 做 SSRF 防护校验后才更新 `base_url`。

**`DeviceRuntime` 职责**：持有 `mac`、`base_url`、`trigger`、I/O 调度锁；维护当前忙碌操作和短信优先队列；维护 `pull_again` 标记（保证同一设备不并发 pull）；提供 `pull`/`send`/`delete`/`at` 设备调用。

`core/main.py` 的 `lifespan` 构造 `DeviceManager` 并拉起后台 worker，彼此通过 **SQLite 队列表 + `asyncio.Event`** 协作，不共享内存状态：

| worker | 文件 | 作用 | 唤醒方式 |
|--------|------|------|---------|
| poller | `device/poller.py` + `manager.py` | **每台启用设备一个 `device_loop` task**，游标拉取入库 | `runtime.trigger.set()`（webhook）+ `POLL_INTERVAL` 兜底 |
| notifier | `notify/notifier.py` | 消费 `notify_jobs` | `_wakeup` + 退避 |
| sender | `sms/sender.py` | 消费 `outbound` | `_wakeup`，poller 每轮也 `sender.wakeup()` |
| commands | `notify/commands.py` | Telegram getUpdates | 25s 长轮询 |
| keepalive | `device/keepalive.py` | 定时 AT 保号 | 到期检查 |

**核心一致性约束**：`poller.poll_device` 把「插入消息 + 派发通知/代发 + 写游标/时间戳」放在**同一事务提交，之后才推进游标**。任意点崩溃，重启后要么幂等重拉，要么已完整入库——不会出现「消息入库但通知丢失」。`db.insert_message` **不 commit**，由调用方串事务。

---

## 附录 A：v2 改造历程

v2 从单设备模型演进而来，分四阶段实施（已全部完成）：

- **Phase 1 协议与数据模型**：固件 webhook 增 `mac`/`seq_id`/`device_ts_ms`/完整 IMSI/`imsi_tail`/`msisdn`/ICCID 字符串；修复 ICCID 解析（保留 `0-9A-Fa-f`）；设备端实现 `/pull` 与批量 `/delete`；后端 v2 schema；MAC 规范化、sim_id 派生、SSRF 校验、设备/SIM 注册。验收：两台 mock 同时注册、同 token 不同 MAC 分别保存、同 IMSI 换 MAC 归属同一 sim_id。
- **Phase 2 多设备同步核心**：`DeviceManager` + per-device poller；消息入库带 `sim_id`/`device_mac`；游标/代次/墓碑按设备隔离；发件队列带 sim_id 与实际 device_mac；全局并发池。验收：两设备都产生 `id=1` 不冲突、A 离线不影响 B、删 A 不影响 B 同编号、禁用卡重启用立即补拉。
- **Phase 3 浏览器 API 与前端**：新增 `/api/devices`/`/api/sims`/卡片备注接口；设置页 SIM 卡与瘦终端板块；前端当前 sim_id 存 localStorage；状态/强制拉取/AT 带 sim_id；收件箱与发件记录用 `sim_id=all`、回复按原 sim_id。前端独立为 `web/` 项目（Vite + React，取代早期「拆 core/static/app.js」规划）。验收：可切换当前卡片、切换后状态/AT 同步、多标签页各自保持选择。
- **Phase 4 统一频率与监控**：SSE payload 卡片事件带 `sim_id`、设备事件带 `device_mac`；前端按 SSE 正常/断开切换刷新频率；`/metrics` 增设备 label。验收：SSE 正常时无高频消息轮询、heartbeat 维持状态新鲜度、Prometheus 可按 SIM/设备区分在线状态。

---

## 附录 B：请求示例精选

> 完整示例集见 [`openapi.yaml`](openapi.yaml)。以下用 `HUB=http://127.0.0.1:8025`、`DEVICE=http://192.168.1.88`、`TOKEN=<DEVICE_TOKEN>`、`WEB_TOKEN=<browser_auth_token>`、`SIM_ID=sim_0123456789abcdef`。

```bash
# 设备 boot 上报（Hub 学习地址、派生 sim_id、调度 pull）
curl -X POST "$HUB/hook/$TOKEN" -H 'Content-Type: application/json' -d '{
  "event":"boot","mac":"aa:bb:cc:dd:ee:ff","ip":"192.168.1.88","port":80,"fw":"2.1.0",
  "modem":{"ready":true,"imsi":"460001234567731","imsi_tail":"7731","msisdn":"13800138000"},
  "buffer":{"latest_id":0,"count":0,"capacity":50}}'
# -> {"ok": true, "pull_scheduled": true}

# Hub 拉短信(带身份)
curl "$DEVICE/$TOKEN/pull?after=123&limit=20&include_status=1"

# Hub 发短信
curl -X POST "$DEVICE/$TOKEN/send" -H 'Content-Type: application/json' \
  -d '{"to":"13800138000","text":"测试短信"}'
# -> {"ok": true, "device_msg_id": 126, "parts": 1}

# 浏览器登录
curl -X POST "$HUB/api/login" -H 'Content-Type: application/json' \
  -d '{"user":"admin","password":"<WEBUI_PASS>"}'   # -> {"token": "..."}

# 设备/卡片概览
curl "$HUB/api/devices" -H "Authorization: Bearer $WEB_TOKEN"

# 当前卡片状态
curl "$HUB/api/status?sim_id=$SIM_ID" -H "Authorization: Bearer $WEB_TOKEN"

# 全部卡片收件箱
curl "$HUB/api/messages?sim_id=all&limit=50" -H "Authorization: Bearer $WEB_TOKEN"

# 发送(进队列,不故障转移)
curl -X POST "$HUB/api/send" -H "Authorization: Bearer $WEB_TOKEN" -H 'Content-Type: application/json' \
  -d '{"sim_id":"sim_0123456789abcdef","to":"13800138000","text":"测试短信"}'
# -> {"ok": true, "queued": true, "id": 7, "parts": 1, "sim_id": "...", "device_mac": "..."}

# 单条删除(本地+墓碑+best-effort 设备)
curl -X DELETE "$HUB/api/messages/101" -H "Authorization: Bearer $WEB_TOKEN"
# -> {"ok": true, "device_deleted": 1}

# 保存运行时配置(即时生效)
curl -X POST "$HUB/api/config" -H "Authorization: Bearer $WEB_TOKEN" -H 'Content-Type: application/json' \
  -d '{"poll_interval":300,"admin_phone":"13800138000","blacklist":"1069,95533*"}'

# SSE 事件流(query token)
curl "$HUB/api/events?token=$WEB_TOKEN"
# -> retry: 3000
#    data: {"type":"new_messages","sim_id":"...","device_mac":"...","count":2}
```

---

## 附录 C：取舍与边界备注

- 设备伪造 MAC：可信局域网内可接受，公网不可接受。MAC 不是安全凭证，公网化前需增加每设备 secret。
- 全局 `DEVICE_TOKEN` 泄露：任一设备失窃会影响整个 Hub。v2 保持全局 token，后续安全版可加每设备 secret 与 `mac + signature`。
- IMSI 泄露：完整 IMSI 只走设备与 Hub 的受控通道；Hub 只持久化 `imsi_hash`/`imsi_tail`，浏览器 API 不返回完整 IMSI。
- 多设备并发请求风暴：heartbeat 60s 起步、兜底 pull `POLL_INTERVAL` 起步、前端依赖 SSE，避免风暴。
- 当前卡片不进后端全局状态：只存前端 `localStorage`，多浏览器互不影响。
- 管理员代发被滥用：默认关闭，只允许全局默认卡片，不支持多卡群发或故障转移代发。
- 设备离线影响：per-device runtime，不同设备并发，单设备失败不扩散。
