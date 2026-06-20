#include "modem_line_reader.h"

ModemLineResult ModemLineReader::onByte(char value) {
  if (value == '\r') return ModemLineResult::None;

  if (value == '\n') {
    if (overflowed_) {
      reset();
      return ModemLineResult::Dropped;
    }
    buffer_[length_] = '\0';
    length_ = 0;
    return ModemLineResult::Line;
  }

  if (overflowed_) return ModemLineResult::None;
  if (length_ + 1 < kCapacity) {
    buffer_[length_++] = value;
    return ModemLineResult::None;
  }

  length_ = 0;
  overflowed_ = true;
  return ModemLineResult::None;
}

const char* ModemLineReader::line() const {
  return buffer_;
}

void ModemLineReader::reset() {
  length_ = 0;
  overflowed_ = false;
  buffer_[0] = '\0';
}
