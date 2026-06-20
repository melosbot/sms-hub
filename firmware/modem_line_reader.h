#pragma once

#include <stddef.h>

enum class ModemLineResult {
  None,
  Line,
  Dropped,
};

class ModemLineReader {
 public:
  static constexpr size_t kCapacity = 768;

  ModemLineResult onByte(char value);
  const char* line() const;
  void reset();

 private:
  char buffer_[kCapacity] = {};
  size_t length_ = 0;
  bool overflowed_ = false;
};
