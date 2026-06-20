#pragma once
// 无 Arduino 依赖的纯逻辑函数:时间比较、指数退避、队列查找。
// 可在宿主机 g++ 上直接编译测试,不依赖 Arduino/ESP32 SDK。
// 固件(.ino)通过包含本头文件复用这些函数。
#include <stdint.h>

namespace modem_utils {

// ── 时间比较(millis() 回绕安全) ──
// 当 `deadline` 在过去(millis()已回绕)时,now 视为已到达。
// 前提:now 与 deadline 的间隔不超过 49 天(2^31 ms)。
// 使用 uint32_t 保证 32 位算术,与 ESP32 `millis()` 一致。
inline bool timeReached(uint32_t now, uint32_t deadline) {
  return (int32_t)(now - deadline) >= 0;
}

// ── 指数退避延时 ──
// attempts: 当前失败次数(从 0 开始);baseMs: 基础延时(ms);maxMs: 最大延时(ms)。
inline uint32_t retryDelayForAttempt(uint8_t attempts,
                                     uint32_t baseMs,
                                     uint32_t maxMs) {
  uint32_t delayMs = baseMs;
  for (uint8_t i = 0; i < attempts && delayMs < maxMs; i++) {
    delayMs *= 2;
    if (delayMs > maxMs) {
      delayMs = maxMs;
      break;
    }
  }
  return delayMs;
}

// ── 固定容量队列:查找空闲槽位 ──
// `inUse` 数组长度 `capacity`;返回首个 `!inUse[i]` 的索引,满时返回 -1。
template <size_t Capacity>
inline int findFreeSlot(const bool (&inUse)[Capacity]) {
  for (size_t i = 0; i < Capacity; i++) {
    if (!inUse[i]) return static_cast<int>(i);
  }
  return -1;
}

// ── 固定容量队列:按整数键查找槽位 ──
// 返回首个 `inUse[i] && keys[i] == key` 的索引,未找到返回 -1。
template <size_t Capacity>
inline int findSlotByKey(const bool (&inUse)[Capacity],
                         const int (&keys)[Capacity],
                         int key) {
  for (size_t i = 0; i < Capacity; i++) {
    if (inUse[i] && keys[i] == key) return static_cast<int>(i);
  }
  return -1;
}

}  // namespace modem_utils
