# sms-hub 运维操作指南（v2）

> 本文覆盖本地开发、测试、Docker 部署、备份恢复、固件刷写与 OTA。系统设计与 HTTP 接口契约见 [guide.md](guide.md)。

> **v2 说明**：Hub 现为多瘦终端 + 多 SIM 卡模型。v2 是**全新安装**：`SCHEMA_VERSION=4`，**不兼容旧版数据库**——升级前必须删除旧 `sms.db`（见下文「数据备份」）。设备共用全局 `DEVICE_TOKEN`，每台瘦终端以 MAC 区分，每张 SIM 以 IMSI 派生的 `sim_id` 标识。

## 目录

- [一、本地开发](#一本地开发)
- [二、测试](#二测试)
- [三、Docker 部署与备份恢复](#三docker-部署与备份恢复)
- [四、固件刷写与 Web OTA](#四固件刷写与-web-ota)

---

## 一、本地开发

### 一键 demo 栈（推荐）

仓库自带 `test/demo/demo` 脚本，一条命令拉起 Hub + mock 设备，适合开发与演示 Web UI：

```bash
test/demo/demo start
```

默认配置：

| 项 | 值 |
|----|----|
| Hub UI | `http://127.0.0.1:8025/` |
| 登录 | `admin` / `demo-pass` |
| Mock 控制台 | `http://127.0.0.1:8080/`（手动注入短信） |
| 设备 API | `http://127.0.0.1:8080/demo-token-12345678` |
| 轮询间隔 | 5s |

其他子命令：`stop` / `restart` / `status` / `logs` / `paths`。运行目录默认在 `test/demo/data/`（SQLite、日志、PID、env），可用 `DEMO_RUN_DIR` 覆盖。脚本自动检测 `.local/venv/bin/python` 与依赖。

**多设备 demo（v2）**：设置 `DEMO_SECOND_DEVICE_PORT` 即可拉起第二台 mock（不同 MAC + IMSI），验证多设备/多卡：

```bash
DEMO_SECOND_DEVICE_PORT=8081 test/demo/demo start
#   Mock 控制台 2: http://127.0.0.1:8081/  (MAC 112233445566)
```

demo 栈自动开启 `ALLOW_LOOPBACK_DEVICE=1`（Hub 与 mock 同机时放行 loopback 设备地址，**生产环境务必关闭**）。升级到 v2 前若 `test/demo/data/sms.db` 是旧版，需先删除：`find test/demo/data -name '*.db*' -delete`。

> **注意**：手动跑 Hub 时请显式指定 `DATA_DIR`；demo 栈固定写入 `test/demo/data/`，Docker 部署写入 Docker volume `/data`。`restart --fresh` 若失效，先用 `ss -ltnp` 对比端口 pid，防孤儿 Hub 占 8025。

### 手动起 Hub

```bash
python3 -m venv .local/venv && . .local/venv/bin/activate
pip install -r core/requirements.txt -r core/requirements-dev.txt

# 终端 1：mock 设备
DEVICE_TOKEN=test-token-local \
python test/demo/mock_device.py --port 8888 --hub http://127.0.0.1:8025

# 终端 2：Hub
DATA_DIR=/tmp/sms-hub-dev \
DEVICE_TOKEN=test-token-local \
DEVICE_URL=http://127.0.0.1:8888/test-token-local \
WEBUI_PASS=test123 \
python -m core.main
```

访问 `http://127.0.0.1:8025/`，账号 `admin`。`test/demo/mock_device.py` 提供模拟控制台，可手动注入短信、演示 UI。

### 项目布局

```
firmware/      瘦网关固件（firmware.ino + config.h）
core/main.py   FastAPI Hub 入口
core/app/      Web 认证与 API routes
core/infra/    配置、SQLite、内部事件
core/device/   设备 HTTP 客户端、poller、keepalive
core/sms/      短信发送、规则、验证码提取、号码归一化
core/notify/   多通道通知与 Telegram 命令
web/           Web UI 源码（Vite + React），构建产物 web/dist 由 Hub 服务
test/unit/     pytest 测试套件
test/demo/     demo 一键栈、mock 设备、demo 运行数据
docs/          guide.md（设计+接口）/ operations.md（本文）/ openapi.yaml（机器契约）
```

---

## 二、测试

### 自动测试

```bash
.local/venv/bin/python -m pytest -q test/unit
```

测试聚焦高风险链路（并发、时序、边界、转义），覆盖矩阵：

| 文件 | 覆盖点 |
|------|--------|
| `test_contract` | API 契约防漂移：openapi.yaml ↔ 运行时路由、前端调用全注册、设备协议文档化 |
| `test_db_migration` | 全新安装 schema 基线、过期消息与墓碑清理 |
| `test_device` | 出站短信段数估算（UCS2 单元，与固件一致） |
| `test_device_lock` | 设备请求串行化、短信优先插队、交互式 AT 忙时 `409` |
| `test_extractor` | 验证码提取真实短信语料（含连字符边界） |
| `test_main_contacts` | 消息含联系人别名 / 搜索、发送号码归一化 |
| `test_mms` | MMS 通知 UDH concat + WAP payload 解码 |
| `test_notifier` | 通知通道投递与重试 → 放弃；钉钉/飞书签名对照官方算法；6 渠道 format/target |
| `test_poller` | SCTS 解析、`age_s` 时间回推、原子入库、游标回退自愈 |
| `test_rules_auth` | 号码归一化、黑名单、登录令牌往返 / 过期 / 篡改 |
| `test_sender` | 出站队列发送成功与重试；**不故障转移**（禁用卡/离线 → give_up，不转其他卡，见 docs/guide.md §5.3） |
| `test_status` | `/api/status` 心跳 / 数据双平面在线判定 |
| `test_v2_multi` | 多设备硬约束：sim_id 派生、临时卡合并、跨设备同编号不冲突、墓碑隔离、并发池上限、SSRF |

浏览器冒烟（Playwright，需先起 demo 栈并构建前端 `web/dist`）：

```bash
test/demo/demo start
cd web && npm run build
NODE_PATH=$(npm root -g) node test/browser-smoke.cjs
```

冒烟覆盖登录、各 tab 导航、搜索、详情，并断言发送（含中文）、SSE 实时推送、删除、强制拉取、401 错误态的真实 HTTP 往返，须保持 0 console/page error。

### 真机回归

自动测试全部跑在 mock 设备上；真机行为（PDU 解码、AT 时序、模组兼容）需手动回归。每次刷固件、换模组、改 poller/notifier/device 关键链路后按以下要点回归一次：

- **基础启动**：串口打印 API 地址 → boot webhook 成功 → Hub 自动学习 `device_url` → Web UI 可登录 → `/api/status` 双平面字段正常。
- **收信**：普通短信、验证码短信（提取 `code`）、长短信合并；webhook 断开后轮询补拉；Hub 重启按游标续拉；设备重启后 NVS 恢复最近短信。
- **通知**：通知送达（Telegram / 各推送渠道）；特殊字符短信不失败；黑名单短信入库但不通知；通知失败后重试。
- **发送**：Web UI 单段 / 多段；Telegram `/sms`；发送失败写入发件记录；管理员短信代发（`收件人:内容`）。
- **管理**：单条/批量删除不回流；清 NVS / 换机后 `gw_epoch` 自愈；AT 控制台 `ATI`/`AT+CESQ`；设备忙时 AT 返回 `409` 且不阻塞 poller；Web OTA 上传重启；Docker volume 备份可恢复。
- **保号（可选）**：启用后到期执行 AT 流程，结果推送通知。

### v2 多设备 / 多卡回归（含验收要点）

v2 改造后，除上表外还需覆盖：

- **MAC 上报**：webhook body 含 `mac`（小写无分隔）；Hub 设置页每台瘦终端显示 `aa:bb:cc:dd:ee:ff`。
- **IMSI 派生**：boot/heartbeat 含 `modem.imsi`；Hub 生成稳定 `sim_id`（同一 SIM 换设备归属不变）；`AT+CIMI` 读不到时生成临时卡 `sim_tmp_<mac>` 并提示。
- **`/pull`**：Hub 走 `GET /{token}/pull?after=&limit=&include_status=`；`include_status=1` 响应含 `status.modem`。
- **批量 `/delete`**：Web 删除触发 `POST /{token}/delete` body `{"device_msg_ids":[...]}`，响应逐条 `found`。
- **多设备**：两台瘦终端同时收信不串卡、不丢；A 离线不影响 B；设置页可切卡、改备注、禁用/启用（启用即补拉）。
- **SSRF**：Hub 拒绝设备上报的公网/loopback（生产）/Hub 自身地址。
- **不故障转移**：给禁用卡或离线承载设备发信 → 直接 `give_up`，不转到其他卡。
- **SSE**：正常时无高频消息轮询；断开时进入 30s 兜底轮询；通知内容包含卡片来源和设备来源。

可靠性验收要点：webhook 丢失后兜底 pull 补齐；heartbeat 发现 `latest_id > cursor` 触发补拉；消息 ID 回退只影响该设备 `gw_epoch`；批量删除写设备维度墓碑；多设备不互相阻塞且全局并发不超过 4；Web UI 删除在设备不可达时仍本地成功。

---

## 三、Docker 部署与备份恢复

```bash
cp .env.example .env       # 编辑：至少改 WEBUI_PASS，填 DEVICE_TOKEN
docker compose up -d --build
```

`docker-compose.yml` 使用 Docker named volume `sms-hub-data` 挂到容器 `/data`（即 `DATA_DIR`），`8025` 端口暴露 Hub。`core/Dockerfile` 多阶段：先在 `web/` 用 Vite 构建前端，再装 Python 依赖到精简前缀，最后组装仅含 Python 解释器 + tzdata + 精简依赖 + 应用代码 + 前端产物的运行镜像，带 healthcheck（`/api/health`）。配置项详见 [README 配置表](../README.md#配置)。

常用：

```bash
docker compose logs -f sms-hub     # 日志
docker compose restart sms-hub     # 重启
```

运行时配置（Telegram、黑名单、保号、通知通道等）在 Web UI 设置页修改即时生效，无需重启容器。

### 数据备份与恢复

```bash
mkdir -p backups
docker compose stop sms-hub
docker run --rm -v sms-hub-data:/data:ro -v "$PWD/backups:/backup" alpine \
  sh -c 'cd /data && tar czf /backup/sms-data-$(date +%Y%m%d-%H%M%S).tgz .'
docker compose start sms-hub
```

> SQLite 开了 WAL，**别直接 `cp`**——用 `sqlite3 .backup` 或停服后 `tar`（上例）。恢复：停 Hub，把备份包解回 `sms-hub-data` volume，再启动 Hub。定期把 `backups/` 纳入 Homelab 常规备份。

---

## 四、固件刷写与 Web OTA

> 只刷 ESP32-C3。ML307R-DC / 短信宝 4G 模组继续用默认 AT 固件，不需要刷。
>
> **先备份，再刷机**。没确认备份文件有效前，不要执行 `erase-flash` 或 IDE 的 "Erase All Flash"。

### 准备配置

```bash
cp firmware/config.example.h firmware/config.h
# 编辑 firmware/config.h
```

v2 固件配置项（与 v1 不同，账号密码在 Hub 侧而非固件）：

```c
#define WIFI_SSID   "your-wifi-ssid"
#define WIFI_PASS   "your-wifi-password"
#define API_TOKEN   "openssl-rand-hex-12"      // 至少 16 随机字符，与 Hub DEVICE_TOKEN 一致（全局共享）
#define WEBHOOK_URL "http://<hub-ip>:8025/hook/<API_TOKEN>"
#define HEARTBEAT_INTERVAL_S 60                 // 60-300 秒；0 = 禁用心跳
#define MY_MSISDN   ""                          // 本机号码（选填，留空则 AT+CNUM 兜底）
#define FW_VERSION  "2.0.0"
// v2：设备 MAC 由 WiFi.macAddress() 自动上报；SIM 身份由 AT+CIMI 的 IMSI 派生。
```

### 安装工具链

Arduino IDE 2.x：开发板管理地址加 `https://espressif.github.io/arduino-esp32/package_esp32_index.json`，安装 `esp32 by Espressif`，库管理器装 `pdulib`。

或 arduino-cli：

```bash
arduino-cli config add board_manager.additional_urls \
  https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index && arduino-cli core install esp32:esp32
arduino-cli lib install pdulib
```

### 编译与有线刷写

**分区方案必须选 `No FS 4MB (2MB APP x2)`**——从其他分区切到该方案，第一次必须 USB 有线刷，之后才能 Web OTA。

```bash
# 仓库内工具链
HOME=$PWD/.local/arduino-home .local/tools/arduino-cli/arduino-cli compile \
  --fqbn esp32:esp32:esp32c3:PartitionScheme=no_fs \
  ./firmware/firmware.ino

# 上传（替换 COM7 为实际端口）
arduino-cli upload --fqbn esp32:esp32:esp32c3:PartitionScheme=no_fs \
  --port COM7 ./firmware/firmware.ino
```

Arduino IDE 关键选项：开发板 `MakerGO ESP32 C3 SuperMini`（没有就选 `ESP32C3 Dev Module`，但分区方案必须对）、`Partition Scheme: No FS 4MB (2MB APP x2)`、`Upload Speed: 460800`。上传失败可降到 `115200`，或手动进下载模式（按住 `BOOT`，点按 `RST`，上传开始后松开 `BOOT`）。

串口（115200）应能看到：连 WiFi → 打印 IP → `启动完成,API: http://<device-ip>/<token>/`。之后访问 `http://<device-ip>/<token>/status` 验证。

### 备份与擦除（esptool）

备份整片 Flash（最重要的恢复源）：

```bash
py -m esptool --chip esp32c3 --port COM7 -b 460800 \
  read-flash 0 ALL ./backups/full-flash.bin
```

仅在确认备份有效后，需要清旧 NVS 时才擦除：

```bash
py -m esptool --chip esp32c3 --port COM7 erase-flash   # 擦除后必须重新有线刷写
```

恢复原固件：`write-flash 0x0 ./backups/full-flash.bin`。完整备份只能恢复到同一块芯片。

### Web OTA 升级

USB 有线刷过一次 `no_fs` 分区后，同分区方案内的后续升级走 Web OTA，无需连线。

**升级前检查**：分区方案是 `No FS 4MB`；`API_TOKEN` 与 Hub `DEVICE_TOKEN` 一致；`WEBHOOK_URL` 指向当前 Hub；供电稳定；Hub 状态页确认设备在线。

```bash
# 浏览器：http://<device-ip>/<token>/update  选 .bin 上传
# 或命令行：
curl -F "firmware=@firmware.ino.bin" "http://<device-ip>/<token>/update"
```

页面返回「升级完成，设备即将重启」后，等设备重启，在 Hub 状态页确认固件版本。

**失败处理**：上传页打不开 → 查 IP/token/WiFi，或等设备 webhook 让 Hub 重新学习地址；上传中断 → 不要立即断电，等响应或重启；升级后 Hub 连不上 → 串口查 IP/token/webhook，必要时 USB 重刷；反复重启 → USB 刷回上一个已验证 `.bin`。

> Web OTA 只上传 `firmware.ino.bin`。不要在 OTA 页面上传 `full-flash.bin`、bootloader、partitions、`boot_app0`——这些会破坏分区，只能 USB 有线恢复。
