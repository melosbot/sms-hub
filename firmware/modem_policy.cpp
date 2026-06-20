#include "modem_policy.h"

#include <stddef.h>
#include <string.h>

namespace {

struct Line {
  const char* begin;
  const char* end;
};

bool nextLine(const char*& cursor, Line& line) {
  if (cursor == nullptr || *cursor == '\0') return false;
  while (*cursor == '\r' || *cursor == '\n') cursor++;
  if (*cursor == '\0') return false;

  line.begin = cursor;
  while (*cursor != '\0' && *cursor != '\r' && *cursor != '\n') cursor++;
  line.end = cursor;
  while (line.begin < line.end && (*line.begin == ' ' || *line.begin == '\t')) line.begin++;
  while (line.end > line.begin && (line.end[-1] == ' ' || line.end[-1] == '\t')) line.end--;
  return true;
}

bool equals(const Line& line, const char* expected) {
  const size_t length = static_cast<size_t>(line.end - line.begin);
  return strlen(expected) == length && strncmp(line.begin, expected, length) == 0;
}

bool startsWith(const Line& line, const char* prefix) {
  const size_t prefixLength = strlen(prefix);
  return static_cast<size_t>(line.end - line.begin) >= prefixLength &&
         strncmp(line.begin, prefix, prefixLength) == 0;
}

void skipSpaces(const char*& cursor, const char* end) {
  while (cursor < end && (*cursor == ' ' || *cursor == '\t')) cursor++;
}

bool parseUnsigned(const char*& cursor, const char* end, unsigned int& value) {
  skipSpaces(cursor, end);
  if (cursor >= end || *cursor < '0' || *cursor > '9') return false;
  value = 0;
  while (cursor < end && *cursor >= '0' && *cursor <= '9') {
    value = value * 10 + static_cast<unsigned int>(*cursor - '0');
    cursor++;
  }
  skipSpaces(cursor, end);
  return true;
}

bool consume(const char*& cursor, const char* end, char expected) {
  skipSpaces(cursor, end);
  if (cursor >= end || *cursor != expected) return false;
  cursor++;
  return true;
}

}  // namespace

namespace modem_policy {

bool hasOkLine(const char* response) {
  const char* cursor = response;
  Line line = {};
  while (nextLine(cursor, line)) {
    if (equals(line, "OK")) return true;
  }
  return false;
}

bool cnmiUsesStorageNotifications(const char* response) {
  const char* cursor = response;
  Line line = {};
  while (nextLine(cursor, line)) {
    if (!startsWith(line, "+CNMI:")) continue;
    const char* value = line.begin + strlen("+CNMI:");
    const unsigned int expected[] = {2, 1, 0, 0, 0};
    for (size_t i = 0; i < 5; i++) {
      unsigned int parsed = 0;
      if (!parseUnsigned(value, line.end, parsed) || parsed != expected[i]) return false;
      if (i < 4 && !consume(value, line.end, ',')) return false;
    }
    skipSpaces(value, line.end);
    return value == line.end;
  }
  return false;
}

bool parseMipCallActive(const char* response, bool& active) {
  if (!hasOkLine(response)) return false;

  const char* cursor = response;
  Line line = {};
  while (nextLine(cursor, line)) {
    if (!startsWith(line, "+MIPCALL:")) continue;
    const char* value = line.begin + strlen("+MIPCALL:");
    unsigned int cid = 0;
    unsigned int state = 0;
    if (!parseUnsigned(value, line.end, cid) || !consume(value, line.end, ',') ||
        !parseUnsigned(value, line.end, state) || state > 1) return false;
    if (value < line.end && *value != ',') return false;
    (void)cid;
    active = state == 1;
    return true;
  }

  // Some ML307 firmware returns only OK when no application connection exists.
  active = false;
  return true;
}

}  // namespace modem_policy
