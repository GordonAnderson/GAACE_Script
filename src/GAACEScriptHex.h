// Pure hex encode/decode helpers — no Arduino dependency, so these can be
// unit tested natively even though the runtime module that uses them
// (GAACEScriptRuntime.h) pulls in Thread/commandProcessor and can't be.
#pragma once

#include <stdint.h>

namespace GAACEScript {

// Decodes an even-length ASCII hex string (as sent by `gsc.py --format hex`,
// or SCRIPTLOAD's argument) into raw bytes. Returns false — leaving outLen
// unspecified — on an odd-length string, a non-hex character, or more bytes
// than outCap; true otherwise, with outLen set to the decoded byte count.
bool hexDecode(const char *hex, uint8_t *out, uint16_t outCap, uint16_t &outLen);

} // namespace GAACEScript
