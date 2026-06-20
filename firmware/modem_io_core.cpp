// modem_io_core 非内联函数实现
#include "modem_io_core.h"
#include <stdio.h>

namespace modem_io {

const char* resultName(Result r) {
  switch (r) {
    case Result::Pending:       return "Pending";
    case Result::Ok:            return "Ok";
    case Result::Error:         return "Error";
    case Result::CmeError:      return "CmeError";
    case Result::CmsError:      return "CmsError";
    case Result::Timeout:       return "Timeout";
    case Result::QueueFull:     return "QueueFull";
    case Result::Cancelled:     return "Cancelled";
    case Result::ProtocolError: return "ProtocolError";
  }
  return "?";
}

TermLine classifyTermLine(const char* line) {
  if (!line) return TermLine::None;
  // 精确匹配终止行(允许前后空白)
  while (*line == ' ' || *line == '\r') line++;
  if (strncmp(line, "OK", 2) == 0 &&
      (line[2] == '\0' || line[2] == '\r' || line[2] == ' '))
    return TermLine::Ok;
  if (strncmp(line, "ERROR", 5) == 0 &&
      (line[5] == '\0' || line[5] == '\r' || line[5] == ' '))
    return TermLine::Error;
  if (strncmp(line, "+CME ERROR:", 11) == 0)
    return TermLine::CmeError;
  if (strncmp(line, "+CMS ERROR:", 11) == 0)
    return TermLine::CmsError;
  if (line[0] == '>' && (line[1] == '\0' || line[1] == ' '))
    return TermLine::GreaterThan;
  return TermLine::None;
}

bool parseCmeCode(const char* line, int16_t& code) {
  if (!line) return false;
  const char* p = strstr(line, "+CME ERROR:");
  if (!p) return false;
  p += 11;  // skip "+CME ERROR:"
  while (*p == ' ') p++;
  return sscanf(p, "%hd", &code) == 1;
}

bool parseCmsCode(const char* line, int16_t& code) {
  if (!line) return false;
  const char* p = strstr(line, "+CMS ERROR:");
  if (!p) return false;
  p += 11;  // skip "+CMS ERROR:"
  while (*p == ' ') p++;
  return sscanf(p, "%hd", &code) == 1;
}

UrcType classifyUrc(const char* line, UrcInfo& info) {
  if (!line) return UrcType::Unknown;
  // 去掉前导空白和 '\r'
  while (*line == ' ' || *line == '\r') line++;
  if (*line == '\0') return UrcType::Unknown;

  if (strncmp(line, "+CMT:", 5) == 0)
    return UrcType::Cmt;
  if (strncmp(line, "+CMTI:", 6) == 0) {
    // +CMTI: "SM",<index>
    const char* comma = strchr(line, ',');
    if (comma) {
      int idx = 0;
      if (sscanf(comma + 1, "%d", &idx) == 1)
        info.simIndex = (int16_t)idx;
    }
    return UrcType::Cmti;
  }
  if (strncmp(line, "+CDS:", 5) == 0)
    return UrcType::Cds;
  if (strncmp(line, "+CEREG:", 7) == 0) {
    const char* comma = strchr(line, ',');
    if (comma) {
      int stat = 0;
      if (sscanf(comma + 1, "%d", &stat) == 1)
        info.ceregStat = (uint8_t)stat;
    }
    return UrcType::Cereg;
  }
  if (strncmp(line, "+CPIN: READY", 12) == 0 ||
      strncmp(line, "+CPIN: ready", 12) == 0)
    return UrcType::CpinReady;
  if (strcmp(line, "+MATREADY") == 0)
    return UrcType::MatReady;
  if (strncmp(line, "+MIPCALL:", 9) == 0)
    return UrcType::Mipcall;
  return UrcType::Unknown;
}

}  // namespace modem_io
