#pragma once
// 长短信(concatenated SMS)合并状态机 — 无 Arduino 依赖的纯逻辑。
// 可在宿主机用 g++ 测试,不依赖 String/Serial/millis()。
//
// 设计:
//  - 固定容量槽位池(MAX_CONCAT_SLOTS)，每个槽位可容纳最多 MAX_CONCAT_PARTS 个分段。
//  - 匹配键=(refNumber, senderHash, totalParts)，防参考号碰撞误合并(§1.1)。
//  - 槽位满时淘汰最老的未完成组(先以不完整内容落盘)。
//  - 超时未收齐的组以不完整内容提交。
//  - 存储索引生命周期跟踪:已收齐或已超时落盘的索引可安全删除,未完索引受保护。
#include <stdint.h>
#include <stddef.h>

namespace modem_concat {

// ── 编译期常量 ──
constexpr uint8_t kMaxConcatParts = 10;
constexpr uint8_t kMaxConcatSlots = 5;
constexpr uint32_t kTimeoutMs = 30000;

// ── 分段数据 ──
struct Part {
  bool valid = false;
  int storageIndex = 0;  // 对应的模组存储索引,0=无
};

// ── 槽位状态 ──
struct Slot {
  bool inUse = false;
  int refNumber = 0;
  uint32_t senderHash = 0;  // hash(sender) 用于匹配,不存原文
  uint8_t totalParts = 0;
  uint8_t receivedParts = 0;
  uint32_t firstPartTime = 0;  // 首段到达时间(注入的时钟值)
  Part parts[kMaxConcatParts];
};

// ── 操作结果 ──
enum class ConcatAction : uint8_t {
  None = 0,
  StorePart,        // 存入分段,等待更多分段
  Complete,         // 全部分段收齐,提交完整内容
  FinalizePartial,  // 超时或淘汰,以不完整内容提交
};

// ── 操作上下文(供调用方在收到操作后执行) ──
struct ConcatResult {
  ConcatAction action = ConcatAction::None;
  int slot = -1;                   // 操作的槽位
  uint8_t totalParts = 0;          // 所有分段总数
  uint8_t receivedParts = 0;       // 已收分段数
  // 完整/不完整时:哪些存储索引可安全删除
  int deletableIndices[kMaxConcatParts];
  uint8_t deletableCount = 0;
  // 槽位淘汰:分配新槽位时淘汰了最老的未完成组
  bool evicted = false;
  uint8_t evictedTotalParts = 0;
  uint8_t evictedReceivedParts = 0;
  int evictedDeletableIndices[kMaxConcatParts];
  uint8_t evictedDeletableCount = 0;
};

// ── 状态机 ──
class ConcatState {
public:
  void reset() {
    for (auto& s : slots_) s = Slot{};
  }

  // 收到一个分段。调用方注入当前时间 nowMs。
  // 返回操作建议,调用方据此执行后续(存储/组装/删除/清理)。
  ConcatResult onPart(int refNumber, uint32_t senderHash,
                      uint8_t totalParts, uint8_t seqNumber,
                      int storageIndex, uint32_t nowMs) {
    ConcatResult out;
    if (totalParts < 1 || totalParts > kMaxConcatParts) return out;
    if (seqNumber < 1 || seqNumber > totalParts) return out;

    int slot = findOrCreateSlot(refNumber, senderHash, totalParts, nowMs, out);
    if (slot < 0) return out;

    Slot& s = slots_[slot];
    uint8_t idx = seqNumber - 1;  // seq 从 1 开始
    if (!s.parts[idx].valid) {
      s.parts[idx].valid = true;
      s.parts[idx].storageIndex = storageIndex;
      s.receivedParts++;
    }
    // 重复分段:幂等,不重复计数

    out.slot = slot;
    out.totalParts = totalParts;
    out.receivedParts = s.receivedParts;

    if (s.receivedParts >= s.totalParts) {
      out.action = ConcatAction::Complete;
      collectDeletable(slot, out);
      clearSlot(slot);
    } else {
      out.action = ConcatAction::StorePart;
    }
    return out;
  }

  // 检查超时。调用方周期性调用(如每次主循环),传入 nowMs。
  // maxDeletablePerCall 限制每次可删除的索引数(调用方可控制 I/O 压力)。
  ConcatResult checkTimeout(uint32_t nowMs, bool ringWouldDrop = false) {
    ConcatResult out;
    for (int i = 0; i < kMaxConcatSlots; i++) {
      if (!slots_[i].inUse) continue;
      if ((nowMs - slots_[i].firstPartTime) < kTimeoutMs) continue;
      // 缓冲有背压风险时暂不删除恢复源
      if (ringWouldDrop) continue;
      collectDeletable(i, out);
      out.action = ConcatAction::FinalizePartial;
      out.slot = i;
      out.totalParts = slots_[i].totalParts;
      out.receivedParts = slots_[i].receivedParts;
      clearSlot(i);
      return out;  // 每次最多处理一个超时
    }
    return out;
  }

  // 查询某存储索引是否仍被未完成组引用(受保护,不可删除)
  bool isStorageIndexProtected(int index) const {
    if (index <= 0) return false;
    for (int i = 0; i < kMaxConcatSlots; i++) {
      if (!slots_[i].inUse) continue;
      for (int j = 0; j < kMaxConcatParts; j++) {
        if (slots_[i].parts[j].valid &&
            slots_[i].parts[j].storageIndex == index)
          return true;
      }
    }
    return false;
  }

  // 诊断
  uint8_t activeSlots() const {
    uint8_t n = 0;
    for (int i = 0; i < kMaxConcatSlots; i++)
      if (slots_[i].inUse) n++;
    return n;
  }

  const Slot& slot(int i) const { return slots_[i]; }

private:
  Slot slots_[kMaxConcatSlots];

  static uint32_t hashSender(const char* s) {
    // FNV-1a 32-bit (轻量,零依赖)
    uint32_t h = 2166136261u;
    if (!s) return h;
    for (; *s; s++) {
      h ^= (uint8_t)*s;
      h *= 16777619u;
    }
    return h;
  }

  int findExistingSlot(int refNumber, uint32_t senderHash, uint8_t totalParts) {
    for (int i = 0; i < kMaxConcatSlots; i++) {
      if (slots_[i].inUse &&
          slots_[i].refNumber == refNumber &&
          slots_[i].senderHash == senderHash &&
          slots_[i].totalParts == totalParts)
        return i;
    }
    return -1;
  }

  int findFreeSlot() {
    for (int i = 0; i < kMaxConcatSlots; i++)
      if (!slots_[i].inUse) return i;
    return -1;
  }

  int findOldestSlot() {
    int oldest = 0;
    uint32_t oldestTime = slots_[0].firstPartTime;
    for (int i = 1; i < kMaxConcatSlots; i++) {
      if (slots_[i].firstPartTime < oldestTime) {
        oldestTime = slots_[i].firstPartTime;
        oldest = i;
      }
    }
    return oldest;
  }

  int findOrCreateSlot(int refNumber, uint32_t senderHash,
                       uint8_t totalParts, uint32_t nowMs,
                       ConcatResult& out) {
    int slot = findExistingSlot(refNumber, senderHash, totalParts);
    if (slot >= 0) return slot;

    slot = findFreeSlot();
    if (slot < 0) {
      // 淘汰最老的未完成槽位
      slot = findOldestSlot();
      if (slots_[slot].receivedParts > 0) {
        collectDeletableForEvict(slot, out);
      }
      clearSlot(slot);
    }

    slots_[slot].inUse = true;
    slots_[slot].refNumber = refNumber;
    slots_[slot].senderHash = senderHash;
    slots_[slot].totalParts = totalParts;
    slots_[slot].receivedParts = 0;
    slots_[slot].firstPartTime = nowMs;
    return slot;
  }

  void clearSlot(int slot) {
    slots_[slot] = Slot{};
  }

  void collectDeletableForEvict(int slot, ConcatResult& out) {
    out.evicted = true;
    out.evictedTotalParts = slots_[slot].totalParts;
    out.evictedReceivedParts = slots_[slot].receivedParts;
    for (int j = 0; j < kMaxConcatParts; j++) {
      if (slots_[slot].parts[j].valid &&
          slots_[slot].parts[j].storageIndex > 0 &&
          out.evictedDeletableCount < kMaxConcatParts) {
        out.evictedDeletableIndices[out.evictedDeletableCount++] =
            slots_[slot].parts[j].storageIndex;
      }
    }
  }

  void collectDeletable(int slot, ConcatResult& out) {
    for (int j = 0; j < kMaxConcatParts; j++) {
      if (slots_[slot].parts[j].valid &&
          slots_[slot].parts[j].storageIndex > 0 &&
          out.deletableCount < kMaxConcatParts) {
        out.deletableIndices[out.deletableCount++] =
            slots_[slot].parts[j].storageIndex;
      }
    }
  }
};

}  // namespace modem_concat
