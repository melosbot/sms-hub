// modem_io_core 宿主机测试(不依赖 Arduino)
#include "modem_io_core.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

using namespace modem_io;

// ── classifyTermLine ──
void test_classifyTermLine() {
  assert(classifyTermLine("OK") == TermLine::Ok);
  assert(classifyTermLine("OK\r") == TermLine::Ok);
  assert(classifyTermLine(" OK") == TermLine::Ok);
  assert(classifyTermLine("ERROR") == TermLine::Error);
  assert(classifyTermLine("+CME ERROR: 515") == TermLine::CmeError);
  assert(classifyTermLine("+CMS ERROR: 500") == TermLine::CmsError);
  assert(classifyTermLine(">") == TermLine::GreaterThan);
  assert(classifyTermLine("> ") == TermLine::GreaterThan);
  assert(classifyTermLine("ATI") == TermLine::None);
  assert(classifyTermLine("") == TermLine::None);
  assert(classifyTermLine(nullptr) == TermLine::None);
  printf("  classifyTermLine 通过\n");
}

// ── parseCmeCode / parseCmsCode ──
void test_parseErrorCodes() {
  int16_t code = 0;
  assert(parseCmeCode("+CME ERROR: 515", code) && code == 515);
  assert(parseCmeCode("+CME ERROR: 0", code) && code == 0);
  assert(parseCmeCode("abc +CME ERROR: 42 xyz", code) && code == 42);
  assert(!parseCmeCode("OK", code));
  assert(!parseCmeCode(nullptr, code));

  assert(parseCmsCode("+CMS ERROR: 500", code) && code == 500);
  assert(parseCmsCode("+CMS ERROR: 331", code) && code == 331);
  assert(!parseCmsCode("ERROR", code));
  printf("  parseErrorCodes 通过\n");
}

// ── classifyUrc ──
void test_classifyUrc() {
  UrcInfo info;
  info.simIndex = 0;
  info.ceregStat = 0;

  assert(classifyUrc("+CMT: 0791...", info) == UrcType::Cmt);

  assert(classifyUrc("+CMTI: \"SM\",7", info) == UrcType::Cmti);
  assert(info.simIndex == 7);

  assert(classifyUrc("+CEREG: 1,5", info) == UrcType::Cereg);
  assert(info.ceregStat == 5);

  assert(classifyUrc("+CPIN: READY", info) == UrcType::CpinReady);
  assert(classifyUrc("+MATREADY", info) == UrcType::MatReady);

  assert(classifyUrc("+MIPCALL: 1,1", info) == UrcType::Mipcall);
  assert(classifyUrc("RANDOM_LINE", info) == UrcType::Unknown);
  assert(classifyUrc("", info) == UrcType::Unknown);
  assert(classifyUrc(nullptr, info) == UrcType::Unknown);
  printf("  classifyUrc 通过\n");
}

// ── CommandQueue: 基本入队/出队 ──
void test_queue_basic() {
  CommandQueue q;
  assert(q.empty());
  assert(q.count() == 0);

  Command c1; c1.priority = Priority::Background;
  c1.text[0] = 'A'; c1.text[1] = '\0';
  assert(q.push(c1));
  assert(q.count() == 1);

  Command* out = q.pop();
  assert(out != nullptr);
  assert(out->text[0] == 'A');
  assert(q.empty());
  printf("  queue_basic 通过\n");
}

// ── CommandQueue: 优先级出队 ──
void test_queue_priority() {
  CommandQueue q;
  // 按 Background → Interactive → SmsSend → ReceiveCritical 入队
  Command c;
  c.priority = Priority::Background;   c.text[0] = 'B'; q.push(c);
  c.priority = Priority::Interactive;  c.text[0] = 'I'; q.push(c);
  c.priority = Priority::SmsSend;      c.text[0] = 'S'; q.push(c);
  c.priority = Priority::ReceiveCritical; c.text[0] = 'R'; q.push(c);

  // 应按 ReceiveCritical → SmsSend → Interactive → Background 出队
  Command* out = q.pop();
  assert(out->text[0] == 'R');  // ReceiveCritical 最高优先
  out = q.pop();
  assert(out->text[0] == 'S');  // SmsSend
  out = q.pop();
  assert(out->text[0] == 'I');  // Interactive
  out = q.pop();
  assert(out->text[0] == 'B');  // Background
  assert(q.pop() == nullptr);
  printf("  queue_priority 通过\n");
}

// ── CommandQueue: 满队列拒绝 ──
void test_queue_full() {
  CommandQueue q;
  Command c;
  c.priority = Priority::Background;
  for (int i = 0; i < kMaxCmdQueue; i++) {
    assert(q.push(c));
  }
  assert(q.full());
  assert(!q.push(c));  // 满,拒绝
  printf("  queue_full 通过\n");
}

// ── CommandQueue: cancelPriority ──
void test_queue_cancel() {
  CommandQueue q;
  Command c;
  c.priority = Priority::Background; c.text[0] = 'B'; q.push(c);
  c.priority = Priority::Background; c.text[0] = 'b'; q.push(c);
  c.priority = Priority::ReceiveCritical; c.text[0] = 'R'; q.push(c);

  uint8_t n = q.cancelPriority(Priority::Background);
  assert(n == 2);

  // ReceiveCritical 应仍可出队
  Command* out = q.pop();
  assert(out != nullptr);
  assert(out->text[0] == 'R');
  // Background 命令已取消,不应出队
  assert(q.pop() == nullptr);
  printf("  queue_cancel 通过\n");
}

// ── CommandQueue: depths ──
void test_queue_depths() {
  CommandQueue q;
  Command c;
  c.priority = Priority::ReceiveCritical; q.push(c);
  c.priority = Priority::Background; q.push(c);
  c.priority = Priority::Background; q.push(c);
  c.priority = Priority::Interactive; q.push(c);

  uint8_t d[4];
  q.depths(d);
  assert(d[0] == 1);  // ReceiveCritical
  assert(d[1] == 0);  // SmsSend
  assert(d[2] == 1);  // Interactive
  assert(d[3] == 2);  // Background
  printf("  queue_depths 通过\n");
}

// ── Command: clearResult ──
void test_command_clearResult() {
  Command c;
  c.result = Result::Ok;
  c.errorCode = 515;
  strcpy(c.response, "test");
  c.responseLen = 4;
  c.clearResult();
  assert(c.result == Result::Pending);
  assert(c.errorCode == 0);
  assert(c.response[0] == '\0');
  assert(c.responseLen == 0);
  assert(!c.truncated);
  printf("  command_clearResult 通过\n");
}

// ── resultName ──
void test_resultName() {
  assert(strcmp(resultName(Result::Ok), "Ok") == 0);
  assert(strcmp(resultName(Result::CmeError), "CmeError") == 0);
  assert(strcmp(resultName(Result::Timeout), "Timeout") == 0);
  assert(strcmp(resultName(Result::Pending), "Pending") == 0);
  printf("  resultName 通过\n");
}

// ── ResponseParser: 正常 OK ──
void test_parser_normal_ok() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 5000;
  p.start(cmd, 0);
  // echo line skipped
  auto r = p.onLine("AT+CSQ", 100);
  assert(r.action == LineAction::Skip);
  // response
  r = p.onLine("+CSQ: 20,99", 200);
  assert(r.action == LineAction::Accumulate);
  // OK
  r = p.onLine("OK", 300);
  assert(r.action == LineAction::Terminate);
  assert(r.term == TermLine::Ok);
  assert(cmd.result == Result::Ok);
  assert(cmd.responseLen > 0);
  printf("  parser_normal_ok 通过\n");
}

// ── ResponseParser: +CME ERROR ──
void test_parser_cme_error() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 5000;
  p.start(cmd, 0);
  auto r = p.onLine("AT+CMGS=...", 100);
  r = p.onLine("+CME ERROR: 515", 200);
  assert(r.action == LineAction::Terminate);
  assert(r.term == TermLine::CmeError);
  assert(cmd.result == Result::CmeError);
  assert(cmd.errorCode == 515);
  printf("  parser_cme_error 通过\n");
}

// ── ResponseParser: +CMS ERROR ──
void test_parser_cms_error() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 5000;
  p.start(cmd, 0);
  p.onLine("AT+CMGS=...", 100);
  auto r = p.onLine("+CMS ERROR: 500", 200);
  assert(r.action == LineAction::Terminate);
  assert(cmd.result == Result::CmsError);
  assert(cmd.errorCode == 500);
  printf("  parser_cms_error 通过\n");
}

// ── ResponseParser: Timeout ──
void test_parser_timeout() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 1000;
  p.start(cmd, 0);
  p.onLine("AT+CSQ", 100);
  // 超时(nowMs > startMs + timeoutMs)
  auto r = p.onLine("+CSQ: 20,99", 1100);
  assert(r.action == LineAction::Terminate);
  assert(cmd.result == Result::Timeout);
  printf("  parser_timeout 通过\n");
}

// ── ResponseParser: 超时后下一条命令正常 ──
void test_parser_timeout_recovery() {
  ResponseParser p;
  Command cmd1, cmd2;
  cmd1.timeoutMs = 500;
  p.start(cmd1, 0);
  p.onLine("AT+CSQ", 100);
  auto r = p.onLine("+CSQ: 20,99", 600);
  assert(cmd1.result == Result::Timeout);

  // 下一条命令正常
  cmd2.timeoutMs = 5000;
  p.start(cmd2, 1000);
  r = p.onLine("AT+CSQ", 1100);
  r = p.onLine("+CSQ: 20,99", 1200);
  r = p.onLine("OK", 1300);
  assert(cmd2.result == Result::Ok);
  printf("  parser_timeout_recovery 通过\n");
}

// ── ResponseParser: URC 插入响应中间 ──
void test_parser_urc_mid_response() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 5000;
  p.start(cmd, 0);
  p.onLine("AT+CSQ", 100);
  p.onLine("+CSQ: 20,99", 200);
  // URC 插入
  auto r = p.onLine("+CMTI: \"SM\",7", 300);
  assert(r.action == LineAction::Urc);
  assert(r.urc == UrcType::Cmti);
  assert(r.urcInfo.simIndex == 7);
  // 响应未被污染
  assert(strstr(cmd.response, "+CMTI") == nullptr);
  // 后续正常终止
  r = p.onLine("OK", 400);
  assert(r.action == LineAction::Terminate);
  assert(cmd.result == Result::Ok);
  printf("  parser_urc_mid_response 通过\n");
}

// ── ResponseParser: +CMT + PDU 插入 ──
void test_parser_urc_cmt_during_command() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 5000;
  p.start(cmd, 0);
  p.onLine("AT+CSQ", 100);
  // +CMT URC 带 PDU 行
  auto r = p.onLine("+CMT: ,30", 200);
  assert(r.action == LineAction::Urc);
  assert(r.urc == UrcType::Cmt);
  // 响应不受影响
  r = p.onLine("+CSQ: 20,99", 300);
  assert(r.action == LineAction::Accumulate);
  r = p.onLine("OK", 400);
  assert(cmd.result == Result::Ok);
  printf("  parser_urc_cmt_during_command 通过\n");
}

// ── ResponseParser: +MATREADY 在命令执行中出现 ──
void test_parser_matready_during_command() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 5000;
  p.start(cmd, 0);
  p.onLine("AT+CSQ", 100);
  auto r = p.onLine("+MATREADY", 200);
  assert(r.action == LineAction::Urc);
  assert(r.urc == UrcType::MatReady);
  // 命令正常完成
  p.onLine("+CSQ: 20,99", 300);
  r = p.onLine("OK", 400);
  assert(cmd.result == Result::Ok);
  printf("  parser_matready_during_command 通过\n");
}

// ── ResponseParser: +CPIN: READY(查询响应不得误判) ──
void test_parser_cpin_query_not_urc() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 5000;
  p.start(cmd, 0);
  p.onLine("AT+CPIN?", 100);
  // 查询响应:这不是 URC,应累积
  auto r = p.onLine("+CPIN: READY", 200);
  assert(r.action == LineAction::Accumulate);
  // NOT classified as Urc during response
  r = p.onLine("OK", 300);
  assert(cmd.result == Result::Ok);
  // 响应应包含 +CPIN: READY
  assert(strstr(cmd.response, "+CPIN: READY") != nullptr);
  printf("  parser_cpin_query_not_urc 通过\n");
}

// ── ResponseParser: prompt 等待(AT+CMGS) ──
void test_parser_wait_prompt() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 10000;
  cmd.waitPrompt = true;
  p.start(cmd, 0);
  p.onLine("AT+CMGS=30", 100);
  auto r = p.onLine(">", 200);
  assert(r.action == LineAction::Prompt);
  assert(p.state() == ParserState::WaitingPrompt);
  // 发送 payload
  p.payloadSent();
  assert(p.state() == ParserState::SendingPayload);
  // 最终响应
  r = p.onLine("+CMGS: 42", 300);
  assert(r.action == LineAction::Accumulate);
  r = p.onLine("OK", 400);
  assert(r.action == LineAction::Terminate);
  assert(cmd.result == Result::Ok);
  printf("  parser_wait_prompt 通过\n");
}

// ── ResponseParser: prompt 前出现 URC ──
void test_parser_urc_before_prompt() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 10000;
  p.start(cmd, 0);
  p.onLine("AT+CMGS=30", 100);
  // URC 在 prompt 前出现
  auto r = p.onLine("+CMTI: \"SM\",3", 200);
  assert(r.action == LineAction::Urc);
  // 然后 prompt
  r = p.onLine(">", 300);
  assert(r.action == LineAction::Prompt);
  printf("  parser_urc_before_prompt 通过\n");
}

// ── ResponseParser: 超长行截断 ──
void test_parser_overflow() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 5000;
  p.start(cmd, 0);
  p.onLine("AT+CSQ", 100);
  // 生成超长行
  char longLine[600];
  memset(longLine, 'X', sizeof(longLine) - 1);
  longLine[sizeof(longLine) - 1] = '\0';
  auto r = p.onLine(longLine, 200);
  assert(r.action == LineAction::Overflow);
  assert(cmd.truncated);
  printf("  parser_overflow 通过\n");
}

// ── ResponseParser: 空闲时 URC 仍分发 ──
void test_parser_urc_when_idle() {
  ResponseParser p;
  // 未在等命令时
  auto r = p.onLine("+CMTI: \"SM\",5", 0);
  assert(r.action == LineAction::Urc);
  assert(r.urc == UrcType::Cmti);
  printf("  parser_urc_when_idle 通过\n");
}

// ── ResponseParser: 未知行在 Idle/Done 状态跳过 ──
void test_parser_unknown_line_skipped() {
  ResponseParser p;
  assert(p.state() == ParserState::Idle);
  auto r = p.onLine("RANDOM", 0);
  assert(r.action == LineAction::Skip);

  Command cmd;
  cmd.timeoutMs = 5000;
  p.start(cmd, 0);
  p.onLine("AT+CSQ", 100);
  p.onLine("OK", 200);
  assert(p.state() == ParserState::Done);

  r = p.onLine("RANDOM", 300);
  assert(r.action == LineAction::Skip);  // Done 后未知行跳过
  printf("  parser_unknown_line_skipped 通过\n");
}

// ── 边缘测试:缺失换行(空行)、重复终止行、命令 echo 差异(§2.8 P1) ──
void test_parser_edge_empty_lines() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 5000;
  p.start(cmd, 0);
  // 空行不应影响解析
  auto r = p.onLine("", 100);
  assert(r.action == LineAction::Skip);
  r = p.onLine("AT+CSQ", 200);
  r = p.onLine("", 300);  // 空行
  r = p.onLine("+CSQ: 20,99", 400);
  assert(r.action == LineAction::Accumulate);
  r = p.onLine("OK", 500);
  assert(cmd.result == Result::Ok);
  printf("  parser_edge_empty_lines 通过\n");
}

void test_parser_edge_duplicate_terminator() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 5000;
  p.start(cmd, 0);
  p.onLine("AT+CSQ", 100);
  p.onLine("+CSQ: 20,99", 200);
  p.onLine("OK", 300);
  assert(cmd.result == Result::Ok);
  // 命令已完成后收到多余终止行:应跳过
  auto r = p.onLine("OK", 400);
  assert(r.action == LineAction::Skip);  // Done 状态忽略
  printf("  parser_edge_duplicate_terminator 通过\n");
}

void test_parser_edge_echo_with_urc() {
  // 命令 echo 中嵌入 URC(极端情况)
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 5000;
  p.start(cmd, 0);
  p.onLine("AT+CMGL=4", 100);  // echo, skip
  auto r = p.onLine("+CMTI: \"SM\",1", 200);  // URC during response
  assert(r.action == LineAction::Urc);
  p.onLine("OK", 300);
  assert(cmd.result == Result::Ok);
  printf("  parser_edge_echo_with_urc 通过\n");
}

void test_parser_edge_only_urc_lines() {
  // 只收 URC,无命令:应全部分发
  ResponseParser p;
  auto r = p.onLine("+CMTI: \"SM\",9", 0);
  assert(r.action == LineAction::Urc);
  r = p.onLine("+CMT: ,30", 100);
  assert(r.action == LineAction::Urc);
  r = p.onLine("+CEREG: 1,5", 200);
  assert(r.action == LineAction::Urc);
  printf("  parser_edge_only_urc_lines 通过\n");
}

void test_parser_edge_cancel_at_prompt() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 5000;
  p.start(cmd, 0);
  p.onLine("AT+CMGS=30", 100);
  p.onLine(">", 200);
  assert(p.state() == ParserState::WaitingPrompt);
  // prompt 后取消(不发送 payload)
  p.cancel();
  assert(cmd.result == Result::Cancelled);
  printf("  parser_edge_cancel_at_prompt 通过\n");
}

void test_parser_edge_multiple_urc_one_line() {
  // 单个 URC 行包含存储索引
  ResponseParser p;
  auto r = p.onLine("+CMTI: \"SM\",255", 0);
  assert(r.action == LineAction::Urc);
  assert(r.urc == UrcType::Cmti);
  assert(r.urcInfo.simIndex == 255);
  printf("  parser_edge_large_sim_index 通过\n");
}

void test_parser_edge_command_immediate_timeout() {
  ResponseParser p;
  Command cmd;
  cmd.timeoutMs = 0;  // 立即超时
  p.start(cmd, 1000);
  auto r = p.onLine("AT+CSQ", 1000);  // now=1000, start=1000, timeout=0
  assert(r.action == LineAction::Terminate);
  assert(cmd.result == Result::Timeout);
  printf("  parser_edge_immediate_timeout 通过\n");
}

int main() {
  test_classifyTermLine();
  test_parseErrorCodes();
  test_classifyUrc();
  test_queue_basic();
  test_queue_priority();
  test_queue_full();
  test_queue_cancel();
  test_queue_depths();
  test_command_clearResult();
  test_resultName();
  test_parser_normal_ok();
  test_parser_cme_error();
  test_parser_cms_error();
  test_parser_timeout();
  test_parser_timeout_recovery();
  test_parser_urc_mid_response();
  test_parser_urc_cmt_during_command();
  test_parser_matready_during_command();
  test_parser_cpin_query_not_urc();
  test_parser_wait_prompt();
  test_parser_urc_before_prompt();
  test_parser_overflow();
  test_parser_urc_when_idle();
  test_parser_unknown_line_skipped();
  test_parser_edge_empty_lines();
  test_parser_edge_duplicate_terminator();
  test_parser_edge_echo_with_urc();
  test_parser_edge_only_urc_lines();
  test_parser_edge_cancel_at_prompt();
  test_parser_edge_multiple_urc_one_line();
  test_parser_edge_command_immediate_timeout();

  printf("modem_io_core 所有测试通过\n");
  return 0;
}
