#pragma once

// Copy this file to config.h and change the values before compiling.

#define WIFI_SSID   "your-wifi-ssid"
#define WIFI_PASS   "your-wifi-password"

// API token, >=16 random chars. All endpoints live under /<token>/...
// Generate one: openssl rand -hex 12
#define API_TOKEN   "change-me-please"

// Hub webhook URL (http:// only). Leave empty to disable the doorbell;
// the hub's 60s polling still works without it.
// Example: "http://192.168.1.50:8025/hook/<API_TOKEN>"
#define WEBHOOK_URL ""

// Heartbeat webhook interval in seconds. Set to 0 to disable status push.
#define HEARTBEAT_INTERVAL_S 60

// 本机号码(选填)。多数 SIM/IoT 卡不烧录 MSISDN,AT+CNUM 只回 OK 查不到;
// 填这里后固件会把它上报给 hub 并在状态页显示。留空则启动时尝试 AT+CNUM 兜底。
#define MY_MSISDN  ""

#define FW_VERSION  "2.0.0"
// 设备协议版本(递增整数)；能力位掩码:1=async_job,2=batch_delete,4=mipcall,8=sms_rx_watchdog,16=delete_queue_stat,32=recovery_reason
#define DEVICE_PROTOCOL_VERSION 1
#define DEVICE_CAPABILITIES 63
