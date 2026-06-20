#pragma once

namespace modem_policy {

bool hasOkLine(const char* response);
bool cnmiUsesStorageNotifications(const char* response);
bool parseMipCallActive(const char* response, bool& active);

}  // namespace modem_policy
