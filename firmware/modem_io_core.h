#pragma once
// modem_io_core — 无 Arduino 依赖的 Modem I/O 核心模型(§2.1)。
// 可在宿主机用 g++ 测试,不依赖 Serial1/FreeRTOS/Arduino。
//
// 职责:
//  - 命令数据模型(优先级/超时/期望响应/结果接收)
//  - 固定容量命令队列(按优先级出队)
//  - AT 响应终止行识别(OK/ERROR/+CME ERROR/+CMS ERROR)
//  - URC 注册与分流
//  - 等待 '>' prompt 的状态跟踪
#include <stdint.h>
#include <stddef.h>
#include <string.h>

namespace modem_io {

// ── 编译期常量 ──
constexpr uint8_t kMaxCmdText = 128;       // 命令文本最大长度(含 '\0')
constexpr uint16_t kMaxResponseBytes = 512; // 响应缓冲上限
constexpr uint8_t kMaxCmdQueue = 16;        // 命令队列容量(共享四档)
constexpr uint8_t kMaxExpectPrefix = 12;    // 期望前缀最大长度

// ── 命令优先级(§2.2) ──
enum class Priority : uint8_t {
  ReceiveCritical = 0,  // SIM 存储读取/删除、接收配置恢复
  SmsSend = 1,          // 出站短信发送事务
  Interactive = 2,      // Hub/Web AT、人工诊断
  Background = 3,       // 状态刷新、保号、巡检
};

// ── 命令结果(§2.1) ──
enum class Result : uint8_t {
  Pending = 0,
  Ok,
  Error,
  CmeError,       // +CME ERROR: <code>
  CmsError,       // +CMS ERROR: <code>
  Timeout,
  QueueFull,      // 队列满,拒绝入队
  Cancelled,      // 调用方取消
  ProtocolError,  // 响应过长/截断/无法解析
};
const char* resultName(Result r);

// ── 数值错误码 ──
struct ErrorCode {
  Result kind = Result::Pending;
  int16_t code = 0;  // CME/CMS 数值码,Ok/Error=0
};

// ── AT 响应终止行识别 ──
enum class TermLine : uint8_t {
  None = 0,
  Ok,
  Error,
  CmeError,
  CmsError,
  GreaterThan,  // '>' prompt
};
TermLine classifyTermLine(const char* line);
// 从 "+CME ERROR: 515" 中提取 code。返回 true 表示匹配。
bool parseCmeCode(const char* line, int16_t& code);
bool parseCmsCode(const char* line, int16_t& code);

// ── URC 类型(§2.3) ──
enum class UrcType : uint8_t {
  Unknown = 0,
  Cmt,          // +CMT: <pdu> — 收信 PDU
  Cmti,         // +CMTI: <mem>,<index> — SIM 存储有新短信
  Cds,          // +CDS: ... — 小区广播
  Cereg,        // +CEREG: ... — EPS 注册状态
  CpinReady,    // +CPIN: READY — SIM 就绪
  MatReady,     // +MATREADY — 模组复位完成
  Mipcall,      // +MIPCALL: ...
};

struct UrcInfo {
  UrcType type = UrcType::Unknown;
  int16_t simIndex = 0;   // +CMTI 的存储索引
  uint8_t ceregStat = 0;  // +CEREG 的注册状态
};

UrcType classifyUrc(const char* line, UrcInfo& info);

// ── 命令模型(§2.1) ──
struct Command {
  char text[kMaxCmdText];
  Priority priority = Priority::Background;
  uint16_t timeoutMs = 5000;
  char expectPrefix[kMaxExpectPrefix];  // 空串=不校验
  bool waitPrompt = false;              // 等待 '>'
  // 以下由 modem I/O 层写入:
  Result result = Result::Pending;
  int16_t errorCode = 0;
  char response[kMaxResponseBytes];
  uint16_t responseLen = 0;
  bool truncated = false;
  // 生命周期:
  uint32_t submitTime = 0;
  uint32_t deadlineMs = 0;
  uint32_t generation = 0;     // 防 dangling 的代数

  void clearResult() {
    result = Result::Pending;
    errorCode = 0;
    response[0] = '\0';
    responseLen = 0;
    truncated = false;
  }
};

// ── 命令队列(固定容量,按优先级出队) ──
class CommandQueue {
public:
  // 入队。满时返回 false(不覆盖已有命令)。
  bool push(const Command& cmd) {
    if (count_ >= kMaxCmdQueue) return false;
    slots_[tail_] = cmd;
    tail_ = (tail_ + 1) % kMaxCmdQueue;
    count_++;
    return true;
  }

  // 出队最高优先级命令(低数值=高优先级)。跳过已取消/已完成命令。
  // 空时或仅剩已完成/已取消命令时返回 nullptr。
  Command* pop() {
    while (count_ > 0) {
      // 遍历找最高优先级(最低数值)的 pending 命令
      int bestSlot = -1;
      uint8_t bestPri = 255;
      for (uint8_t i = 0; i < count_; i++) {
        uint8_t idx = (head_ + i) % kMaxCmdQueue;
        if (slots_[idx].result != Result::Pending) continue;
        if (static_cast<uint8_t>(slots_[idx].priority) < bestPri) {
          bestPri = static_cast<uint8_t>(slots_[idx].priority);
          bestSlot = (int)idx;
        }
      }
      if (bestSlot < 0) {
        // 所有命令都已完成/取消,清空队列
        head_ = tail_;
        count_ = 0;
        return nullptr;
      }
      // 将 best 与 head 交换后出队
      if ((uint8_t)bestSlot != head_) {
        Command tmp = slots_[head_];
        slots_[head_] = slots_[(uint8_t)bestSlot];
        slots_[(uint8_t)bestSlot] = tmp;
      }
      Command* out = &slots_[head_];
      head_ = (head_ + 1) % kMaxCmdQueue;
      count_--;
      return out;
    }
    return nullptr;
  }

  // 取消所有匹配优先级的未执行命令
  uint8_t cancelPriority(Priority p) {
    uint8_t n = 0;
    for (uint8_t i = 0; i < count_; i++) {
      uint8_t idx = (head_ + i) % kMaxCmdQueue;
      if (slots_[idx].result == Result::Pending &&
          slots_[idx].priority == p) {
        slots_[idx].result = Result::Cancelled;
        n++;
      }
    }
    return n;
  }

  uint8_t count() const { return count_; }
  uint8_t capacity() const { return kMaxCmdQueue; }
  bool empty() const { return count_ == 0; }
  bool full() const { return count_ >= kMaxCmdQueue; }

  // 诊断:各优先级深度和高水位
  void depths(uint8_t out[4]) const {
    out[0] = out[1] = out[2] = out[3] = 0;
    for (uint8_t i = 0; i < count_; i++) {
      uint8_t idx = (head_ + i) % kMaxCmdQueue;
      out[static_cast<uint8_t>(slots_[idx].priority)]++;
    }
  }

private:
  Command slots_[kMaxCmdQueue];
  uint8_t head_ = 0;
  uint8_t tail_ = 0;
  uint8_t count_ = 0;
};

// ── AT 响应解析状态机(§2.3) ──
// 行驱动:每收到一行(来自 ModemLineReader)调用一次 onLine()。
// 自动识别终止行/URC/prompt,累积响应文本,检测超时和截断。
enum class ParserState : uint8_t {
  Idle,
  SendingCommand,   // 命令已提交到 UART(echo 待跳过)
  WaitingResponse,  // 收集响应行,等待终止
  WaitingPrompt,    // 等待 '>' (AT+CMGS 等)
  SendingPayload,   // 发送 PDU payload 到模组
  WaitingFinal,     // 等待 Ctrl+Z 后的最终 OK/ERROR
  Done,
};

enum class LineAction : uint8_t {
  Accumulate,    // 正常响应行,追加到缓冲
  Terminate,     // 终止行(OK/ERROR/CME/CMS),命令结束
  Urc,           // URC 行,调用方应分派到应用层
  Prompt,        // '>' prompt,调用方应发送 payload
  Skip,          // 跳过此行(echo 或无关行)
  Overflow,      // 响应缓冲满,截断
};

struct LineResult {
  LineAction action = LineAction::Accumulate;
  TermLine term = TermLine::None;
  UrcType urc = UrcType::Unknown;
  UrcInfo urcInfo;  // +CMTI 的索引等
};

class ResponseParser {
public:
  void reset() {
    state_ = ParserState::Idle;
    cmd_ = nullptr;
    responseLen_ = 0;
    truncated_ = false;
    echoSkipped_ = false;
    startMs_ = 0;
  }

  // 开始执行命令
  void start(Command& cmd, uint32_t nowMs) {
    reset();
    state_ = ParserState::SendingCommand;
    cmd_ = &cmd;
    cmd.clearResult();
    startMs_ = nowMs;
  }

  ParserState state() const { return state_; }

  // 收到一行(已去 \r\n)。返回此行应如何处置。
  // 调用方:根据 action 决定累积/分发 URC/完成命令/发送 payload。
  LineResult onLine(const char* line, uint32_t nowMs) {
    LineResult out;

    // 检查超时
    if (cmd_ && state_ != ParserState::Idle && state_ != ParserState::Done) {
      uint32_t elapsed = nowMs - startMs_;
      if (elapsed >= cmd_->timeoutMs) {
        cmd_->result = Result::Timeout;
        state_ = ParserState::Done;
        out.action = LineAction::Terminate;
        out.term = TermLine::None;
        return out;
      }
    }

    // 分类此行
    TermLine term = classifyTermLine(line);
    UrcInfo urcInfo;
    UrcType urc = classifyUrc(line, urcInfo);

    switch (state_) {
      case ParserState::Idle:
      case ParserState::Done:
        // 未在等响应:任何 URC 仍应分发
        if (urc != UrcType::Unknown) {
          out.action = LineAction::Urc;
          out.urc = urc;
          out.urcInfo = urcInfo;
        } else {
          out.action = LineAction::Skip;
        }
        break;

      case ParserState::SendingCommand:
        // 跳过命令 echo(首行通常为命令回显)
        if (!echoSkipped_ && term == TermLine::None && urc == UrcType::Unknown) {
          echoSkipped_ = true;
          out.action = LineAction::Skip;
          break;
        }
        echoSkipped_ = true;
        // 进入等待响应状态后按 WaitingResponse 逻辑处理
        state_ = ParserState::WaitingResponse;
        // fall through

      case ParserState::WaitingResponse:
        if (urc != UrcType::Unknown) {
          // URC 插入响应中间:分发但不污染命令响应
          // 例外:CPIN READY 在命令执行期间不是 URC(查询响应),仅 Idle 期间才是
          if (urc == UrcType::CpinReady) {
            // 累积为正常响应行
            if (!accumulateLine(line)) out.action = LineAction::Overflow;
            break;
          }
          out.action = LineAction::Urc;
          out.urc = urc;
          out.urcInfo = urcInfo;
          break;
        }
        if (term == TermLine::GreaterThan) {
          // 需要 prompt(AT+CMGS 等)
          state_ = ParserState::WaitingPrompt;
          out.action = LineAction::Prompt;
          break;
        }
        if (term != TermLine::None) {
          // 终止行:先累积(以包含错误码文本),再完结
          accumulateLine(line);
          finalizeCommand(term);
          out.action = LineAction::Terminate;
          out.term = term;
          break;
        }
        // 普通响应行:累积(校验期望前缀)
        if (!accumulateLine(line)) {
          out.action = LineAction::Overflow;
        }
        break;

      case ParserState::WaitingPrompt:
        // 不应收到非提示行;检查 URC
        if (urc != UrcType::Unknown) {
          out.action = LineAction::Urc;
          out.urc = urc;
          out.urcInfo = urcInfo;
        } else {
          out.action = LineAction::Skip;
        }
        break;

      case ParserState::SendingPayload:
        // payload 已发送,转入等待最终响应(继续处理当前行)
        state_ = ParserState::WaitingFinal;
        // fall through to WaitingFinal
        [[fallthrough]];

      case ParserState::WaitingFinal:
        if (urc != UrcType::Unknown) {
          out.action = LineAction::Urc;
          out.urc = urc;
          out.urcInfo = urcInfo;
          break;
        }
        if (term != TermLine::None) {
          accumulateLine(line);
          finalizeCommand(term);
          out.action = LineAction::Terminate;
          out.term = term;
          break;
        }
        if (!accumulateLine(line)) {
          out.action = LineAction::Overflow;
        }
        break;
    }
    return out;
  }

  // 通知 payload 已发送,进入等待最终响应
  void payloadSent() {
    if (state_ == ParserState::WaitingPrompt)
      state_ = ParserState::SendingPayload;
  }

  // 主动超时
  void forceTimeout() {
    if (cmd_) cmd_->result = Result::Timeout;
    state_ = ParserState::Done;
  }

  // 取消(prompt 超时发送 ESC 后)
  void cancel() {
    if (cmd_) cmd_->result = Result::Cancelled;
    state_ = ParserState::Done;
  }

private:
  ParserState state_ = ParserState::Idle;
  Command* cmd_ = nullptr;
  uint16_t responseLen_ = 0;
  bool truncated_ = false;
  bool echoSkipped_ = false;
  uint32_t startMs_ = 0;

  bool accumulateLine(const char* line) {
    if (!cmd_ || !line) return true;
    uint16_t avail = kMaxResponseBytes - responseLen_ - 1;  // reserve '\0'
    uint16_t n = 0;
    while (line[n] && n < avail) {
      cmd_->response[responseLen_ + n] = line[n];
      n++;
    }
    responseLen_ += n;
    cmd_->response[responseLen_] = '\0';
    cmd_->responseLen = responseLen_;
    if (line[n] != '\0') {
      truncated_ = true;
      cmd_->truncated = true;
      return false;
    }
    return true;
  }

  void finalizeCommand(TermLine term) {
    if (!cmd_) return;
    switch (term) {
      case TermLine::Ok:
        cmd_->result = Result::Ok;
        break;
      case TermLine::Error:
        cmd_->result = Result::Error;
        break;
      case TermLine::CmeError:
        cmd_->result = Result::CmeError;
        parseCmeCode(cmd_->response, cmd_->errorCode);
        break;
      case TermLine::CmsError:
        cmd_->result = Result::CmsError;
        parseCmsCode(cmd_->response, cmd_->errorCode);
        break;
      default:
        cmd_->result = Result::Error;
        break;
    }
    cmd_->truncated = truncated_;
    state_ = ParserState::Done;
  }
};

}  // namespace modem_io
