# sms-hub

> ESP32C3 + 4G 模组做短信收发和本地缓冲，通知、存储、搜索、发送、规则、配置和 Web UI 全部放在 Homelab 的 Docker 容器里。

> **v2**:支持**多台瘦终端 + 多张 SIM 卡**接入同一个 Hub。物理设备以 MAC 标识,SIM 卡以 IMSI 派生的 `sim_id` 标识,设备共用全局 `DEVICE_TOKEN`。v2 是全新安装(不兼容旧库,升级前删除旧 `sms.db`)。完整设计见 [`docs/guide.md`](docs/guide.md)。

灵感来自 [chenxuuu/sms_forwarding](https://github.com/chenxuuu/sms_forwarding)，在其基础上将业务逻辑从固件中剥离，演进为 **瘦终端 + Hub** 架构。

```
┌────────────────────────────┐       webhook / heartbeat       ┌──────────────────────────────┐
│  ESP32C3 + ML307R-DC       │ ──────────────────────────────► │  sms-hub (FastAPI + SQLite)   │
│  瘦终端固件                 │                                 │                              │
│  收:PDU 解码 / 长短信合并   │  ◄──── 游标拉取 / 发送 / AT ────  │  入库 · 搜索 · 验证码提取     │
│  发:PDU 编码 / 分段发送     │                                 │  Telegram 通知与命令          │
│  RAM 环形缓冲 50 条         │                                 │  移动优先 Web UI              │
│  NVS 镜像最近 16 条         │                                 │  保号 · 黑名单 · 短信转发     │
│  HTTP API: /<token>/...    │                                 │  失联告警 · 通知重试          │
└────────────────────────────┘                                 └──────────────────────────────┘
```

## 它能做什么

- **收信** —— PDU 解码、长短信合并、验证码自动提取、SQLite 持久化，按接收时间排序。
- **通知** —— 新短信推送 Telegram（兼容各类 Bot API 代理），失败入队、指数退避重试。
- **发送** —— Web UI、Telegram `/sms`、管理员短信代发，统一进入持久出站队列，后台重试。
- **Web UI** —— 移动优先：收件箱搜索/分页/批量删除/导出、联系人别名、发件记录、设备状态、运行时配置、模组工具箱、AT 控制台。
- **实时** —— webhook 触发即时拉取，前端 SSE 秒级刷新，30 秒轮询兜底。
- **可靠** —— 三层缓冲（RAM / NVS / 模组存储）、删除墓碑防回流、换机编号自愈、失联/恢复告警。
- **运维** —— 设备 IP 自学习、`/metrics` Prometheus 指标、Docker 部署、Web OTA。

## 硬件

低成本方案约 ¥27.8，仅推荐移动/联通卡：

| 组件 | 参考价 |
|------|--------|
| [ESP32C3 Super Mini](https://item.taobao.com/item.htm?id=852057780489&skuId=5813710390565) | ¥9.5 |
| [小蓝鲸 ML307R-DC 核心板](https://item.taobao.com/item.htm?id=797466121802&skuId=5722077108045) | ¥16.3 |
| 4G FPC 天线 | ¥2 |

成品可选 [小蓝鲸 WIFI 短信宝](https://item.taobao.com/item.htm?id=1003711355912)，支持移动/联通/电信。

接线：

```
ESP32C3 Super Mini        ML307R-DC 核心板
GPIO5 (MODEM_EN) ───────── EN
GPIO3 (TX)       ────────► RX
GPIO4 (RX)       ◄──────── TX
GND              ───────── GND
5V               ───────── VCC (5V)
```

## 快速开始

### 1. 刷固件

```bash
cp firmware/config.example.h firmware/config.h
# 编辑 firmware/config.h，改下面四项
```

```c
#define WIFI_SSID   "your-wifi-ssid"
#define WIFI_PASS   "your-wifi-password"
#define API_TOKEN   "paste-openssl-rand-hex-12-here"   // 至少 16 个随机字符
#define WEBHOOK_URL "http://<hub-ip>:8025/hook/<API_TOKEN>"
```

token 用 `openssl rand -hex 12` 生成。`WEBHOOK_URL` 可留空（此时 Hub 只能靠轮询工作，也无法自动发现设备），但推荐填上。

用 Arduino IDE 或 arduino-cli 编译刷入，**分区方案选 `No FS 4MB (2MB APP x2)`**：

```bash
arduino-cli compile --fqbn esp32:esp32:esp32c3:PartitionScheme=no_fs ./firmware/firmware.ino
```

依赖：ESP32 板支持（版型选 `MakerGO ESP32 C3 SuperMini`）+ `pdulib`。从默认分区切到 `no_fs` **必须先 USB 有线刷一次**；之后可访问 `http://<device-ip>/<token>/update` 做 Web OTA。

### 2. 启动 Hub

```bash
cp .env.example .env
# 编辑 .env，至少改 WEBUI_PASS，并填入与固件一致的 DEVICE_TOKEN
docker compose up -d --build
```

```dotenv
DEVICE_TOKEN=<与固件 API_TOKEN 一致>
WEBUI_PASS=<务必修改>
TG_BOT_TOKEN=<可留空>
TG_CHAT_ID=<可留空>
```

Web UI：`http://<homelab-ip>:8025/`，默认账号 `admin`，密码取自 `WEBUI_PASS`。首次收到设备 webhook 后，Hub 会把设备地址持久化到 SQLite，后续 DHCP 换地址也会自愈。

### 3. 验证

设备串口出现 `启动完成，API: http://<device-ip>/<token>/`，然后给 SIM 卡发一条短信，预期：

- Hub 日志出现 webhook 与拉取记录；
- Web UI 收件箱出现新短信；
- 若配置了 Telegram，会收到通知，验证码单独显示；
- `/api/status` 里 `hub.cursor` 追上设备 `buffer.latest_id`。

## 配置

`.env` 是启动默认值；Web UI 设置页保存的值会写入 SQLite 的 `cfg`，并即时覆盖当前进程配置（无需重启容器）。下表标注**可在 UI 改**的项。

| 变量 | 默认 | 说明 | 可 UI 改 |
|------|------|------|:---:|
| `DEVICE_TOKEN` | 空 | 必填，与固件 `API_TOKEN` 一致 | |
| `DEVICE_URL` | 空 | 可选，设备初始地址；webhook 自动学习后会被持久化地址覆盖 | |
| `POLL_INTERVAL` | `60` | webhook 之外的兜底拉取周期（秒） | ✓ |
| `ALERT_CONSECUTIVE_FAILS` | `3` | 连续拉取失败多少次后发失联告警 | ✓ |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | 空 | Telegram 通道启动默认值；运行时在设置页通知规则中修改 | ✓ |
| `TG_API_BASE` | `https://api.telegram.org` | Bot API 地址，可填局域网代理或反代 | ✓ |
| `TG_MANAGE_ENABLED` | `1` | 是否启用 Telegram 命令轮询（`/status` `/sms` `/history`） | ✓ |
| `FORWARD_SMS_TO` | 空 | 短信转发通道启动默认值；运行时在设置页通知规则中修改 | ✓ |
| `ADMIN_PHONE` | 空 | 管理员手机号；来自该号码的短信不通知，可用 `收件人:内容` 代发 | ✓ |
| `BLACKLIST` | 空 | 拦截规则，逗号分隔；支持完整号码、短前缀（`1069`）、星号后缀（`95533*`） | ✓ |
| `KEEPALIVE_INTERVAL_DAYS` | `0` | 保号间隔（天）；`0` 为禁用 | ✓ |
| `KEEPALIVE_PING_HOST` | `119.29.29.29` | 保号 ping 目标 | ✓ |
| `TOMBSTONE_KEEP_DAYS` | `30` | 已删除短信墓碑保留天数，防设备侧旧缓存回流 | ✓ |
| `MESSAGE_KEEP_DAYS` | `0` | 收件箱历史保留天数；`0` 为永久保留 | ✓ |
| `WEBUI_USER` / `WEBUI_PASS` | `admin` / `admin123` | Web UI 账号密码，生产环境必须改密码 | |
| `JWT_SECRET` | 自动生成 | 登录令牌签名密钥，未配置时写入 `/data/.jwt_secret` | |
| `LISTEN_PORT` | `8025` | Hub 监听端口 | |

设置页的通知规则支持 Telegram、短信转发、POST JSON Webhook、GET 请求 Webhook；顶部会显示当前启用的渠道。

**自定义推送模板**：每个通道可填一个模板，用占位符自定义推送内容，留空则用各通道默认格式。

| 占位符 | 含义 |
|------|------|
| `{sender}` | 发件人号码 |
| `{text}` / `{fulltext}` | 短信全文 |
| `{code}` | 验证码（无则为空） |
| `{time}` / `{timestamp}` | 接收时间 |
| `{sender_name}` | 通讯录备注名 |
| `{id}` / `{raw_id}` | 消息编号 / 全局唯一编号 |
| `{text_md}` | 已转义正文（Telegram 防格式破坏） |

占位符不区分大小写、兼容全角 `｛｝` 括号；未识别的原样保留。各通道注意：Telegram、短信转发直接替换为纯文本；POST JSON 的模板是整个 JSON，占位符放在双引号内（如 `{"code":"{code}"}`，值自动转义）；GET 请求的**地址本身就是模板**（如 `https://x.com/p?code={code}`，占位符自动 URL 编码）。

模板示例：

- 短信转发：`{sender_name}({sender}) 验证码 {code}`
- Telegram：`*{sender_name}*\n{text_md}`（`{text_md}` 已转义，正文含 `*` `_` 不破坏格式）
- GET 请求：`https://x.com/push?code={code}&from={sender}`
- POST JSON：`{"text":"{text}","code":"{code}","time":"{time}"}`

## 常用运维

```bash
docker compose logs -f sms-hub          # 查看 Hub 日志
docker compose restart sms-hub          # 重启 Hub
```

## 文档

| 文档 | 内容 |
|------|------|
| [设计与接口指南](docs/guide.md) | 架构、可靠性模型、HTTP 接口契约、数据模型、产品设计与运行时 |
| [运维操作](docs/operations.md) | 本地开发、测试、Docker 部署、备份恢复、固件刷写、Web OTA |

## 目录

```
firmware/   ESP32C3 瘦网关固件（Arduino，单文件）
core/       FastAPI Hub 后端
  app/      Web 认证与 API routes
  infra/    配置、SQLite、内部事件
  device/   设备 HTTP 客户端、poller、keepalive
  sms/      短信发送、规则、验证码提取、号码归一化
  notify/   多通道通知与 Telegram 命令
web/        Web UI 源码（Vite + React + shadcn），构建产物 web/dist 由 Hub 服务
test/       单元测试、demo 脚本、mock 设备
docs/       架构 / API / 开发与部署文档
```

## 设计边界

本项目的前提是**严格运行在可信局域网内**。设备 API 不做 TLS，Web UI 不做多用户权限——开发优先级放在短信可靠性、维护便利性和移动端体验上。完整的安全取舍与故障恢复行为见[设计与接口指南 · 安全边界](docs/guide.md#28-安全边界)。

## 致谢

- [chenxuuu/sms_forwarding](https://github.com/chenxuuu/sms_forwarding) — 本项目的灵感来源，提供了 ESP32C3 + ML307R-DC 硬件方案和 PDU 编解码的初始思路。

## 社区

本项目在 [LINUX DO](https://linux.do/) 社区分享与讨论 —— 一个活跃的中文技术交流社区,感谢社区的支持与反馈。社区主页:https://linux.do/

## 许可证

MIT，见 [LICENSE](LICENSE)。
