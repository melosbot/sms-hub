# sms-hub

把 4G 短信变成手机通知。插一张 SIM 卡，插上网线，短信自动推到 Telegram / 钉钉 / 飞书 / Bark / Webhook。也能从手机回复、群发、查历史。

```
SIM 卡 → ESP32C3 → 4G 模组 ──webhook──► Hub (Docker) ──通知──► 你的手机
                ▲                            │
                └──── 游标拉取 / 发送 / AT ────┘
```

> **v2**：支持多台设备 + 多张 SIM 卡接入同一个 Hub。完整设计见 [docs/guide.md](docs/guide.md)。

---

## 一分钟看懂

| 你做什么 | 它做什么 |
|----------|----------|
| 插上 SIM，刷固件，通电 | 自动连 WiFi、连 Hub |
| 收到验证码短信 | 自动提取验证码，推到 Telegram |
| 在 Telegram 回复 `/sms 13800138000 你好` | Hub 通过 4G 模组发出短信 |
| 打开网页 `:8025` | 搜索、导出、删除、查看设备状态 |
| 设备断电重启 | 不丢短信，游标自动续拉 |

---

## 硬件（¥28 起）

| 组件 | 型号 | 参考价 |
|------|------|--------|
| MCU | ESP32C3 Super Mini | ¥9.5 |
| 4G 模组 | 小蓝鲸 ML307R-DC | ¥16.3 |
| 天线 | 4G FPC 天线 | ¥2 |

**接线**（4 根线）：

```
ESP32C3          ML307R-DC
GPIO5  ───────── EN
GPIO3  ───────── RX
GPIO4  ───────── TX
GND    ───────── GND
5V     ───────── VCC
```

> 也可直接买 [小蓝鲸 WIFI 短信宝](https://item.taobao.com/item.htm?id=1003711355912) 成品。

---

## 刷固件

### 1. 安装 Arduino CLI

```bash
# macOS
brew install arduino-cli

# Linux
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh

# 初始化
arduino-cli config init
arduino-cli core update-index
arduino-cli core install esp32:esp32
arduino-cli lib install pdulib
```

### 2. 配置

```bash
cp firmware/config.example.h firmware/config.h
```

编辑 `firmware/config.h`，改这 4 项：

```c
#define WIFI_SSID   "你家WiFi名"
#define WIFI_PASS   "你家WiFi密码"
#define API_TOKEN   "openssl-rand-hex-12-生成的随机串"
#define WEBHOOK_URL "http://192.168.1.x:8025/hook/你的API_TOKEN"
```

生成 token：`openssl rand -hex 12`

### 3. 编译并刷入

```bash
# 编译（首次需下载工具链，约 3 分钟）
arduino-cli compile \
  --fqbn esp32:esp32:esp32c3:PartitionScheme=huge_app \
  ./firmware/firmware.ino

# 刷入（把设备用 USB 连到电脑）
arduino-cli upload -p /dev/ttyUSB0 \
  --fqbn esp32:esp32:esp32c3:PartitionScheme=huge_app \
  ./firmware/firmware.ino
```

> 分区方案必须选 `Huge APP`。本项目不支持 OTA 升级，每次更新都 USB 有线刷。

刷完后打开串口监视器（`115200 baud`），看到 `启动完成` 即成功。

---

## 启动 Hub

```bash
git clone https://github.com/你的/sms-hub.git
cd sms-hub

# 创建环境文件
cat > .env << 'EOF'
DEVICE_TOKEN=与固件API_TOKEN一致
WEBUI_PASS=设一个密码
EOF

# 启动
docker compose up -d
```

打开 `http://你的IP:8025`，账号 `admin`，密码是你设的 `WEBUI_PASS`。

发送一条短信到 SIM 卡，几秒内 Web UI 收件箱就会出现。

### 配置 Telegram 通知

在 Web UI → 设置 → 通知规则 → 添加 Telegram：

1. 填 `Bot Token`（从 [@BotFather](https://t.me/BotFather) 获取）
2. 填 `Chat ID`（从 [@userinfobot](https://t.me/userinfobot) 获取）
3. 启用

之后每条新短信都会自动推送。还支持钉钉、飞书、Bark、Server 酱、PushPlus、Gotify、自定义 Webhook。

---

## 目录

```
firmware/    ESP32C3 固件（Arduino + 可独立测试的 modem 核心）
core/        FastAPI Hub（API / 设备管理 / 通知 / 短信发送）
web/         React Web UI（移动优先）
test/        单元测试 + mock 设备 + demo 一键栈
docs/        架构设计 · 接口契约 · 运维手册
```

---

## 文档

| 想了解 | 看这篇 |
|--------|--------|
| 架构设计、数据模型、接口契约 | [docs/guide.md](docs/guide.md) |
| 部署、备份、固件刷写、真机回归 | [docs/operations.md](docs/operations.md) |
| 固件可靠性路线图 | [TODO.md](TODO.md) |

---

## 社区

感谢 [linux.do](https://linux.do) 社区的朋友们在项目早期提供的反馈、测试和建议。

---

## 许可证

MIT
