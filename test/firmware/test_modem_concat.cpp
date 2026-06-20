// 长短信合并状态机独立测试(宿主机 g++,不依赖 Arduino)
#include "modem_concat.h"
#include <assert.h>
#include <stdio.h>

using namespace modem_concat;

int main() {
  ConcatState cs;

  // ── 基础:完整收齐 3 段长短信 ──
  cs.reset();
  {
    // 分段1/3 (sender="+8613800138000")
    uint32_t hash1 = ConcatState{}.slot(0).senderHash;  // dummy for hash
    (void)hash1;
    uint32_t senderHash = 0xabcdef12;  // 预计算 hash
    auto r = cs.onPart(42, senderHash, 3, 1, 101, 1000);
    assert(r.action == ConcatAction::StorePart);
    assert(r.receivedParts == 1);
    assert(cs.activeSlots() == 1);
    assert(!cs.isStorageIndexProtected(0));
    assert(cs.isStorageIndexProtected(101));

    r = cs.onPart(42, senderHash, 3, 2, 102, 2000);
    assert(r.action == ConcatAction::StorePart);
    assert(r.receivedParts == 2);

    r = cs.onPart(42, senderHash, 3, 3, 103, 3000);
    assert(r.action == ConcatAction::Complete);
    assert(r.receivedParts == 3);
    assert(r.deletableCount == 3);
    assert(cs.activeSlots() == 0);
    // 收齐后索引不再受保护
    assert(!cs.isStorageIndexProtected(101));
    assert(!cs.isStorageIndexProtected(102));
    assert(!cs.isStorageIndexProtected(103));
  }

  // ── 超时:30s 后以不完整内容提交 ──
  cs.reset();
  {
    uint32_t senderHash = 0x11111111;
    auto r = cs.onPart(1, senderHash, 4, 1, 201, 0);
    assert(r.action == ConcatAction::StorePart);
    assert(cs.activeSlots() == 1);

    r = cs.onPart(1, senderHash, 4, 2, 202, 5000);
    assert(r.action == ConcatAction::StorePart);

    // 未超时
    r = cs.checkTimeout(25000, false);
    assert(r.action == ConcatAction::None);

    // 已超时
    r = cs.checkTimeout(31000, false);
    assert(r.action == ConcatAction::FinalizePartial);
    assert(r.totalParts == 4);
    assert(r.receivedParts == 2);
    assert(r.deletableCount == 2);
    assert(cs.activeSlots() == 0);
  }

  // ── 重复分段:幂等 ──
  cs.reset();
  {
    uint32_t senderHash = 0x22222222;
    auto r = cs.onPart(7, senderHash, 2, 1, 301, 100);
    assert(r.action == ConcatAction::StorePart);
    assert(r.receivedParts == 1);

    // 重复分段
    r = cs.onPart(7, senderHash, 2, 1, 301, 200);
    assert(r.action == ConcatAction::StorePart);
    assert(r.receivedParts == 1);  // receivedParts 不增加

    r = cs.onPart(7, senderHash, 2, 2, 302, 300);
    assert(r.action == ConcatAction::Complete);
    assert(r.receivedParts == 2);
    assert(r.deletableCount == 2);
  }

  // ── 乱序接收 ──
  cs.reset();
  {
    uint32_t senderHash = 0x33333333;
    // 先收第3段
    auto r = cs.onPart(9, senderHash, 5, 3, 401, 100);
    assert(r.action == ConcatAction::StorePart);
    assert(r.receivedParts == 1);

    // 再收第1段
    r = cs.onPart(9, senderHash, 5, 1, 402, 200);
    assert(r.action == ConcatAction::StorePart);
    assert(r.receivedParts == 2);

    // 再收第5段
    r = cs.onPart(9, senderHash, 5, 5, 403, 300);
    assert(r.action == ConcatAction::StorePart);
    assert(r.receivedParts == 3);

    // 收第4段
    r = cs.onPart(9, senderHash, 5, 4, 404, 400);
    assert(r.receivedParts == 4);

    // 收第2段 — 收齐
    r = cs.onPart(9, senderHash, 5, 2, 405, 500);
    assert(r.action == ConcatAction::Complete);
    assert(r.receivedParts == 5);
  }

  // ── 输入校验:非法参数 ──
  cs.reset();
  {
    uint32_t sh = 0x44444444;
    // totalParts 为 0
    auto r = cs.onPart(5, sh, 0, 1, 501, 0);
    assert(r.action == ConcatAction::None);
    assert(cs.activeSlots() == 0);

    // seqNumber > totalParts
    r = cs.onPart(5, sh, 3, 5, 502, 0);
    assert(r.action == ConcatAction::None);
    assert(cs.activeSlots() == 0);

    // seqNumber 为 0
    r = cs.onPart(5, sh, 3, 0, 503, 0);
    assert(r.action == ConcatAction::None);
  }

  // ── 多组并存 ──
  cs.reset();
  {
    uint32_t hashA = 0xaaaaaaaa;
    uint32_t hashB = 0xbbbbbbbb;

    auto r1 = cs.onPart(11, hashA, 2, 1, 601, 1000);
    assert(r1.action == ConcatAction::StorePart);
    assert(cs.activeSlots() == 1);

    auto r2 = cs.onPart(22, hashB, 3, 1, 701, 2000);
    assert(r2.action == ConcatAction::StorePart);
    assert(cs.activeSlots() == 2);

    // 不同组互不干扰
    r1 = cs.onPart(11, hashA, 2, 2, 602, 3000);
    assert(r1.action == ConcatAction::Complete);
    assert(cs.activeSlots() == 1);  // hashA 的槽位已释放

    r2 = cs.onPart(22, hashB, 3, 2, 702, 4000);
    assert(r2.action == ConcatAction::StorePart);
    assert(cs.activeSlots() == 1);

    r2 = cs.onPart(22, hashB, 3, 3, 703, 5000);
    assert(r2.action == ConcatAction::Complete);
    assert(cs.activeSlots() == 0);
  }

  // ── 槽位满时淘汰最老未完成组 ──
  cs.reset();
  {
    // 填满所有槽位(kMaxConcatSlots=5)
    for (int i = 0; i < kMaxConcatSlots; i++) {
      uint32_t h = 0xc0000000 + i;
      auto r = cs.onPart(100 + i, h, 3, 1, 800 + i, (uint32_t)(i * 1000));
      assert(r.action == ConcatAction::StorePart);
    }
    assert(cs.activeSlots() == kMaxConcatSlots);

    // 再一个新组:应淘汰最老的(第0组,time=0)
    uint32_t hNew = 0xdddddddd;
    auto r = cs.onPart(999, hNew, 2, 1, 900, 10000);
    // 新分段正常存入(action=StorePart),同时淘汰了最老槽位(evicted=true)
    assert(r.action == ConcatAction::StorePart);
    assert(r.evicted);
    assert(r.evictedTotalParts == 3);
    assert(r.evictedReceivedParts == 1);
    assert(r.evictedDeletableCount == 1);
    assert(r.evictedDeletableIndices[0] == 800);  // 第0组的分段索引
  }

  // ── 存储索引保护 ──
  cs.reset();
  {
    uint32_t sh = 0xeeeeeeee;
    auto r = cs.onPart(55, sh, 3, 1, 1001, 0);
    assert(cs.isStorageIndexProtected(1001));
    assert(!cs.isStorageIndexProtected(1002));  // 未出现的索引
    assert(!cs.isStorageIndexProtected(0));      // 无效索引
    assert(!cs.isStorageIndexProtected(-1));

    r = cs.onPart(55, sh, 3, 2, 1002, 100);
    assert(cs.isStorageIndexProtected(1001));
    assert(cs.isStorageIndexProtected(1002));

    // 收齐后索引不再受保护
    r = cs.onPart(55, sh, 3, 3, 1003, 200);
    assert(r.action == ConcatAction::Complete);
    assert(!cs.isStorageIndexProtected(1001));
    assert(!cs.isStorageIndexProtected(1002));
    assert(!cs.isStorageIndexProtected(1003));
  }

  // ── ringWouldDrop 阻止超时删除 ──
  cs.reset();
  {
    uint32_t sh = 0xffffffff;
    auto r = cs.onPart(77, sh, 2, 1, 1101, 0);
    assert(r.action == ConcatAction::StorePart);

    // 已超时但 ringWouldDrop=true
    r = cs.checkTimeout(40000, true);
    assert(r.action == ConcatAction::None);  // 受保护,不删除
    assert(cs.activeSlots() == 1);           // 槽位仍在

    // ringWouldDrop=false 时才提交
    r = cs.checkTimeout(40000, false);
    assert(r.action == ConcatAction::FinalizePartial);
    assert(cs.activeSlots() == 0);
  }

  printf("modem_concat 所有测试通过\n");
  return 0;
}
