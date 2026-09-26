#include "GAACEScriptHex.h"

namespace GAACEScript {

static bool hexNibble(char c, uint8_t &out) {
  if (c >= '0' && c <= '9') { out = (uint8_t)(c - '0'); return true; }
  if (c >= 'a' && c <= 'f') { out = (uint8_t)(c - 'a' + 10); return true; }
  if (c >= 'A' && c <= 'F') { out = (uint8_t)(c - 'A' + 10); return true; }
  return false;
}

bool hexDecode(const char *hex, uint8_t *out, uint16_t outCap, uint16_t &outLen) {
  uint16_t n = 0;
  while (hex[n] != '\0') n++;
  if ((n % 2) != 0) return false;

  uint16_t byteCount = n / 2;
  if (byteCount > outCap) return false;

  for (uint16_t i = 0; i < byteCount; i++) {
    uint8_t hi, lo;
    if (!hexNibble(hex[i * 2], hi))     return false;
    if (!hexNibble(hex[i * 2 + 1], lo)) return false;
    out[i] = (uint8_t)((hi << 4) | lo);
  }

  outLen = byteCount;
  return true;
}

} // namespace GAACEScript
