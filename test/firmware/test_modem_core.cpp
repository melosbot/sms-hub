#include "modem_line_reader.h"
#include "modem_policy.h"
#include "modem_utils.h"

#include <assert.h>
#include <string.h>

int main() {
  // ── modem_line_reader: 串口行解析 ──
  ModemLineReader reader;
  const char* response = "+CMTI: \"SM\",7\r\n";
  ModemLineResult result = ModemLineResult::None;
  for (const char* cursor = response; *cursor != '\0'; cursor++) {
    result = reader.onByte(*cursor);
  }
  assert(result == ModemLineResult::Line);
  assert(strcmp(reader.line(), "+CMTI: \"SM\",7") == 0);

  for (size_t i = 0; i < ModemLineReader::kCapacity + 20; i++) {
    assert(reader.onByte('A') == ModemLineResult::None);
  }
  assert(reader.onByte('\n') == ModemLineResult::Dropped);
  assert(reader.onByte('O') == ModemLineResult::None);
  assert(reader.onByte('K') == ModemLineResult::None);
  assert(reader.onByte('\n') == ModemLineResult::Line);
  assert(strcmp(reader.line(), "OK") == 0);

  // ── modem_policy: AT 响应解析 ──
  assert(modem_policy::hasOkLine("AT+CNMI?\r\n+CNMI: 2,1,0,0,0\r\nOK\r\n"));
  assert(!modem_policy::hasOkLine("BROKEN_OK"));
  assert(modem_policy::cnmiUsesStorageNotifications("+CNMI: 2, 1, 0, 0, 0\r\nOK\r\n"));
  assert(!modem_policy::cnmiUsesStorageNotifications("+CNMI: 2,2,0,0,0\r\nOK\r\n"));

  bool active = false;
  assert(modem_policy::parseMipCallActive("+MIPCALL: 1,1,\"10.0.0.2\"\r\nOK\r\n", active));
  assert(active);
  assert(modem_policy::parseMipCallActive("OK\r\n", active));
  assert(!active);
  assert(!modem_policy::parseMipCallActive("+MIPCALL: 1,9\r\nOK\r\n", active));
  assert(!modem_policy::parseMipCallActive("+MIPCALL: 1,1x\r\nOK\r\n", active));
  assert(!modem_policy::parseMipCallActive("+MIPCALL: 1,1\r\n", active));

  // ── modem_utils: timeReached(millis() 回绕安全) ──
  assert(modem_utils::timeReached(100, 50));     // now > deadline
  assert(modem_utils::timeReached(50, 50));      // now == deadline
  assert(!modem_utils::timeReached(50, 100));    // now < deadline
  // 49 天回绕: deadline 在 UINT32_MAX, now 回绕到 0 附近
  assert(modem_utils::timeReached(100, UINT32_MAX - 50));  // deadline 在 UINT32_MAX 附近, now 回绕
  assert(!modem_utils::timeReached(100, 200));   // deadline 在 now 之后

  // ── modem_utils: retryDelayForAttempt(指数退避) ──
  unsigned long baseMs = 15000UL;
  unsigned long maxMs = 900000UL;
  assert(modem_utils::retryDelayForAttempt(0, baseMs, maxMs) == 15000UL);
  assert(modem_utils::retryDelayForAttempt(1, baseMs, maxMs) == 30000UL);
  assert(modem_utils::retryDelayForAttempt(2, baseMs, maxMs) == 60000UL);
  assert(modem_utils::retryDelayForAttempt(10, baseMs, maxMs) == maxMs);
  // 自定义参数
  assert(modem_utils::retryDelayForAttempt(0, 1000UL, 10000UL) == 1000UL);
  assert(modem_utils::retryDelayForAttempt(5, 1000UL, 10000UL) == 10000UL);

  // ── modem_utils: findFreeSlot ──
  {
    bool inUse[4] = {false, false, true, false};
    assert(modem_utils::findFreeSlot(inUse) == 0);
    inUse[0] = true;
    assert(modem_utils::findFreeSlot(inUse) == 1);
    inUse[1] = true;
    assert(modem_utils::findFreeSlot(inUse) == 3);
    inUse[3] = true;
    assert(modem_utils::findFreeSlot(inUse) == -1);  // 满
  }

  // ── modem_utils: findSlotByKey ──
  {
    bool inUse[4] = {true, false, true, true};
    int keys[4] = {10, 0, 20, 30};
    assert(modem_utils::findSlotByKey(inUse, keys, 10) == 0);
    assert(modem_utils::findSlotByKey(inUse, keys, 20) == 2);
    assert(modem_utils::findSlotByKey(inUse, keys, 30) == 3);
    assert(modem_utils::findSlotByKey(inUse, keys, 0) == -1);    // 槽位空闲
    assert(modem_utils::findSlotByKey(inUse, keys, 99) == -1);   // key 不存在
  }

  return 0;
}
