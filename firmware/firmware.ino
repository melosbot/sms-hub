// V2 瘦网关固件:ESP32C3 + ML307R-DC
// 只做短信收发与缓存,HTTP API 形如 /<token>/(status|get|send|at|update),
// 转发/通知/规则全部在 hub(Docker)侧。设计文档见 docs/V2_DESIGN.md。
#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <pdulib.h>
#include <ESPmDNS.h>
#include <Update.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <esp_ota_ops.h>

#include "config.h"

// 串口映射
#define TXD 3
#define RXD 4
#define MODEM_EN_PIN 5

#ifndef LED_BUILTIN
#define LED_BUILTIN 8
#endif

#define MODEM_INIT_ATTEMPTS 12
#define MODEM_NETWORK_WAIT_MS 60000
#define WIFI_CONNECT_WAIT_MS 45000
#define SERIAL_RX_BUFFER_SIZE 4096
#define MODEM_LINE_BUFFER_SIZE 768
#define MAX_PDU_LENGTH 1024

#define SMS_BUFFER_SIZE 50            // RAM 环形缓冲容量
#define MAX_STORED_TEXT_BYTES 1024    // 单条正文缓存上限(UTF-8 字节)
#define NVS_MIRROR_COUNT 16           // NVS 镜像最近 N 条(重启恢复)
#define NVS_MIRROR_TEXT_MAX 512       // 镜像正文截断长度(字节)
#define MAX_PENDING_STORED_SMS 16
#define MAX_STORED_SMS_READ_ATTEMPTS 5
#define READ_RETRY_BASE_MS 15000UL
#define READ_RETRY_MAX_MS 900000UL

#define SMS_SINGLE_PART_CHAR_LIMIT 70 // UCS2 单段上限
#define SMS_SPLIT_PART_CHAR_LIMIT 60  // 分段时每段上限
#define MAX_OUTGOING_SMS_PARTS 5

#define MAX_CONCAT_PARTS 10           // 长短信最大分段数
#define CONCAT_TIMEOUT_MS 30000       // 长短信合并超时
#define MAX_CONCAT_MESSAGES 5         // 同时合并的组数

#define WEBHOOK_TIMEOUT_MS 1500
#define MODEM_STATUS_CACHE_MS 60000   // /status 里蜂窝信息的缓存时长
#ifndef HEARTBEAT_INTERVAL_S
#define HEARTBEAT_INTERVAL_S 60       // heartbeat webhook 周期;<=0 可禁用
#endif
#ifndef MY_MSISDN
#define MY_MSISDN ""                  // 本机号码(选填);config.h 未定义时留空→AT+CNUM 兜底
#endif
#define MAX_DIAG_TEXT_LENGTH 96
#define AT_PROXY_MAX_TIMEOUT_MS 15000

Preferences preferences;
PDU pdu = PDU(4096);
WebServer server(80);

// ───────────────────────── 运行状态 ─────────────────────────
bool modemCommandActive = false;
bool directSmsPduExpected = false;
String pendingDirectSmsPdu = "";      // AT 等待期暂存的 +CMT 直推 PDU
bool mdnsStarted = false;
bool httpServerStarted = false;
bool modemInitOk = false;
bool modemInitDone = false;
bool networkRegistered = false;
unsigned long lastWiFiReconnectTime = 0;
String lastError = "";

String modemModel = "";
int cachedCereg = 0;                  // 0=未注册 1=home 2=搜索中 5=roaming
int cachedCsqDbm = 0;                 // 0 表示未知
String cachedOperator = "";
String msisdn = "";                    // 本机号码:MY_MSISDN 优先,否则 AT+CNUM 兜底(多数 SIM 查不到)
String cachedIccid = "";               // SIM ICCID(开机查一次,随心跳上报)
String cachedImsiTail = "";            // IMSI 后 4 位(脱敏,展示用)
String cachedImsi = "";                // 完整 IMSI(v2:仅上报 Hub 派生 sim_id,不展示/不入 NVS)
String cachedApn = "";                 // APN(开机查一次)
int cachedCfun = 1;                    // 飞行模式:1=正常 4=飞行(随 refreshModemStatus 刷新)
bool cachedPdpActive = false;          // PDP 承载激活状态(随刷新)
int64_t modemStatusRefreshedUs = -1;

uint32_t rxTotal = 0;                 // NVS 持久化
uint32_t txTotal = 0;                 // NVS 持久化
uint32_t webhookFailTotal = 0;
uint32_t droppedTotal = 0;            // 覆盖了 hub 未拉取的消息的次数

// ── v2 身份:MAC 物理链路标识 + webhook 单调序号 ──
String deviceMac = "";                // WiFi.macAddress() 规范化为小写无分隔(启动时填)
uint32_t webhookSeqId = 0;            // 同物理设备内单调递增的 webhook 序号

// ───────────────────────── 短信环形缓冲 ─────────────────────────
struct SmsRecord {
  bool valid;
  uint32_t id;
  int64_t receivedUs;   // esp_timer_get_time();-1 表示恢复后年龄未知
  String sender;
  String scts;          // 短信中心时间戳,原样透传
  String text;
  bool complete;        // 长短信是否合并完整
  bool truncated;       // 正文是否被截断
  bool isMms = false;   // 彩信通知(WAP Push):上报原始 PDU hex,Hub 合并解码
};

SmsRecord smsRing[SMS_BUFFER_SIZE];
int ringNext = 0;                 // 下一个写入槽位
uint32_t nextMsgId = 1;           // NVS 持久化
uint32_t lastPulledId = 0;        // 从 /get?after= 学到的 hub 游标

// ───────────────────────── 待读取的模组存储索引 ─────────────────────────
struct PendingStoredSms {
  bool inUse;
  int index;
  uint8_t attempts;
  unsigned long nextAttempt;
};
PendingStoredSms pendingStoredSms[MAX_PENDING_STORED_SMS];

// ───────────────────────── 长短信合并 ─────────────────────────
struct SmsPart {
  bool valid;
  String text;
};
struct ConcatSms {
  bool inUse;
  int refNumber;
  String sender;
  String timestamp;
  int totalParts;
  int receivedParts;
  unsigned long firstPartTime;
  SmsPart parts[MAX_CONCAT_PARTS];
};
ConcatSms concatBuffer[MAX_CONCAT_MESSAGES];

// ───────────────────────── 前置声明 ─────────────────────────
String jsonEscape(const String& str);
bool sendATandWaitOK(const char* cmd, unsigned long timeout);
bool sendSMS(const char* phoneNumber, const char* message);
void processSmsContent(const char* sender, const char* text, const char* timestamp, bool complete, bool isMms = false);
bool processPduLine(const String& pduLine);
bool handleModemUrcLine(String& line);
String assembleConcatSms(int slot);
bool isHexString(const String& str);
bool isMmsNotificationPdu(const String& pduLine);
bool enqueueStoredSmsIndex(int index);
bool sendWebhook(const char* event);
bool sendWebhookRaw(const String& body);
void scheduleWebhookRetry(const char* event);

// ───────────────────────── 通用工具 ─────────────────────────
bool timeReached(unsigned long now, unsigned long target) {
  return (long)(now - target) >= 0;
}

int64_t nowUs() {
  return esp_timer_get_time();
}

String truncateUtf8Bytes(const String& str, int maxBytes, const char* suffix) {
  if (maxBytes <= 0 || (int)str.length() <= maxBytes) return str;
  int suffixLen = strlen(suffix);
  int cut = maxBytes - suffixLen;
  if (cut <= 0) cut = maxBytes;
  if (cut > (int)str.length()) cut = str.length();
  while (cut > 0 && (((uint8_t)str.charAt(cut)) & 0xC0) == 0x80) cut--;
  String result = str.substring(0, cut);
  if (cut + suffixLen <= maxBytes) result += suffix;
  return result;
}

String limitText(const String& str, int maxBytes) {
  return truncateUtf8Bytes(str, maxBytes, "...");
}

void recordLastError(const String& message) {
  lastError = limitText(message, MAX_DIAG_TEXT_LENGTH);
  Serial.println("诊断错误: " + lastError);
}

String jsonEscape(const String& str) {
  String result = "";
  result.reserve(str.length() + 8);
  for (unsigned int i = 0; i < str.length(); i++) {
    char c = str.charAt(i);
    if (c == '"') result += "\\\"";
    else if (c == '\\') result += "\\\\";
    else if (c == '\n') result += "\\n";
    else if (c == '\r') result += "\\r";
    else if (c == '\t') result += "\\t";
    else if ((uint8_t)c < 0x20) result += ' ';
    else result += c;
  }
  return result;
}

String boolJson(bool value) {
  return value ? "true" : "false";
}

const char* resetReasonText() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "POWERON";
    case ESP_RST_EXT: return "EXTERNAL";
    case ESP_RST_SW: return "SOFTWARE";
    case ESP_RST_PANIC: return "PANIC";
    case ESP_RST_INT_WDT: return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT: return "WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    default: return "UNKNOWN";
  }
}

// 极简 JSON 字段提取(请求体只有 hub 一个调用方,字段都是平铺的)。
// 注意:只解 \n \r \t \" \\ ,不支持 \uXXXX——hub 侧(device.py)必须以
// ensure_ascii=False 发送原始 UTF-8,这是双方的固定约定。
String extractJsonStringAt(const String& src, int idx) {
  String result = "";
  bool escaped = false;
  for (int i = idx; i < (int)src.length(); i++) {
    char c = src.charAt(i);
    if (escaped) {
      if (c == 'n') result += '\n';
      else if (c == 'r') result += '\r';
      else if (c == 't') result += '\t';
      else result += c;
      escaped = false;
    } else if (c == '\\') {
      escaped = true;
    } else if (c == '"') {
      break;
    } else {
      result += c;
    }
  }
  return result;
}

String extractJsonString(const String& src, const char* key) {
  String pattern = "\"" + String(key) + "\"";
  int idx = src.indexOf(pattern);
  if (idx < 0) return "";
  idx = src.indexOf(':', idx + pattern.length());
  if (idx < 0) return "";
  idx = src.indexOf('"', idx + 1);
  if (idx < 0) return "";
  return extractJsonStringAt(src, idx + 1);
}

long extractJsonNumber(const String& src, const char* key, long fallback) {
  String pattern = "\"" + String(key) + "\"";
  int idx = src.indexOf(pattern);
  if (idx < 0) return fallback;
  idx = src.indexOf(':', idx + pattern.length());
  if (idx < 0) return fallback;
  idx++;
  while (idx < (int)src.length() && (src.charAt(idx) == ' ')) idx++;
  String num = "";
  if (idx < (int)src.length() && src.charAt(idx) == '-') { num += '-'; idx++; }
  while (idx < (int)src.length() && src.charAt(idx) >= '0' && src.charAt(idx) <= '9') {
    num += src.charAt(idx);
    idx++;
  }
  return num.length() > 0 ? num.toInt() : fallback;
}

// ───────────────────────── NVS:计数器 + 消息镜像 ─────────────────────────
// 镜像最近 NVS_MIRROR_COUNT 条消息(正文截 NVS_MIRROR_TEXT_MAX 字节),
// 重启后恢复进环形缓冲且 ID 连续,hub 游标无感。no_fs 分区 NVS 共 20KB。
struct __attribute__((packed)) NvsMsgHeader {
  uint8_t flags;       // bit0=complete bit1=truncated
  char sender[21];
  char scts[21];
};

const char* nvsMsgKey(uint32_t id) {
  static char buf[12];
  snprintf(buf, sizeof(buf), "m%lu", (unsigned long)id);
  return buf;
}

void loadCounters() {
  preferences.begin("v2", true);
  nextMsgId = preferences.getUInt("nid", 1);
  rxTotal = preferences.getUInt("rx", 0);
  txTotal = preferences.getUInt("tx", 0);
  preferences.end();
  if (nextMsgId == 0) nextMsgId = 1;
  Serial.printf("NVS 计数器: next_id=%lu rx=%lu tx=%lu\n",
                (unsigned long)nextMsgId, (unsigned long)rxTotal, (unsigned long)txTotal);
}

void persistTxTotal() {
  preferences.begin("v2", false);
  preferences.putUInt("tx", txTotal);
  preferences.end();
}

// 每条新短信:一次 NVS 会话写计数器 + 镜像 blob,并删过期 key
void persistMessage(const SmsRecord& rec) {
  static uint8_t buf[sizeof(NvsMsgHeader) + NVS_MIRROR_TEXT_MAX];
  NvsMsgHeader hdr = {};
  hdr.flags = (rec.complete ? 1 : 0) | (rec.truncated ? 2 : 0);
  strlcpy(hdr.sender, rec.sender.c_str(), sizeof(hdr.sender));
  strlcpy(hdr.scts, rec.scts.c_str(), sizeof(hdr.scts));

  String body = rec.text;
  if ((int)body.length() > NVS_MIRROR_TEXT_MAX) {
    body = truncateUtf8Bytes(body, NVS_MIRROR_TEXT_MAX, "");
    hdr.flags |= 2;  // 镜像副本被截断
  }
  memcpy(buf, &hdr, sizeof(hdr));
  memcpy(buf + sizeof(hdr), body.c_str(), body.length());
  size_t len = sizeof(hdr) + body.length();

  preferences.begin("v2", false);
  preferences.putUInt("nid", nextMsgId);
  preferences.putUInt("rx", rxTotal);
  if (preferences.putBytes(nvsMsgKey(rec.id), buf, len) != len) {
    Serial.println("NVS 镜像写入失败(空间不足?),仅计数器已保存");
  }
  if (rec.id > NVS_MIRROR_COUNT) {
    preferences.remove(nvsMsgKey(rec.id - NVS_MIRROR_COUNT));
  }
  preferences.end();
}

// 开机恢复:把 NVS 里 [next_id-16, next_id-1] 的镜像按 id 升序插回环形缓冲。
// 停电时长未知,receivedUs 置 -1(/get 返回 age_s=-1,hub 用 scts 兜底)。
void restoreMessagesFromNvs() {
  if (nextMsgId <= 1) return;
  static uint8_t buf[sizeof(NvsMsgHeader) + NVS_MIRROR_TEXT_MAX + 1];
  uint32_t from = nextMsgId > NVS_MIRROR_COUNT ? nextMsgId - NVS_MIRROR_COUNT : 1;
  int restored = 0;

  preferences.begin("v2", true);
  for (uint32_t id = from; id < nextMsgId; id++) {
    size_t len = preferences.getBytes(nvsMsgKey(id), buf, sizeof(buf) - 1);
    if (len < sizeof(NvsMsgHeader)) continue;
    NvsMsgHeader hdr;
    memcpy(&hdr, buf, sizeof(hdr));
    hdr.sender[sizeof(hdr.sender) - 1] = 0;
    hdr.scts[sizeof(hdr.scts) - 1] = 0;
    buf[len] = 0;

    SmsRecord& slot = smsRing[ringNext];
    slot.valid = true;
    slot.id = id;
    slot.receivedUs = -1;
    slot.sender = String(hdr.sender);
    slot.scts = String(hdr.scts);
    slot.text = String((const char*)(buf + sizeof(hdr)));
    slot.complete = hdr.flags & 1;
    slot.truncated = hdr.flags & 2;
    ringNext = (ringNext + 1) % SMS_BUFFER_SIZE;
    restored++;
  }
  preferences.end();
  if (restored > 0) {
    Serial.printf("NVS 恢复 %d 条消息(id %lu..%lu)\n",
                  restored, (unsigned long)from, (unsigned long)(nextMsgId - 1));
  }
}

// ───────────────────────── NVS:出站队列持久化 ─────────────────────────
// 多段长短信发送中途重启时恢复。最多 OUTBOUND_NVS_COUNT 条,每条正文截断。
#define OUTBOUND_NVS_COUNT 4
#define OUTBOUND_TEXT_MAX 256

struct __attribute__((packed)) OutboundNvsEntry {
  char to[21];
  uint8_t textLen;
  char text[OUTBOUND_TEXT_MAX];
};

// 将待发消息写入 NVS(发送前调用)
int persistOutbound(const char* to, const char* text) {
  // 找一个空槽
  for (int i = 0; i < OUTBOUND_NVS_COUNT; i++) {
    char key[8];
    snprintf(key, sizeof(key), "ob%d", i);
    if (!preferences.isKey(key)) {
      OutboundNvsEntry entry;
      memset(&entry, 0, sizeof(entry));
      strncpy(entry.to, to, 20);
      int len = strlen(text);
      if (len > OUTBOUND_TEXT_MAX) len = OUTBOUND_TEXT_MAX;
      entry.textLen = (uint8_t)len;
      memcpy(entry.text, text, len);
      preferences.putBytes(key, &entry, sizeof(entry));
      Serial.printf("出站消息持久化: ob%d (%s -> %s)\n", i, to, text);
      return i;
    }
  }
  return -1;  // 槽满
}

// 发送成功后删除 NVS 条目
void clearOutboundNvs(int slot) {
  if (slot < 0 || slot >= OUTBOUND_NVS_COUNT) return;
  char key[8];
  snprintf(key, sizeof(key), "ob%d", slot);
  preferences.remove(key);
  Serial.printf("出站消息已清除: ob%d\n", slot);
}

// boot 时恢复未完成的出站消息
void restoreOutboundFromNvs() {
  for (int i = 0; i < OUTBOUND_NVS_COUNT; i++) {
    char key[8];
    snprintf(key, sizeof(key), "ob%d", i);
    if (!preferences.isKey(key)) continue;
    OutboundNvsEntry entry;
    size_t len = preferences.getBytes(key, &entry, sizeof(entry));
    if (len < sizeof(OutboundNvsEntry) - OUTBOUND_TEXT_MAX) continue;
    entry.to[20] = '\0';
    int tLen = entry.textLen;
    if (tLen > OUTBOUND_TEXT_MAX) tLen = OUTBOUND_TEXT_MAX;
    entry.text[tLen] = '\0';
    Serial.printf("恢复出站消息 ob%d: %s -> %s\n", i, entry.to, entry.text);
    bool ok = sendSMS(entry.to, entry.text);
    if (ok) {
      clearOutboundNvs(i);
      Serial.printf("恢复出站消息 ob%d 发送成功\n", i);
    } else {
      Serial.printf("恢复出站消息 ob%d 发送失败,保留待重试\n", i);
    }
  }
}

// ───────────────────────── 环形缓冲 ─────────────────────────
int ringCount() {
  int count = 0;
  for (int i = 0; i < SMS_BUFFER_SIZE; i++) {
    if (smsRing[i].valid) count++;
  }
  return count;
}

uint32_t ringOldestId() {
  for (int n = 0; n < SMS_BUFFER_SIZE; n++) {
    int idx = (ringNext + n) % SMS_BUFFER_SIZE;
    if (smsRing[idx].valid) return smsRing[idx].id;
  }
  return 0;
}

uint32_t ringLatestId() {
  return nextMsgId - 1;
}

// hub 还没拉走的消息会被覆盖时返回 true(背压依据)
bool ringWouldDropUnpulled() {
  SmsRecord& slot = smsRing[ringNext];
  return slot.valid && slot.id > lastPulledId;
}

void storeSms(const char* sender, const char* text, const char* timestamp, bool complete, bool isMms = false) {
  SmsRecord& slot = smsRing[ringNext];
  if (slot.valid && slot.id > lastPulledId) {
    droppedTotal++;
    Serial.printf("缓冲溢出:覆盖未拉取消息 #%lu(累计丢弃 %lu)\n",
                  (unsigned long)slot.id, (unsigned long)droppedTotal);
  }

  String body = String(text);
  bool truncated = (int)body.length() > MAX_STORED_TEXT_BYTES;
  if (truncated) body = truncateUtf8Bytes(body, MAX_STORED_TEXT_BYTES, "\n[内容已截断]");

  slot.valid = true;
  slot.id = nextMsgId++;
  slot.receivedUs = nowUs();
  slot.sender = String(sender);
  slot.scts = String(timestamp);
  slot.text = body;
  slot.complete = complete;
  slot.truncated = truncated;
  slot.isMms = isMms;
  ringNext = (ringNext + 1) % SMS_BUFFER_SIZE;

  rxTotal++;
  persistMessage(slot);

  Serial.printf("短信入缓冲 #%lu 来自 %s\n", (unsigned long)slot.id, sender);
  scheduleWebhookRetry("sms");
}

// ───────────────────────── 模组串口 ─────────────────────────
bool readModemLine(String& out) {
  static char lineBuf[MODEM_LINE_BUFFER_SIZE];
  static int linePos = 0;

  while (Serial1.available()) {
    char c = Serial1.read();
    if (c == '\n') {
      lineBuf[linePos] = 0;
      out = String(lineBuf);
      out.trim();
      linePos = 0;
      return true;
    }
    if (c == '\r') continue;

    if (linePos < MODEM_LINE_BUFFER_SIZE - 1) {
      lineBuf[linePos++] = c;
    } else {
      linePos = 0;
      Serial.println("模组响应行过长,已丢弃该行");
    }
  }
  return false;
}

bool isFinalAtResponseLine(const String& line) {
  return line == "OK" ||
         line == "ERROR" ||
         line.startsWith("+CME ERROR") ||
         line.startsWith("+CMS ERROR");
}

int parseCmtiIndex(const String& line) {
  int commaIdx = line.lastIndexOf(',');
  if (commaIdx < 0 || commaIdx + 1 >= (int)line.length()) return -1;
  String indexStr = line.substring(commaIdx + 1);
  indexStr.trim();
  int index = indexStr.toInt();
  return index > 0 ? index : -1;
}

bool lastAtStorageFull = false;  // 最近一次 AT 响应是否包含存储满错误

// 发送 AT 命令并获取响应。短信 URC 在这里被截获,不混入普通响应。
String sendATCommand(const char* cmd, unsigned long timeout) {
  bool wasActive = modemCommandActive;
  modemCommandActive = true;
  lastAtStorageFull = false;
  Serial1.println(cmd);

  unsigned long start = millis();
  String resp = "";
  resp.reserve(256);
  while (millis() - start < timeout) {
    String line;
    if (readModemLine(line)) {
      if (line.length() == 0) continue;
      if (handleModemUrcLine(line)) continue;

      resp += line;
      resp += "\r\n";
      if (line.startsWith("+CMS ERROR: 322") || line.indexOf("CMS ERROR: 322") >= 0) {
        lastAtStorageFull = true;
      }
      if (isFinalAtResponseLine(line)) {
        modemCommandActive = wasActive;
        return resp;
      }
    } else {
      delay(2);
    }
  }

  modemCommandActive = wasActive;
  return resp;
}

bool sendATandWaitOK(const char* cmd, unsigned long timeout) {
  String resp = sendATCommand(cmd, timeout);
  int start = 0;
  while (start <= (int)resp.length()) {
    int end = resp.indexOf('\n', start);
    if (end < 0) end = resp.length();
    String line = resp.substring(start, end);
    line.trim();
    if (line == "OK") return true;
    if (line == "ERROR" || line.startsWith("+CME ERROR") || line.startsWith("+CMS ERROR")) return false;
    start = end + 1;
  }
  return false;
}

bool isHexString(const String& str) {
  if (str.length() == 0) return false;
  for (unsigned int i = 0; i < str.length(); i++) {
    char c = str.charAt(i);
    if (!((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F') || (c >= 'a' && c <= 'f'))) {
      return false;
    }
  }
  return true;
}

int hexNibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;
}

int pduByteAt(const String& pduLine, int byteIndex) {
  int pos = byteIndex * 2;
  if (pos < 0 || pos + 1 >= (int)pduLine.length()) return -1;
  int hi = hexNibble(pduLine.charAt(pos));
  int lo = hexNibble(pduLine.charAt(pos + 1));
  if (hi < 0 || lo < 0) return -1;
  return (hi << 4) | lo;
}

bool isMmsNotificationPdu(const String& pduLine) {
  if (!isHexString(pduLine) || (pduLine.length() % 2) != 0) return false;
  int byteLen = pduLine.length() / 2;
  if (byteLen < 32) return false;

  int smscLen = pduByteAt(pduLine, 0);
  int pos = 1 + smscLen;
  if (smscLen < 0 || pos + 12 >= byteLen) return false;

  int firstOctet = pduByteAt(pduLine, pos++);
  // SMS-DELIVER + UDH. MMS notification over SMS arrives as WAP Push in UDH.
  if ((firstOctet & 0x03) != 0 || (firstOctet & 0x40) == 0) return false;

  int oaLen = pduByteAt(pduLine, pos++);
  int toa = pduByteAt(pduLine, pos++);
  if (oaLen < 0 || toa < 0) return false;
  int addrBytes = ((toa & 0x70) == 0x50) ? ((oaLen * 7 + 7) / 8) : ((oaLen + 1) / 2);
  pos += addrBytes;
  if (pos + 10 >= byteLen) return false;

  pos++;      // PID
  pos++;      // DCS; MMS WAP Push is binary, but markers below are the real guard.
  pos += 7;   // SCTS
  int udl = pduByteAt(pduLine, pos++);
  if (udl <= 0 || pos >= byteLen) return false;

  int udStart = pos;
  int udhl = pduByteAt(pduLine, pos++);
  int udhEnd = udStart + 1 + udhl;
  if (udhl < 0 || udhEnd > byteLen) return false;

  bool wapPushPort = false;
  while (pos + 1 < udhEnd) {
    int iei = pduByteAt(pduLine, pos++);
    int ieLen = pduByteAt(pduLine, pos++);
    if (iei < 0 || ieLen < 0 || pos + ieLen > udhEnd) return false;
    if (iei == 0x05 && ieLen == 4) {
      int destPort = (pduByteAt(pduLine, pos) << 8) | pduByteAt(pduLine, pos + 1);
      if (destPort == 0x0B84 || destPort == 0x23F0) wapPushPort = true;
    }
    pos += ieLen;
  }
  if (!wapPushPort) return false;

  bool hasMmsContentType = false;    // WSP media: application/vnd.wap.mms-message
  bool hasNotificationInd = false;   // X-Mms-Message-Type: m-notification-ind
  int scanEnd = min(byteLen, udhEnd + 80);
  for (int i = udhEnd; i < scanEnd; i++) {
    int b = pduByteAt(pduLine, i);
    if (b == 0xBE) hasMmsContentType = true;
    if (b == 0x8C && i + 1 < scanEnd && pduByteAt(pduLine, i + 1) == 0x82) {
      hasNotificationInd = true;
    }
  }
  return hasMmsContentType && hasNotificationInd;
}

// ───────────────────────── 短信发送(PDU)─────────────────────────
// 统计文本编码为 UCS2 后的单元数(4 字节 UTF-8 字符按代理对计 2)
int ucs2UnitCount(const String& text) {
  int count = 0;
  for (int i = 0; i < (int)text.length(); i++) {
    uint8_t c = (uint8_t)text.charAt(i);
    if ((c & 0xC0) == 0x80) continue;
    count += ((c & 0xF8) == 0xF0) ? 2 : 1;
  }
  return count;
}

String takeUcs2Chunk(const String& text, int& byteCursor, int maxUnits) {
  int units = 0;
  int end = byteCursor;
  while (end < (int)text.length()) {
    uint8_t c = (uint8_t)text.charAt(end);
    int charLen = 1;
    if ((c & 0xE0) == 0xC0) charLen = 2;
    else if ((c & 0xF0) == 0xE0) charLen = 3;
    else if ((c & 0xF8) == 0xF0) charLen = 4;
    int weight = (charLen == 4) ? 2 : 1;
    if (units + weight > maxUnits) break;
    units += weight;
    end += charLen;
  }
  String part = text.substring(byteCursor, end);
  byteCursor = end;
  return part;
}

int countSmsChunks(const String& text, int maxUnits) {
  int cursor = 0;
  int parts = 0;
  while (cursor < (int)text.length()) {
    int before = cursor;
    takeUcs2Chunk(text, cursor, maxUnits);
    if (cursor == before) break;
    parts++;
  }
  return parts;
}

String validateOutgoingSms(const String& phone, const String& content) {
  if (phone.length() < 3 || phone.length() > 20) return "号码长度无效";
  for (unsigned int i = 0; i < phone.length(); i++) {
    char c = phone.charAt(i);
    if (c >= '0' && c <= '9') continue;
    if (c == '+' && i == 0) continue;
    return "号码只能是数字,可带+前缀";
  }
  int units = ucs2UnitCount(content);
  int maxUnits = SMS_SPLIT_PART_CHAR_LIMIT * MAX_OUTGOING_SMS_PARTS;
  if (units > maxUnits) {
    return "内容过长:" + String(units) + " 字符,上限 " + String(maxUnits) +
           " 字符(约" + String(MAX_OUTGOING_SMS_PARTS) + "条)";
  }
  return "";
}

bool waitSmsPrompt(unsigned long timeout) {
  unsigned long start = millis();
  String line = "";
  while (millis() - start < timeout) {
    while (Serial1.available()) {
      char c = Serial1.read();
      Serial.print(c);
      // 提示符只在行首出现,避免 URC 文本里的 '>' 被误判
      if (c == '>' && line.length() == 0) return true;
      if (c == '\n') {
        line.trim();
        if (line.length() > 0) handleModemUrcLine(line);
        line = "";
      } else if (c != '\r') {
        if (line.length() < MODEM_LINE_BUFFER_SIZE - 1) line += c;
        else line = "";
      }
    }
    delay(2);
  }
  return false;
}

bool sendSingleSMSPDU(const char* phoneNumber, const char* message) {
  pdu.setSCAnumber();  // 使用默认短信中心
  int pduLen = pdu.encodePDU(phoneNumber, message);
  if (pduLen < 0) {
    Serial.printf("PDU编码失败,错误码: %d\n", pduLen);
    return false;
  }

  // 等待提示符期间可能处理 URC 并复用全局 pdu 对象,先把编码结果拷出来
  String pduData = pdu.getSMS();

  String cmgsCmd = "AT+CMGS=";
  cmgsCmd += pduLen;

  bool wasActive = modemCommandActive;
  modemCommandActive = true;
  Serial1.println(cmgsCmd);

  if (!waitSmsPrompt(5000)) {
    Serial.println("未收到>提示符");
    modemCommandActive = wasActive;
    return false;
  }

  Serial1.print(pduData);
  Serial1.write(0x1A);  // Ctrl+Z 结束

  unsigned long start = millis();
  while (millis() - start < 30000) {
    String line;
    if (readModemLine(line)) {
      if (line.length() == 0) continue;
      if (handleModemUrcLine(line)) continue;
      Serial.println("CMGS> " + line);
      if (line == "OK") {
        modemCommandActive = wasActive;
        return true;
      }
      if (line == "ERROR" || line.startsWith("+CME ERROR") || line.startsWith("+CMS ERROR")) {
        modemCommandActive = wasActive;
        return false;
      }
    } else {
      delay(5);
    }
  }
  Serial.println("短信发送超时");
  modemCommandActive = wasActive;
  return false;
}

// 长内容按 UCS2 单元数拆为多条发送
bool sendSMS(const char* phoneNumber, const char* message) {
  String text = String(message);
  if (ucs2UnitCount(text) <= SMS_SINGLE_PART_CHAR_LIMIT) {
    return sendSingleSMSPDU(phoneNumber, message);
  }

  int totalParts = countSmsChunks(text, SMS_SPLIT_PART_CHAR_LIMIT);
  int cursor = 0;
  for (int i = 0; i < totalParts; i++) {
    String part = takeUcs2Chunk(text, cursor, SMS_SPLIT_PART_CHAR_LIMIT);
    String payload = "(" + String(i + 1) + "/" + String(totalParts) + ") " + part;
    if (!sendSingleSMSPDU(phoneNumber, payload.c_str())) return false;
    delay(800);
  }
  return true;
}

// ───────────────────────── 模组上下电 ─────────────────────────
void blink_short(unsigned long gap_time = 500) {
  // 模组初始化等待期间也响应 HTTP 请求
  if (httpServerStarted) server.handleClient();
  digitalWrite(LED_BUILTIN, LOW);
  delay(50);
  digitalWrite(LED_BUILTIN, HIGH);
  delay(gap_time);
  if (httpServerStarted) server.handleClient();
}

void modemPowerCycle() {
  pinMode(MODEM_EN_PIN, OUTPUT);
  Serial.println("EN 拉低:关闭模组");
  digitalWrite(MODEM_EN_PIN, LOW);
  delay(1200);
  Serial.println("EN 拉高:开启模组");
  digitalWrite(MODEM_EN_PIN, HIGH);
  delay(6000);  // 等模组完全启动再发 AT
}

bool retryATStep(const char* cmd, unsigned long timeout, const char* retryMsg, const char* okMsg) {
  for (int i = 0; i < MODEM_INIT_ATTEMPTS; i++) {
    if (sendATandWaitOK(cmd, timeout)) {
      Serial.println(okMsg);
      return true;
    }
    Serial.println(retryMsg);
    blink_short();
  }
  Serial.println("初始化步骤失败,继续启动");
  return false;
}

bool waitCEREG() {
  String resp = sendATCommand("AT+CEREG?", 2000);
  int idx = resp.indexOf("+CEREG:");
  if (idx < 0) return false;
  String line = resp.substring(idx);
  int lineEnd = line.indexOf('\n');
  if (lineEnd > 0) line = line.substring(0, lineEnd);
  int commaIdx = line.indexOf(',');
  if (commaIdx < 0 || commaIdx + 1 >= (int)line.length()) return false;
  int stat = line.substring(commaIdx + 1).toInt();
  cachedCereg = stat;
  return stat == 1 || stat == 5;
}

bool waitNetworkRegistered(unsigned long timeoutMs) {
  unsigned long start = millis();
  while (millis() - start < timeoutMs) {
    if (waitCEREG()) return true;
    Serial.println("等待网络注册...");
    blink_short();
  }
  Serial.println("网络注册超时,继续启动");
  return false;
}


// /status 用的蜂窝信息,带缓存避免每次都打 AT
void refreshModemStatus(bool force) {
  if (!modemInitDone) return;
  int64_t now = nowUs();
  if (!force && modemStatusRefreshedUs > 0 &&
      now - modemStatusRefreshedUs < (int64_t)MODEM_STATUS_CACHE_MS * 1000) {
    return;
  }
  modemStatusRefreshedUs = now;

  waitCEREG();  // 更新 cachedCereg

  String resp = sendATCommand("AT+CESQ", 2000);
  int idx = resp.indexOf("+CESQ:");
  if (idx >= 0) {
    // +CESQ: rxlev,ber,rscp,ecno,rsrq,rsrp → rsrp 是最后一个字段
    String params = resp.substring(idx + 6);
    int lineEnd = params.indexOf('\n');
    if (lineEnd > 0) params = params.substring(0, lineEnd);
    int lastComma = params.lastIndexOf(',');
    if (lastComma >= 0) {
      int rsrp = params.substring(lastComma + 1).toInt();
      cachedCsqDbm = (rsrp != 99 && rsrp != 255) ? (-140 + rsrp) : 0;
    }
  }

  resp = sendATCommand("AT+COPS?", 2000);
  idx = resp.indexOf(",\"");
  if (idx >= 0) {
    int endIdx = resp.indexOf('"', idx + 2);
    if (endIdx > idx) cachedOperator = resp.substring(idx + 2, endIdx);
  }

  // 飞行模式 / PDP 承载:随 60s 刷新一并更新,UI 免 AT 读取
  resp = sendATCommand("AT+CFUN?", 2000);
  idx = resp.indexOf("+CFUN:");
  if (idx >= 0) {
    int c = resp.substring(idx + 6).toInt();
    if (c > 0) cachedCfun = c;
  }
  resp = sendATCommand("AT+CGACT?", 2000);
  cachedPdpActive = resp.indexOf("+CGACT: 1,1") >= 0;

}

// ───────────────────────── 收短信:URC / 存储读取 ─────────────────────────
bool enqueueStoredSmsIndex(int index) {
  if (index <= 0) return false;

  for (int i = 0; i < MAX_PENDING_STORED_SMS; i++) {
    if (pendingStoredSms[i].inUse && pendingStoredSms[i].index == index) return true;
  }

  for (int i = 0; i < MAX_PENDING_STORED_SMS; i++) {
    if (!pendingStoredSms[i].inUse) {
      pendingStoredSms[i].inUse = true;
      pendingStoredSms[i].index = index;
      pendingStoredSms[i].attempts = 0;
      pendingStoredSms[i].nextAttempt = millis();
      Serial.printf("短信存储索引已入队: %d\n", index);
      return true;
    }
  }

  Serial.println("短信索引队列已满,无法加入新短信");
  recordLastError("短信索引队列已满");
  return false;
}

void clearPendingStoredSmsSlot(int slot) {
  pendingStoredSms[slot].inUse = false;
  pendingStoredSms[slot].index = 0;
  pendingStoredSms[slot].attempts = 0;
  pendingStoredSms[slot].nextAttempt = 0;
}

unsigned long retryDelayForAttempt(uint8_t attempts) {
  unsigned long delayMs = READ_RETRY_BASE_MS;
  for (uint8_t i = 0; i < attempts && delayMs < READ_RETRY_MAX_MS; i++) {
    delayMs *= 2;
    if (delayMs > READ_RETRY_MAX_MS) {
      delayMs = READ_RETRY_MAX_MS;
      break;
    }
  }
  return delayMs;
}

bool readStoredSmsAtIndex(int index, bool forwardRawOnFailure) {
  String cmd = "AT+CMGR=" + String(index);
  String resp = sendATCommand(cmd.c_str(), 5000);
  bool decoded = false;
  String pduLineSeen = "";

  int start = 0;
  while (start <= (int)resp.length()) {
    int end = resp.indexOf('\n', start);
    if (end < 0) end = resp.length();
    String line = resp.substring(start, end);
    line.trim();
    if (line.length() > 0 && line.length() <= MAX_PDU_LENGTH && isHexString(line)) {
      pduLineSeen = line;
      decoded = processPduLine(line);
      break;
    }
    start = end + 1;
  }

  // 最后一次尝试仍解码失败:按原始 PDU 入缓冲,宁可乱码也不悄悄丢短信
  if (!decoded && forwardRawOnFailure && pduLineSeen.length() > 0) {
    String text = "[PDU解码失败,原始数据]\n" + pduLineSeen;
    processSmsContent("解码失败", text.c_str(), "", true);
    decoded = true;
  }

  if (decoded) {
    String delCmd = "AT+CMGD=" + String(index);
    if (!sendATandWaitOK(delCmd.c_str(), 3000)) {
      Serial.println("短信已处理,但删除模组存储失败");
      recordLastError("删除模组存储短信失败");
    }
  }

  return decoded;
}

// SIM 存储满时自动删除最旧的已读短信,腾出空间接收新短信
// 返回 true 表示至少删了一条
bool cleanupSimStorage() {
  Serial.println("SIM 存储满,尝试清理已读短信...");
  int deleted = 0;
  for (int round = 0; round < 10; round++) {
    if (modemCommandActive) break;
    String resp = sendATCommand("AT+CMGL=1", 5000);  // 1=已读短信
    int minIndex = 9999;
    int start = 0;
    while (start <= (int)resp.length()) {
      int end = resp.indexOf('\n', start);
      if (end < 0) end = resp.length();
      String line = resp.substring(start, end);
      line.trim();
      if (line.startsWith("+CMGL:")) {
        int colonIdx = line.indexOf(':');
        int commaIdx = line.indexOf(',', colonIdx + 1);
        if (colonIdx >= 0 && commaIdx > colonIdx) {
          int idx = line.substring(colonIdx + 1, commaIdx).toInt();
          if (idx > 0 && idx < minIndex) minIndex = idx;
        }
      }
      start = end + 1;
    }
    if (minIndex >= 9999) {
      Serial.println("SIM 中无已读短信可清理");
      break;
    }
    String delCmd = "AT+CMGD=" + String(minIndex);
    if (sendATandWaitOK(delCmd.c_str(), 3000)) {
      Serial.printf("已删除 SIM 短信 #%d\n", minIndex);
      deleted++;
    } else {
      break;
    }
  }
  if (deleted > 0) {
    Serial.printf("SIM 清理完成,共删除 %d 条已读短信\n", deleted);
    recordLastError("SIM 存储满,已自动清理");
  }
  return deleted > 0;
}

void processPendingStoredSms() {
  if (modemCommandActive) return;
  // 背压:将要覆盖 hub 未拉取的消息时,暂停从模组读取,让短信留在模组里
  if (ringWouldDropUnpulled()) return;

  unsigned long now = millis();
  for (int i = 0; i < MAX_PENDING_STORED_SMS; i++) {
    if (!pendingStoredSms[i].inUse || !timeReached(now, pendingStoredSms[i].nextAttempt)) continue;

    int index = pendingStoredSms[i].index;
    bool lastAttempt = pendingStoredSms[i].attempts + 1 >= MAX_STORED_SMS_READ_ATTEMPTS;
    if (readStoredSmsAtIndex(index, lastAttempt)) {
      clearPendingStoredSmsSlot(i);
    } else if (lastAttempt) {
      // 连 CMGR 都多次读不到内容:删除并放弃,避免永久占用队列槽位和模组存储
      Serial.printf("读取存储短信 %d 多次失败,删除并放弃该索引\n", index);
      recordLastError("读取存储短信多次失败");
      String delCmd = "AT+CMGD=" + String(index);
      sendATandWaitOK(delCmd.c_str(), 3000);
      clearPendingStoredSmsSlot(i);
      // 如果是存储满导致的失败,清理已读短信腾出空间
      if (lastAtStorageFull) cleanupSimStorage();
    } else if (lastAtStorageFull) {
      // 存储满:立即清理已读短信,不走指数退避
      Serial.println("检测到 SIM 存储满,触发清理");
      cleanupSimStorage();
      pendingStoredSms[i].attempts++;
      pendingStoredSms[i].nextAttempt = millis() + 2000;  // 清理后 2s 重试
    } else {
      pendingStoredSms[i].attempts++;
      pendingStoredSms[i].nextAttempt = now + retryDelayForAttempt(pendingStoredSms[i].attempts);
    }
    return;  // 每轮 loop 最多读一条,避免长时间阻塞 HTTP
  }
}

void enqueueStoredSmsFromList() {
  String resp = sendATCommand("AT+CMGL=4", 8000);
  int start = 0;
  while (start <= (int)resp.length()) {
    int end = resp.indexOf('\n', start);
    if (end < 0) end = resp.length();
    String line = resp.substring(start, end);
    line.trim();
    if (line.startsWith("+CMGL:")) {
      int colonIdx = line.indexOf(':');
      int commaIdx = line.indexOf(',', colonIdx + 1);
      if (colonIdx >= 0 && commaIdx > colonIdx) {
        int index = line.substring(colonIdx + 1, commaIdx).toInt();
        if (index > 0) enqueueStoredSmsIndex(index);
      }
    }
    start = end + 1;
  }
}

// ───────────────────────── 长短信合并 ─────────────────────────
void initConcatBuffer() {
  for (int i = 0; i < MAX_CONCAT_MESSAGES; i++) {
    concatBuffer[i].inUse = false;
    concatBuffer[i].receivedParts = 0;
    for (int j = 0; j < MAX_CONCAT_PARTS; j++) {
      concatBuffer[i].parts[j].valid = false;
      concatBuffer[i].parts[j].text = "";
    }
  }
}

int findOrCreateConcatSlot(int refNumber, const char* sender, int totalParts) {
  for (int i = 0; i < MAX_CONCAT_MESSAGES; i++) {
    if (concatBuffer[i].inUse &&
        concatBuffer[i].refNumber == refNumber &&
        concatBuffer[i].sender.equals(sender)) {
      return i;
    }
  }

  for (int i = 0; i < MAX_CONCAT_MESSAGES; i++) {
    if (!concatBuffer[i].inUse) {
      concatBuffer[i].inUse = true;
      concatBuffer[i].refNumber = refNumber;
      concatBuffer[i].sender = String(sender);
      concatBuffer[i].totalParts = totalParts;
      concatBuffer[i].receivedParts = 0;
      concatBuffer[i].firstPartTime = millis();
      for (int j = 0; j < MAX_CONCAT_PARTS; j++) {
        concatBuffer[i].parts[j].valid = false;
        concatBuffer[i].parts[j].text = "";
      }
      return i;
    }
  }

  // 没有空闲槽位:先把最老的不完整消息入缓冲再覆盖
  int oldestSlot = 0;
  unsigned long oldestTime = concatBuffer[0].firstPartTime;
  for (int i = 1; i < MAX_CONCAT_MESSAGES; i++) {
    if (concatBuffer[i].firstPartTime < oldestTime) {
      oldestTime = concatBuffer[i].firstPartTime;
      oldestSlot = i;
    }
  }

  Serial.println("长短信缓存已满,覆盖前先保存最老的不完整消息");
  if (concatBuffer[oldestSlot].receivedParts > 0) {
    String partialText = assembleConcatSms(oldestSlot);
    processSmsContent(concatBuffer[oldestSlot].sender.c_str(),
                      partialText.c_str(),
                      concatBuffer[oldestSlot].timestamp.c_str(),
                      false);
  }
  concatBuffer[oldestSlot].inUse = true;
  concatBuffer[oldestSlot].refNumber = refNumber;
  concatBuffer[oldestSlot].sender = String(sender);
  concatBuffer[oldestSlot].totalParts = totalParts;
  concatBuffer[oldestSlot].receivedParts = 0;
  concatBuffer[oldestSlot].firstPartTime = millis();
  for (int j = 0; j < MAX_CONCAT_PARTS; j++) {
    concatBuffer[oldestSlot].parts[j].valid = false;
    concatBuffer[oldestSlot].parts[j].text = "";
  }
  return oldestSlot;
}

String assembleConcatSms(int slot) {
  String result = "";
  int limit = concatBuffer[slot].totalParts;
  if (limit > MAX_CONCAT_PARTS) limit = MAX_CONCAT_PARTS;
  for (int i = 0; i < limit; i++) {
    if (concatBuffer[slot].parts[i].valid) {
      result += concatBuffer[slot].parts[i].text;
    } else {
      result += "[缺失分段" + String(i + 1) + "]";
    }
  }
  return result;
}

void clearConcatSlot(int slot) {
  concatBuffer[slot].inUse = false;
  concatBuffer[slot].receivedParts = 0;
  concatBuffer[slot].sender = "";
  concatBuffer[slot].timestamp = "";
  for (int j = 0; j < MAX_CONCAT_PARTS; j++) {
    concatBuffer[slot].parts[j].valid = false;
    concatBuffer[slot].parts[j].text = "";
  }
}

void checkConcatTimeout() {
  unsigned long now = millis();
  for (int i = 0; i < MAX_CONCAT_MESSAGES; i++) {
    if (concatBuffer[i].inUse) {
      if (now - concatBuffer[i].firstPartTime >= CONCAT_TIMEOUT_MS) {
        Serial.printf("长短信超时,以不完整内容入缓冲: 参考号=%d, 已收=%d/%d\n",
                      concatBuffer[i].refNumber,
                      concatBuffer[i].receivedParts,
                      concatBuffer[i].totalParts);
        String fullText = assembleConcatSms(i);
        processSmsContent(concatBuffer[i].sender.c_str(),
                          fullText.c_str(),
                          concatBuffer[i].timestamp.c_str(),
                          false);
        clearConcatSlot(i);
      }
    }
  }
}

// ───────────────────────── 短信内容入口 ─────────────────────────
void processSmsContent(const char* sender, const char* text, const char* timestamp, bool complete, bool isMms) {
  Serial.println("=== 收到短信 ===");
  Serial.printf("发送者: %s\n", sender);
  Serial.printf("时间戳: %s\n", timestamp);
  storeSms(sender, text, timestamp, complete, isMms);
}

bool processPduLine(const String& pduLine) {
  if (!isHexString(pduLine) || pduLine.length() > MAX_PDU_LENGTH) {
    Serial.println("PDU数据无效或过长,已忽略");
    recordLastError("PDU数据无效或过长");
    return false;
  }

  if (isMmsNotificationPdu(pduLine)) {
    // 彩信通知(WAP Push):固件不解 WBXML,上报原始 PDU hex 让 Hub 合并 concat + 解码。
    // 尝试 decodePDU 取发件人(OA 在正文前,通常能解出);失败则留空。
    String sender;
    if (pdu.decodePDU(pduLine.c_str())) sender = pdu.getSender();
    Serial.println("检测到彩信通知(WAP Push),上报原始 PDU");
    processSmsContent(sender.c_str(), pduLine.c_str(), "", true, true);
    return true;
  }

  if (!pdu.decodePDU(pduLine.c_str())) {
    Serial.println("PDU解析失败");
    recordLastError("PDU解析失败");
    return false;
  }

  int* concatInfo = pdu.getConcatInfo();
  int refNumber = concatInfo[0];
  int partNumber = concatInfo[1];
  int totalParts = concatInfo[2];
  // PDU 可声明最多 255 段,超出缓存上限的部分丢弃,防止越界
  if (totalParts > MAX_CONCAT_PARTS) {
    Serial.printf("长短信声明%d段,超过上限,仅合并前%d段\n", totalParts, MAX_CONCAT_PARTS);
    totalParts = MAX_CONCAT_PARTS;
  }

  if (totalParts > 1 && partNumber > 0) {
    Serial.printf("收到长短信分段 %d/%d (参考号 %d)\n", partNumber, totalParts, refNumber);

    int slot = findOrCreateConcatSlot(refNumber, pdu.getSender(), totalParts);
    int partIndex = partNumber - 1;
    if (partIndex >= 0 && partIndex < MAX_CONCAT_PARTS) {
      if (!concatBuffer[slot].parts[partIndex].valid) {
        concatBuffer[slot].parts[partIndex].valid = true;
        concatBuffer[slot].parts[partIndex].text = String(pdu.getText());
        concatBuffer[slot].receivedParts++;
        if (concatBuffer[slot].receivedParts == 1) {
          concatBuffer[slot].timestamp = String(pdu.getTimeStamp());
        }
      }
    }

    if (concatBuffer[slot].receivedParts >= totalParts) {
      Serial.println("长短信已收齐,合并入缓冲");
      String fullText = assembleConcatSms(slot);
      processSmsContent(concatBuffer[slot].sender.c_str(),
                        fullText.c_str(),
                        concatBuffer[slot].timestamp.c_str(),
                        true);
      clearConcatSlot(slot);
    }
    return true;
  }

  processSmsContent(pdu.getSender(), pdu.getText(), pdu.getTimeStamp(), true);
  return true;
}

bool handleModemUrcLine(String& line) {
  line.trim();
  if (line.length() == 0) return false;

  if (directSmsPduExpected) {
    directSmsPduExpected = false;
    if (isHexString(line)) {
      // AT 命令等待响应期间不直接处理,暂存一条回主循环再处理;
      // 暂存位已占用时仍内联处理以免丢短信。
      if (modemCommandActive && pendingDirectSmsPdu.length() == 0) {
        pendingDirectSmsPdu = line;
      } else {
        processPduLine(line);
      }
      return true;
    }
  }

  if (line.startsWith("+CMTI:")) {
    int index = parseCmtiIndex(line);
    if (index > 0) enqueueStoredSmsIndex(index);
    return true;
  }

  if (line.startsWith("+CMT:")) {
    Serial.println("检测到+CMT直推,等待PDU数据...");
    directSmsPduExpected = true;
    return true;
  }

  return false;
}

void checkSerial1URC() {
  if (pendingDirectSmsPdu.length() > 0 && !modemCommandActive) {
    String pduLine = pendingDirectSmsPdu;
    pendingDirectSmsPdu = "";
    processPduLine(pduLine);
  }

  String line;
  while (readModemLine(line)) {
    if (line.length() == 0) continue;
    if (!handleModemUrcLine(line)) {
      Serial.println("Debug> " + line);
    }
  }

  processPendingStoredSms();
}

// ───────────────────────── Webhook(门铃 + IP 上报)─────────────────────────
// 手写裸 TCP POST:仅为一个 webhook 引入 HTTPClient 会把 TLS 栈整个链进固件(~200KB)
IPAddress webhookReportedIp;     // 最近一次成功上报给 hub 的本机 IP
String whHost = "";
uint16_t whPort = 80;
String whPath = "/";
bool whValid = false;

// ── Webhook 重试状态 ──
#define WH_RETRY_MAX 3          // 总尝试次数(含首次)
#define WH_RETRY_DELAYS_MS {0, 1000, 3000}  // 每次延迟(首次立即)
uint8_t whRetryCount = 0;      // 当前重试编号(0=首次)
unsigned long whRetryNextMs = 0;
String whRetryEvent = "";

// ── 心跳状态 ──
unsigned long lastHeartbeatMs = 0;
bool heartbeatForce = false;  // 连通/重连后立即补发一次心跳,跳过周期门控(见 maintainHeartbeat)

void parseWebhookUrl() {
  String url = String(WEBHOOK_URL);
  if (url.length() == 0) return;
  if (!url.startsWith("http://")) {
    Serial.println("WEBHOOK_URL 仅支持 http://,webhook 已禁用");
    return;
  }
  String rest = url.substring(7);
  int slash = rest.indexOf('/');
  String hostport = slash >= 0 ? rest.substring(0, slash) : rest;
  whPath = slash >= 0 ? rest.substring(slash) : "/";
  int colon = hostport.indexOf(':');
  if (colon >= 0) {
    whHost = hostport.substring(0, colon);
    whPort = (uint16_t)hostport.substring(colon + 1).toInt();
  } else {
    whHost = hostport;
    whPort = 80;
  }
  whValid = whHost.length() > 0 && whPort > 0;
}

// v2:WiFi.macAddress() 规范化为小写无分隔 12 位十六进制
String buildDeviceMac() {
  uint8_t m[6];
  WiFi.macAddress(m);
  char buf[13];
  for (int i = 0; i < 6; i++) sprintf(buf + i * 2, "%02x", m[i]);
  buf[12] = 0;
  return String(buf);
}

// v2:返回当前毫秒时间戳(设备本地,仅供 hub 乱序诊断,不参与排序)
uint64_t deviceTsMs() {
  return (uint64_t)(esp_timer_get_time() / 1000ULL);
}

// 返回 true 表示发送成功(2xx)
bool sendWebhook(const char* event) {
  if (!whValid || WiFi.status() != WL_CONNECTED) return false;
  webhookSeqId += 1;

  // 带上本机地址 + MAC/seq/时间戳:hub 以此学习设备地址并派生 sim_id
  String ip = WiFi.localIP().toString();
  String body = "{\"event\":\"" + String(event) +
                "\",\"seq_id\":" + String(webhookSeqId) +
                ",\"device_ts_ms\":" + String((unsigned long)deviceTsMs()) +
                ",\"mac\":\"" + deviceMac + "\"" +
                ",\"latest_id\":" + String(ringLatestId()) +
                ",\"ip\":\"" + ip + "\",\"port\":80,\"fw\":\"" FW_VERSION "\"}";

  return sendWebhookRaw(body);
}

// 底层发送:接受完整 JSON body
bool sendWebhookRaw(const String& body) {
  if (!whValid || WiFi.status() != WL_CONNECTED) return false;

  WiFiClient c;
  if (!c.connect(whHost.c_str(), whPort, WEBHOOK_TIMEOUT_MS)) {
    webhookFailTotal++;
    Serial.println("webhook 连接失败");
    return false;
  }
  c.print("POST " + whPath + " HTTP/1.1\r\n"
          "Host: " + whHost + "\r\n"
          "Content-Type: application/json\r\n"
          "Content-Length: " + String(body.length()) + "\r\n"
          "Connection: close\r\n\r\n" + body);

  // 只读状态行,够判断成败
  unsigned long deadline = millis() + WEBHOOK_TIMEOUT_MS;
  String statusLine = "";
  while (!timeReached(millis(), deadline) && (c.connected() || c.available())) {
    if (c.available()) {
      char ch = (char)c.read();
      if (ch == '\n') break;
      if (ch != '\r' && statusLine.length() < 64) statusLine += ch;
    } else {
      delay(2);
    }
  }
  c.stop();

  int sp = statusLine.indexOf(' ');
  int code = sp > 0 ? statusLine.substring(sp + 1).toInt() : 0;
  if (code >= 200 && code < 300) {
    webhookReportedIp = WiFi.localIP();
    return true;
  }
  webhookFailTotal++;
  Serial.printf("webhook 失败: %d\n", code);
  return false;
}

// 调度 webhook 重试(非阻塞),首次立即发,后续按递增延迟
void scheduleWebhookRetry(const char* event) {
  static const unsigned long delays[] = WH_RETRY_DELAYS_MS;
  whRetryEvent = String(event);
  whRetryCount = 0;
  whRetryNextMs = millis() + delays[0];  // delays[0]=0,即立即
}

// loop 中调用:检查是否到了重试时间
void maintainWebhookRetry() {
  if (whRetryCount >= WH_RETRY_MAX) return;  // 无待重试
  if (!timeReached(millis(), whRetryNextMs)) return;
  if (WiFi.status() != WL_CONNECTED) return;  // WiFi 断了等重连后再试

  static const unsigned long delays[] = WH_RETRY_DELAYS_MS;
  bool ok = sendWebhook(whRetryEvent.c_str());
  if (ok || whRetryCount + 1 >= WH_RETRY_MAX) {
    // 成功或已达最大次数,重置
    whRetryCount = WH_RETRY_MAX;
    return;
  }
  whRetryCount++;
  whRetryNextMs = millis() + delays[whRetryCount];
  Serial.printf("webhook(%s) 将在 %lums 后重试(%d/%d)\n",
                whRetryEvent.c_str(), delays[whRetryCount], whRetryCount + 1, WH_RETRY_MAX);
}

// 定时心跳:每 HEARTBEAT_INTERVAL_S 秒发送一次 webhook,携带完整设备状态
void maintainHeartbeat() {
#if HEARTBEAT_INTERVAL_S <= 0
  return;
#endif
  if (!whValid || WiFi.status() != WL_CONNECTED) return;  // 没连上就等下次,heartbeatForce 保留到连上为止
  unsigned long intervalMs = (unsigned long)HEARTBEAT_INTERVAL_S * 1000UL;
  if (!heartbeatForce && millis() - lastHeartbeatMs < intervalMs) return;
  heartbeatForce = false;
  lastHeartbeatMs = millis();

  refreshModemStatus(false);  // 确保状态数据新鲜
  webhookSeqId += 1;

  String ip = WiFi.localIP().toString();
  String body = "{";
  body.reserve(640);
  body += "\"event\":\"heartbeat\",";
  body += "\"mac\":\"" + deviceMac + "\",";
  appendf(body, "\"seq_id\":%lu,", (unsigned long)webhookSeqId);
  appendf(body, "\"device_ts_ms\":%lu,", (unsigned long)deviceTsMs());
  body += "\"ip\":\"" + ip + "\",\"port\":80,";
  body += "\"fw\":\"" FW_VERSION "\",";
  appendf(body, "\"latest_id\":%lu,", ringLatestId());
  appendf(body, "\"uptime_s\":%ld,", (long)(nowUs() / 1000000LL));
  appendf(body, "\"free_heap\":%lu,", ESP.getFreeHeap());
  appendf(body, "\"wifi_rssi\":%d,", WiFi.RSSI());
  body += "\"wifi_ssid\":\"" + jsonEscape(WiFi.SSID()) + "\",";
  body += "\"reset_reason\":\"" + jsonEscape(resetReasonText()) + "\",";
  body += "\"last_error\":\"" + jsonEscape(lastError) + "\",";
  // 模组状态
  body += "\"modem\":{";
  body += "\"ready\":" + boolJson(modemInitOk) + ",";
  body += "\"model\":\"" + jsonEscape(modemModel) + "\",";
  appendf(body, "\"cereg\":%d,", cachedCereg);
  appendf(body, "\"csq_dbm\":%d,", cachedCsqDbm);
  body += "\"operator\":\"" + jsonEscape(cachedOperator) + "\",";
  body += "\"sim\":" + boolJson(modemInitOk) + ",";
  body += "\"imsi\":\"" + jsonEscape(cachedImsi) + "\",";
  body += "\"imsi_tail\":\"" + jsonEscape(cachedImsiTail) + "\",";
  body += "\"msisdn\":\"" + jsonEscape(msisdn) + "\",";
  body += "\"iccid\":\"" + jsonEscape(cachedIccid) + "\",";
  body += "\"iccid_tail\":\"" + jsonEscape(cachedIccid.substring(cachedIccid.length() > 4 ? cachedIccid.length() - 4 : 0)) + "\",";
  body += "\"apn\":\"" + jsonEscape(cachedApn) + "\",";
  appendf(body, "\"flight_mode\":%d,", cachedCfun);
  body += "\"pdp_active\":" + boolJson(cachedPdpActive);
  body += "},";
  // 缓冲状态
  body += "\"buffer\":{";
  appendf(body, "\"count\":%d,", ringCount());
  appendf(body, "\"capacity\":%d,", SMS_BUFFER_SIZE);
  appendf(body, "\"latest_id\":%lu,", ringLatestId());
  appendf(body, "\"dropped_total\":%lu", droppedTotal);
  body += "},";
  // 计数器
  body += "\"counters\":{";
  appendf(body, "\"rx_total\":%lu,", rxTotal);
  appendf(body, "\"tx_total\":%lu,", txTotal);
  appendf(body, "\"webhook_fail_total\":%lu", webhookFailTotal);
  body += "}";
  body += "}";

  Serial.println("发送心跳...");
  sendWebhookRaw(body);  // 心跳失败不重试,下次周期会再发
}

// WiFi 重连拿到新 IP 后,主动向 hub 补报一次地址。
// hub 不在线时每次 POST 会阻塞到超时,所以失败后最快 30s 重试一次。
void maintainWebhookIpReport() {
  static unsigned long nextTryAt = 0;
  if (!whValid) return;
  if (WiFi.status() != WL_CONNECTED) return;
  IPAddress ip = WiFi.localIP();
  if (ip == webhookReportedIp) return;
  if (!timeReached(millis(), nextTryAt)) return;
  nextTryAt = millis() + 30000UL;
  Serial.printf("IP 变化,向 hub 上报: %s\n", ip.toString().c_str());
  sendWebhook("hello");
  // 重连拿到新 IP:hello 只补报地址,顺带强制发一次心跳,让 hub 状态立即恢复新鲜。
  heartbeatForce = true;
}

// ───────────────────────── HTTP API ─────────────────────────
// 辅助:将 snprintf 结果完整追加到 String。常见字段走栈缓冲,超长时按需分配,
// 避免数值位数增长后静默截断并破坏 JSON。
static void appendf(String& s, const char* fmt, ...) {
  char buf[48];
  va_list ap;
  va_start(ap, fmt);
  int needed = vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  if (needed < 0) return;
  if ((size_t)needed < sizeof(buf)) {
    s.concat(buf, (unsigned int)needed);
    return;
  }

  char* expanded = (char*)malloc((size_t)needed + 1);
  if (!expanded) {
    Serial.println("JSON 字段格式化失败:内存不足");
    return;
  }
  va_start(ap, fmt);
  vsnprintf(expanded, (size_t)needed + 1, fmt, ap);
  va_end(ap);
  s.concat(expanded, (unsigned int)needed);
  free(expanded);
}

void handleStatus() {
  refreshModemStatus(false);

  String json = "{";
  json.reserve(768);
  json += "\"fw\":\"" FW_VERSION "\",";
  appendf(json, "\"uptime_s\":%ld,", (long)(nowUs() / 1000000LL));
  json += "\"reset_reason\":\"" + jsonEscape(resetReasonText()) + "\",";
  appendf(json, "\"free_heap\":%lu,", ESP.getFreeHeap());
  appendf(json, "\"min_free_heap\":%lu,", ESP.getMinFreeHeap());
  appendf(json, "\"wifi_rssi\":%d,", WiFi.RSSI());
  json += "\"wifi_ssid\":\"" + jsonEscape(WiFi.SSID()) + "\",";
  json += "\"ip\":\"" + jsonEscape(WiFi.localIP().toString()) + "\",";
  json += "\"mdns\":" + boolJson(mdnsStarted) + ",";

  json += "\"modem\":{";
  json += "\"ready\":" + boolJson(modemInitOk) + ",";
  json += "\"model\":\"" + jsonEscape(modemModel) + "\",";
  appendf(json, "\"cereg\":%d,", cachedCereg);
  appendf(json, "\"csq_dbm\":%d,", cachedCsqDbm);
  json += "\"operator\":\"" + jsonEscape(cachedOperator) + "\",";
  json += "\"sim\":" + boolJson(modemInitOk) + ",";
  json += "\"imsi\":\"" + jsonEscape(cachedImsi) + "\",";
  json += "\"imsi_tail\":\"" + jsonEscape(cachedImsiTail) + "\",";
  json += "\"msisdn\":\"" + jsonEscape(msisdn) + "\",";
  json += "\"iccid\":\"" + jsonEscape(cachedIccid) + "\",";
  json += "\"apn\":\"" + jsonEscape(cachedApn) + "\",";
  appendf(json, "\"flight_mode\":%d,", cachedCfun);
  json += "\"pdp_active\":" + boolJson(cachedPdpActive);
  json += "},";

  json += "\"buffer\":{";
  appendf(json, "\"oldest_id\":%lu,", ringOldestId());
  appendf(json, "\"latest_id\":%lu,", ringLatestId());
  appendf(json, "\"count\":%d,", ringCount());
  appendf(json, "\"capacity\":%d,", SMS_BUFFER_SIZE);
  appendf(json, "\"last_pulled_id\":%lu,", lastPulledId);
  appendf(json, "\"dropped_total\":%lu", droppedTotal);
  json += "},";

  json += "\"counters\":{";
  appendf(json, "\"rx_total\":%lu,", rxTotal);
  appendf(json, "\"tx_total\":%lu,", txTotal);
  appendf(json, "\"webhook_fail_total\":%lu", webhookFailTotal);
  json += "},";

  json += "\"last_error\":\"" + jsonEscape(lastError) + "\"";
  json += "}";
  server.send(200, "application/json", json);
}

void handlePull() {
  uint32_t after = 0;
  int limit = 20;
  bool includeStatus = false;
  if (server.hasArg("after")) {
    long v = server.arg("after").toInt();
    if (v > 0) after = (uint32_t)v;
  }
  if (server.hasArg("limit")) {
    limit = server.arg("limit").toInt();
    if (limit <= 0) limit = 20;
    if (limit > SMS_BUFFER_SIZE) limit = SMS_BUFFER_SIZE;
  }
  if (server.hasArg("include_status")) {
    includeStatus = server.arg("include_status").toInt() != 0;
  }

  // 从 hub 的游标学习已拉取位置(只前进),但不超过设备当前高水位。
  // 这样错误客户端传入过大的 after 不会破坏背压判断;Hub 仍能通过
  // latest_id < cursor 的响应检测设备编号回退并自愈。
  uint32_t effectiveAfter = after;
  uint32_t latestId = ringLatestId();
  if (effectiveAfter > latestId) effectiveAfter = latestId;
  if (effectiveAfter > lastPulledId) lastPulledId = effectiveAfter;

  String json = "{";
  json.reserve(2048);
  json += "\"mac\":\"" + deviceMac + "\",\"buffer\":{";
  appendf(json, "\"oldest_id\":%lu,", ringOldestId());
  appendf(json, "\"latest_id\":%lu,", latestId);
  appendf(json, "\"count\":%d,", ringCount());
  appendf(json, "\"capacity\":%d,", SMS_BUFFER_SIZE);
  appendf(json, "\"dropped_total\":%lu", droppedTotal);
  json += "},\"messages\":[";

  int emitted = 0;
  int64_t now = nowUs();
  // 从最旧往最新走,天然升序
  for (int n = 0; n < SMS_BUFFER_SIZE && emitted < limit; n++) {
    int idx = (ringNext + n) % SMS_BUFFER_SIZE;
    SmsRecord& rec = smsRing[idx];
    if (!rec.valid || rec.id <= effectiveAfter) continue;

    if (emitted > 0) json += ",";
    long ageS = rec.receivedUs >= 0 ? (long)((now - rec.receivedUs) / 1000000LL) : -1;
    json += "{\"device_msg_id\":" + String(rec.id) + ",";
    json += "\"from\":\"" + jsonEscape(rec.sender) + "\",";
    json += "\"scts\":\"" + jsonEscape(rec.scts) + "\",";
    appendf(json, "\"age_s\":%ld,", ageS);
    json += "\"text\":\"" + jsonEscape(rec.text) + "\",";
    json += "\"complete\":" + boolJson(rec.complete) + ",";
    json += "\"truncated\":" + boolJson(rec.truncated);
    if (rec.isMms) json += ",\"mms\":1";
    json += "}";
    emitted++;
  }

  json += "]";
  // 可选状态块:手动刷新 / 首拉解析 sim_id 时用(include_status=1)
  if (includeStatus) {
    refreshModemStatus(false);
    String iccidTail = cachedIccid.length() > 4
                           ? cachedIccid.substring(cachedIccid.length() - 4)
                           : cachedIccid;
    json += ",\"status\":{\"modem\":{";
    json += "\"ready\":" + boolJson(modemInitOk) + ",";
    json += "\"imsi\":\"" + jsonEscape(cachedImsi) + "\",";
    json += "\"imsi_tail\":\"" + jsonEscape(cachedImsiTail) + "\",";
    json += "\"msisdn\":\"" + jsonEscape(msisdn) + "\",";
    json += "\"iccid\":\"" + jsonEscape(cachedIccid) + "\",";
    json += "\"iccid_tail\":\"" + jsonEscape(iccidTail) + "\",";
    json += "\"operator\":\"" + jsonEscape(cachedOperator) + "\"}}";
  } else {
    json += ",\"status\":null";
  }
  json += "}";
  server.send(200, "application/json", json);
}

void handleSend() {
  String body = server.arg("plain");
  String to = extractJsonString(body, "to");
  String text = extractJsonString(body, "text");
  to.trim();

  String err = "";
  if (to.length() == 0) err = "缺少 to 字段";
  else if (text.length() == 0) err = "缺少 text 字段";
  else err = validateOutgoingSms(to, text);

  if (err.length() > 0) {
    server.send(200, "application/json",
                "{\"ok\":false,\"error\":\"" + jsonEscape(err) + "\",\"device_msg_id\":0}");
    return;
  }

  int parts = ucs2UnitCount(text) <= SMS_SINGLE_PART_CHAR_LIMIT
                  ? 1
                  : countSmsChunks(text, SMS_SPLIT_PART_CHAR_LIMIT);
  Serial.println("API 发送短信 → " + to);
  // 持久化:多段发送中途重启可恢复
  int outSlot = (parts > 1) ? persistOutbound(to.c_str(), text.c_str()) : -1;
  bool ok = sendSMS(to.c_str(), text.c_str());
  if (ok) {
    txTotal++;
    persistTxTotal();
    if (outSlot >= 0) clearOutboundNvs(outSlot);
    server.send(200, "application/json",
                "{\"ok\":true,\"parts\":" + String(parts) + ",\"device_msg_id\":" + String(txTotal) + "}");
  } else {
    recordLastError("短信发送失败");
    server.send(200, "application/json",
                "{\"ok\":false,\"error\":\"发送失败,请检查模组状态\",\"device_msg_id\":0}");
  }
}

void handleAt() {
  String body = server.arg("plain");
  String cmd = extractJsonString(body, "cmd");
  long timeoutMs = extractJsonNumber(body, "timeout_ms", 3000);
  cmd.trim();
  if (timeoutMs < 100) timeoutMs = 100;
  if (timeoutMs > AT_PROXY_MAX_TIMEOUT_MS) timeoutMs = AT_PROXY_MAX_TIMEOUT_MS;

  if (cmd.length() == 0) {
    server.send(200, "application/json", "{\"ok\":false,\"response\":\"缺少 cmd 字段\"}");
    return;
  }

  Serial.println("API 透传 AT: " + cmd);
  String resp = sendATCommand(cmd.c_str(), (unsigned long)timeoutMs);
  bool ok = resp.length() > 0;
  server.send(200, "application/json",
              "{\"ok\":" + boolJson(ok) + ",\"response\":\"" +
                  jsonEscape(ok ? resp : "TIMEOUT") + "\"}");

  // 飞行模式 / 数据承载 被 UI 主动切换后,立即更新缓存并强制补发心跳,
  // 下次 /api/status 马上反映新值,不必等下个心跳周期(可能是几百秒)。
  if (ok) {
    String up = cmd;
    up.toUpperCase();
    if (up.startsWith("AT+CFUN=")) {
      long v = up.substring(8).toInt();
      if (v >= 0) { cachedCfun = (int)v; heartbeatForce = true; }
    } else if (up.startsWith("AT+CGACT=")) {
      cachedPdpActive = up.substring(9).startsWith("1");
      heartbeatForce = true;
    }
  }
}

// ── 批量删除(/{token}/delete)──
// v2:body {"device_msg_ids":[...]}。逐条移出环形缓冲并清 NVS 镜像,
// hub 删除时同步调用做到"前端删除即设备删除"。id 不在缓冲(已溢出)时
// found=false,视为已删除(幂等)。
int parseDeviceMsgIds(const String& body, uint32_t* ids, int maxN) {
  int k = body.indexOf("\"device_msg_ids\"");
  if (k < 0) return 0;
  int lb = body.indexOf('[', k);
  int rb = body.indexOf(']', lb < 0 ? k : lb);
  if (lb < 0 || rb < 0) return 0;
  int n = 0, i = lb + 1;
  while (i < rb && n < maxN) {
    while (i < rb && (body[i] == ' ' || body[i] == ',')) i++;
    String num;
    while (i < rb && body[i] >= '0' && body[i] <= '9') { num += body[i]; i++; }
    if (num.length()) ids[n++] = (uint32_t)num.toInt();
    else break;
  }
  return n;
}

void handleDelete() {
  String body = server.arg("plain");
  uint32_t ids[SMS_BUFFER_SIZE];
  int n = parseDeviceMsgIds(body, ids, SMS_BUFFER_SIZE);
  if (n == 0) {
    server.send(200, "application/json",
                "{\"ok\":false,\"error\":\"缺少 device_msg_ids\"}");
    return;
  }
  preferences.begin("v2", false);
  String json = "{\"ok\":true,\"deleted\":[";
  for (int i = 0; i < n; i++) {
    uint32_t id = ids[i];
    bool found = false;
    for (int j = 0; j < SMS_BUFFER_SIZE; j++) {
      if (smsRing[j].valid && smsRing[j].id == id) {
        smsRing[j].valid = false;
        found = true;
        break;
      }
    }
    preferences.remove(nvsMsgKey(id));  // key 不存在(超出镜像窗口)时 remove 是空操作
    if (i > 0) json += ",";
    json += "{\"device_msg_id\":" + String(id) + ",\"found\":" + boolJson(found) + "}";
  }
  preferences.end();
  json += "]}";
  Serial.printf("API 批量删除 %d 条\n", n);
  server.send(200, "application/json", json);
}

// ── OTA(/{token}/update)──
void handleUpdatePage() {
  const char page[] PROGMEM = R"rawliteral(
<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>固件升级</title><style>
body{margin:0;background:#f6f7f9;color:#20242c;font-family:system-ui,"Microsoft YaHei",sans-serif}.wrap{max-width:560px;margin:0 auto;padding:18px}.panel{border:1px solid #d8dee8;border-radius:8px;background:#fff;padding:16px}h1{font-size:22px}input,button{width:100%;min-height:42px;margin-top:10px}button{border:0;border-radius:7px;background:#2563eb;color:#fff;font-weight:700}.note{color:#5f6b7a;font-size:13px;line-height:1.5}
</style></head><body><div class="wrap"><h1>固件升级</h1><div class="panel"><form method="POST" enctype="multipart/form-data"><input type="file" name="firmware" accept=".bin" required><button type="submit">上传并升级</button></form><p class="note">上传完成后设备会自动重启。</p></div></div></body></html>
)rawliteral";
  server.send_P(200, "text/html", page);
}

void handleUpdateUpload() {
  HTTPUpload& upload = server.upload();
  if (upload.status == UPLOAD_FILE_START) {
    Serial.printf("开始OTA升级: %s\n", upload.filename.c_str());
    if (!Update.begin(UPDATE_SIZE_UNKNOWN)) {
      Update.printError(Serial);
    }
  } else if (upload.status == UPLOAD_FILE_WRITE) {
    if (Update.write(upload.buf, upload.currentSize) != upload.currentSize) {
      Update.printError(Serial);
    }
  } else if (upload.status == UPLOAD_FILE_END) {
    if (Update.end(true)) {
      Serial.printf("OTA升级完成: %u bytes\n", upload.totalSize);
    } else {
      Update.printError(Serial);
    }
  }
}

void setupHttpServer() {
  if (httpServerStarted) return;

  String base = "/" + String(API_TOKEN);
  server.on(base + "/status", HTTP_GET, handleStatus);
  server.on(base + "/pull", HTTP_GET, handlePull);
  server.on(base + "/send", HTTP_POST, handleSend);
  server.on(base + "/at", HTTP_POST, handleAt);
  server.on(base + "/delete", HTTP_POST, handleDelete);
  server.on(base + "/update", HTTP_GET, handleUpdatePage);
  server.on(base + "/update", HTTP_POST, []() {
    bool ok = !Update.hasError();
    server.send(200, "text/plain", ok ? "升级完成,设备即将重启" : "升级失败");
    if (ok) {
      delay(500);
      ESP.restart();
    }
  }, handleUpdateUpload);

  // token 不对一律 404 空响应,不暴露任何信息
  server.onNotFound([]() {
    server.send(404, "text/plain", "");
  });

  server.begin();
  httpServerStarted = true;
  Serial.println("HTTP服务器已启动");
}

// ───────────────────────── WiFi / mDNS ─────────────────────────
bool waitWiFiConnected(unsigned long timeoutMs) {
  unsigned long start = millis();
  while (millis() - start < timeoutMs) {
    if (WiFi.status() == WL_CONNECTED) return true;
    blink_short();
  }
  Serial.println("WiFi连接超时,继续启动(后台持续重连)");
  return false;
}

void maintainWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastWiFiReconnectTime < 10000) return;

  lastWiFiReconnectTime = millis();
  Serial.println("WiFi未连接,重试连接");
  WiFi.disconnect(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS, 0, nullptr, true);
}

void startMdnsIfPossible() {
  if (mdnsStarted || WiFi.status() != WL_CONNECTED) return;
  if (MDNS.begin("sms")) {
    mdnsStarted = true;
    MDNS.addService("http", "tcp", 80);
    Serial.println("mDNS已启动: http://sms.local/");
  }
}

void queryModemModel() {
  String resp = sendATCommand("ATI", 2000);
  int start = 0;
  int lineNum = 0;
  while (start <= (int)resp.length()) {
    int end = resp.indexOf('\n', start);
    if (end < 0) end = resp.length();
    String line = resp.substring(start, end);
    line.trim();
    if (line.length() > 0 && line != "ATI" && line != "OK" && !line.startsWith("Revision")) {
      lineNum++;
      if (lineNum == 2) {  // 第1行制造商,第2行型号
        modemModel = line;
        break;
      }
      if (modemModel.length() == 0) modemModel = line;
    }
    start = end + 1;
  }
}

// 查询本机号码:config.h 的 MY_MSISDN 优先;留空则 AT+CNUM 兜底(SIM 未烧录 MSISDN 时只回 OK)。
// +CNUM 响应形如 +CNUM: "","13800138000",129 —— 取引号内纯数字串。
void queryMsisdn() {
  if (strlen(MY_MSISDN) > 0) { msisdn = String(MY_MSISDN); return; }
  String resp = sendATCommand("AT+CNUM", 2000);
  int from = resp.indexOf("+CNUM:");
  if (from < 0) return;
  while (from < (int)resp.length()) {
    int q1 = resp.indexOf('"', from);
    if (q1 < 0) break;
    int q2 = resp.indexOf('"', q1 + 1);
    if (q2 < 0) break;
    String field = resp.substring(q1 + 1, q2);
    bool isNum = field.length() >= 5;
    for (unsigned int i = 0; i < field.length() && isNum; i++) {
      char c = field.charAt(i);
      if (i == 0 && c == '+') continue;
      if (c < '0' || c > '9') isNum = false;
    }
    if (isNum) { msisdn = field; return; }
    from = q2 + 1;
  }
}

// 取响应里最长的一串连续数字(解析 IMSI 这种模块格式不一的字段)
static String _longestDigits(const String& s, int minLen) {
  String digits, best;
  for (unsigned int i = 0; i <= s.length(); i++) {
    char c = i < s.length() ? s.charAt(i) : ' ';
    if (c >= '0' && c <= '9') digits += c;
    else { if ((int)digits.length() > (int)best.length()) best = digits; digits = ""; }
  }
  return (int)best.length() >= minLen ? best : String("");
}

// 取响应里最长的一串十六进制标识字符。部分 ML307A 的 AT+ICCID 会返回
// 898600A...F... 这类含 A/F 的字符串,不能按纯数字截断。
static String _longestHexToken(const String& s, int minLen) {
  String token, best;
  for (unsigned int i = 0; i <= s.length(); i++) {
    char c = i < s.length() ? s.charAt(i) : ' ';
    bool isDigit = c >= '0' && c <= '9';
    bool isUpperHex = c >= 'A' && c <= 'F';
    bool isLowerHex = c >= 'a' && c <= 'f';
    if (isDigit || isUpperHex || isLowerHex) {
      token += isLowerHex ? (char)(c - 'a' + 'A') : c;
    } else {
      if ((int)token.length() > (int)best.length()) best = token;
      token = "";
    }
  }
  return (int)best.length() >= minLen ? best : String("");
}

// ICCID/IMSI/APN 极少变化,开机查一次缓存即可,随 heartbeat 上报给 hub(字段名对齐 mock)。
void queryModemIdentity() {
  // ICCID 各模块命令不一:先 AT+ICCID,空则退回 AT+CCID / AT+QCCID,避免读不到导致设置页空白
  cachedIccid = _longestHexToken(sendATCommand("AT+ICCID", 2000), 10);
  if (cachedIccid.length() == 0) cachedIccid = _longestHexToken(sendATCommand("AT+CCID", 2000), 10);
  if (cachedIccid.length() == 0) cachedIccid = _longestHexToken(sendATCommand("AT+QCCID", 2000), 10);
  String imsi = _longestDigits(sendATCommand("AT+CIMI", 2000), 10);
  cachedImsi = imsi;  // v2:完整 IMSI 仅上报 Hub 派生 sim_id,不展示/不入 NVS
  cachedImsiTail = imsi.length() > 4 ? imsi.substring(imsi.length() - 4) : imsi;

  String resp = sendATCommand("AT+CGDCONT?", 2000);
  int idx = resp.indexOf("+CGDCONT:");
  if (idx >= 0) {
    // +CGDCONT: 1,"IP","apn",... — 取第 3 个引号字段
    int q1 = resp.indexOf('"', idx);
    int q2 = q1 >= 0 ? resp.indexOf('"', q1 + 1) : -1;
    int q3 = q2 >= 0 ? resp.indexOf('"', q2 + 1) : -1;
    int q4 = q3 >= 0 ? resp.indexOf('"', q3 + 1) : -1;
    if (q3 >= 0 && q4 > q3) cachedApn = resp.substring(q3 + 1, q4);
  }
}

// ───────────────────────── setup / loop ─────────────────────────
void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);

  Serial.begin(115200);
  delay(1500);  // 等 USB CDC 稳定
  Serial.println("V2 瘦网关启动 fw=" FW_VERSION);

  Serial1.setRxBufferSize(SERIAL_RX_BUFFER_SIZE);
  Serial1.begin(115200, SERIAL_8N1, RXD, TXD);

  initConcatBuffer();
  loadCounters();
  restoreMessagesFromNvs();
  parseWebhookUrl();

  if (String(API_TOKEN).length() < 16) {
    Serial.println("警告: API_TOKEN 过短,建议至少 16 字符随机串");
  }

  // 扫描所有信道以连接信号最强的 AP
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // 常供电设备不省这点电:关掉省电模式,降低 hub 拉取/webhook 延迟
  WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
  deviceMac = buildDeviceMac();  // v2:物理链路标识,webhook/pull/status 均上报
  Serial.println("设备MAC: " + deviceMac);
  WiFi.begin(WIFI_SSID, WIFI_PASS, 0, nullptr, true);
  Serial.println("连接WiFi: " WIFI_SSID);
  if (waitWiFiConnected(WIFI_CONNECT_WAIT_MS)) {
    Serial.print("WiFi已连接,IP: ");
    Serial.println(WiFi.localIP());
  } else {
    recordLastError("WiFi连接超时");
  }

  setupHttpServer();
  startMdnsIfPossible();

  // 模组初始化放在 HTTP 服务之后,模组异常时 API 仍可访问
  modemPowerCycle();

  bool atOk = retryATStep("AT", 1000, "AT未响应,重试...", "模组AT响应正常");
  bool cgactOk = retryATStep("AT+CGACT=0,1", 5000, "设置CGACT失败,重试...", "已禁用数据连接");
  bool pduModeOk = retryATStep("AT+CMGF=0", 1000, "设置PDU模式失败,重试...", "PDU模式设置完成");
  bool storageOk = retryATStep("AT+CPMS=\"SM\",\"SM\",\"SM\"", 2000, "设置短信存储失败,重试...", "短信存储设置完成");
  bool cnmiOk = retryATStep("AT+CNMI=2,1,0,0,0", 1000, "设置CNMI失败,重试...", "CNMI存储通知模式设置完成");
  modemInitOk = atOk && cgactOk && pduModeOk && storageOk && cnmiOk;
  modemInitDone = true;
  if (!modemInitOk) recordLastError("模组初始化未完全成功");

  queryModemModel();
  queryMsisdn();
  queryModemIdentity();
  enqueueStoredSmsFromList();
  networkRegistered = waitNetworkRegistered(MODEM_NETWORK_WAIT_MS);
  if (!networkRegistered) recordLastError("蜂窝网络注册超时");

  digitalWrite(LED_BUILTIN, LOW);

  // OTA 安全网:标记当前固件有效,取消回滚。如果新固件 crash loop,
  // ESP32 bootloader 会在超时后自动回滚到上一版。
  esp_ota_mark_app_valid_cancel_rollback();

  Serial.println("启动完成,API: http://" + WiFi.localIP().toString() + "/" + String(API_TOKEN) + "/");

  // 恢复上次重启前未完成的出站消息
  restoreOutboundFromNvs();

  scheduleWebhookRetry("boot");
  // 刚连上 hub:boot 只是门铃(不带状态),立刻补发一次心跳,让 hub 第一时间拿到
  // 完整设备状态,避免新架构(不再主动 GET /status)下首屏状态卡片空数据。
  heartbeatForce = true;
}

void loop() {
  maintainWiFi();
  startMdnsIfPossible();
  maintainWebhookIpReport();
  maintainWebhookRetry();
  maintainHeartbeat();
  server.handleClient();
  checkConcatTimeout();

  // USB 串口透传到模组,方便调试
  if (Serial.available()) Serial1.write(Serial.read());

  checkSerial1URC();
}
