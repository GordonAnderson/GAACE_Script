#if defined(ARDUINO)

#include "GAACEScriptRuntime.h"
#include "GAACEScriptHex.h"
#include <Errors.h>
#include <stdio.h>
#include <stdlib.h>  // atoi(), strtoul()
#include <string.h>  // memcpy(), strlen()

namespace GAACEScript {

void ScriptSlot::run() {
  runned();  // required by Thread's contract when overriding run() (see Thread.h)
  if (!loaded) return;
  vm.pc = 0;
  vm.sp = 0;  // rerun without vmInit(): var-declared slots persist across ticks
  lastStatus = vmRun(vm, maxSteps);
}

// Only one ScriptRuntime instance may exist at a time -- see the header's
// Limitations note (same convention threadCommands uses).
static ScriptRuntime *activeInstance = nullptr;

// -----------------------------------------------------------------------
// cmd() bridge -- lets any script in any slot call into the project's own
// commandProcessor by name (see GAACEScript.h's CMDCALL/CmdBridgeFn docs
// and tools/README.md's cmd() language section). Builds "NAME,arg0,arg1"
// and runs it through executeLine() (GAACE_Core), which never touches the
// shared ring buffer/output stream a real command might be mid-arriving
// on -- see that method's own doc comment for why that isolation matters.
//
// Return value: the response parsed as a number on ACK (an int for cmd(), a
// float32 bit-cast into the int32 for cmdf()), 0 on NAK or a response with
// no trailing number (e.g. a plain action command). NAK and
// "ACK with value 0" are not distinguishable from the return value alone.
// -----------------------------------------------------------------------
static int32_t cmdBridge(const char *name, int32_t *args, uint8_t argc, bool wantFloat) {
  commandProcessor &cp = *activeInstance->cp_;

  char line[64];
  int len = snprintf(line, sizeof(line), "%s", name);
  for (uint8_t i = 0; i < argc && len < (int)sizeof(line) - 1; i++) {
    len += snprintf(line + len, sizeof(line) - (size_t)len, ",%d", (int)args[i]);
  }

  char response[48];
  cp.executeLine(line, response, sizeof(response));

  if ((uint8_t)response[0] != 0x06) return 0;  // NAK (0 / 0.0f both read as 0 bits)
  if (wantFloat) {
    float f = (float)atof(response + 1);
    int32_t bits;
    memcpy(&bits, &f, sizeof(bits));
    return bits;
  }
  return (int32_t)atoi(response + 1);
}

ScriptRuntime::ScriptRuntime(commandProcessor *cpArg, ThreadController *tasks,
                              unsigned long defaultIntervalMs) {
  activeInstance = this;
  cp_ = cpArg;
  for (uint8_t i = 0; i < SCRIPT_SLOTS; i++) {
    char name[12];
    snprintf(name, sizeof(name), "Script%u", (unsigned)i);
    slots[i].setName(name);
    slots[i].setInterval(defaultIntervalMs);
    vmSetCmdBridge(slots[i].vm, cmdBridge);  // cmd() works in every slot, free
    tasks->add(&slots[i]);
  }
}

uint8_t ScriptRuntime::registerSyscall(SyscallFn fn) {
  uint8_t id = 0xFF;
  for (uint8_t i = 0; i < SCRIPT_SLOTS; i++) {
    id = vmRegisterSyscall(slots[i].vm, fn);
  }
  return id;
}

bool ScriptRuntime::registerSyscalls(const SyscallDesc *table, uint8_t count) {
  if (table == nullptr || syscallTable_ != nullptr) return false;
  if (count > MAX_SYSCALLS) return false;
  if (slots[0].vm.syscallCount != 0) return false;   // ids must start at 0
  for (uint8_t i = 0; i < count; i++) {
    if (table[i].name == nullptr || table[i].fn == nullptr) return false;
  }
  for (uint8_t i = 0; i < count; i++) {
    for (uint8_t s = 0; s < SCRIPT_SLOTS; s++) vmRegisterSyscall(slots[s].vm, table[i].fn);
  }
  syscallTable_ = table;
  syscallCount_ = count;
  syscallHash_  = syscallTableHash(table, count);
  return true;
}

// Reads the optional trailing <hash> argument of SCRIPTLOAD/SCRIPTCOMMIT
// (8 hex digits). `present` is whether the command line carries one; with
// none, `hash` stays 0 (= unchecked) and this succeeds. Returns false (the
// caller NAKs) for a malformed hash, or one that doesn't match this
// firmware's registered table -- including any hash at all when no table
// was registered, since then there is nothing to check it against.
static bool readSyscallHash(bool present, uint32_t &hash) {
  hash = 0;
  if (!present) return true;

  commandProcessor &cp = *activeInstance->cp_;
  char *tok;
  if (!cp.getValue(&tok)) return false;
  char *end = nullptr;
  unsigned long v = strtoul(tok, &end, 16);
  bool wellFormed = (end != tok) && (*end == '\0') && strlen(tok) <= 8 && v != 0;
  cp.ca->free(tok);

  if (!wellFormed) return false;
  if (activeInstance->syscallHash_ == 0 || (uint32_t)v != activeInstance->syscallHash_)
    return false;
  hash = (uint32_t)v;
  return true;
}

// -----------------------------------------------------------------------
// SCRIPTLOAD,<slot>,<hex>[,<hash>] — decode <hex> into slots[slot].code and load it.
// <hex> carries [codeLen:u16 LE][code bytes][pool bytes] -- pool is
// whatever's left after codeLen code bytes, empty for a script that
// doesn't use cmd() (see gsc.py --scriptload, which produces exactly this
// layout). NAKs (ERR_BADARG) on a bad slot index, malformed hex, a
// decoded length over SCRIPT_MAX_CODE_LEN, or a codeLen prefix that
// doesn't fit within what was actually decoded. The optional <hash> is
// checked after the hex is decoded and freed (not held alongside it) so a
// maximum-size hex argument still fits the 512-byte scratch arena.
// -----------------------------------------------------------------------
static void cmdScriptLoad(void) {
  commandProcessor &cp = *activeInstance->cp_;
  int numArgs = cp.getNumArgs();
  if (numArgs != 2 && numArgs != 3) { cp.sendNAK(ERR_BADARG); return; }
  bool hasHash = (numArgs == 3);

  // NOT cp.getValue(&slot, 0, SCRIPT_SLOTS - 1): commandProcessor treats
  // ll == 0 && ul == 0 as "no range check" (see its getValue doc comment),
  // which would silently disable bounds checking whenever SCRIPT_SLOTS == 1.
  int slot;
  if (!cp.getValue(&slot) || slot < 0 || slot >= SCRIPT_SLOTS) {
    cp.sendNAK(ERR_BADARG);
    return;
  }

  char *hex;
  if (!cp.getValue(&hex)) { cp.sendNAK(ERR_BADARG); return; }

  ScriptSlot &s = activeInstance->slots[slot];
  // The decode below overwrites the slot's buffer, and a later check (layout,
  // syscall hash) can still reject the load -- so, like SCRIPTAPPEND, a slot
  // is not runnable from here until this load actually succeeds.
  s.loaded = false;
  uint16_t decodedLen = 0;
  bool ok = hexDecode(hex, s.code, SCRIPT_MAX_CODE_LEN, decodedLen);
  cp.ca->free(hex);

  if (!ok || decodedLen < 2) { cp.sendNAK(ERR_BADARG); return; }

  uint16_t codeLen = (uint16_t)((uint8_t)s.code[0] | ((uint8_t)s.code[1] << 8));
  if (codeLen > decodedLen - 2) { cp.sendNAK(ERR_BADARG); return; }
  uint16_t poolLen = (uint16_t)(decodedLen - 2 - codeLen);

  uint32_t hash;
  if (!readSyscallHash(hasHash, hash)) { cp.sendNAK(ERR_BADARG); return; }

  s.codeLen = codeLen;
  vmInit(s.vm, s.code + 2, codeLen);
  // Always called, even for poolLen 0: a slot being reloaded with a script
  // that has no pool must not keep pointing at a previous script's pool.
  vmSetPool(s.vm, s.code + 2 + codeLen, poolLen);
  s.syscallHash = hash;
  s.loaded = true;
  s.lastStatus = VM_OK;
  cp.sendACK();
}

// -----------------------------------------------------------------------
// SCRIPTAPPEND,<slot>,<offset>,<hexchunk> — write a decoded hex chunk
// directly into slot <slot>'s raw payload buffer at byte offset <offset>,
// without touching vmInit/vmSetPool. Call SCRIPTCOMMIT once every chunk has
// been written.
//
// Why this exists: SCRIPTLOAD's <hex> argument is decoded through
// commandProcessor's shared 512-byte charAllocate scratch arena (see this
// file's header for the full explanation), so one SCRIPTLOAD call can only
// ever deliver roughly its half in decoded bytes -- raising
// GAACE_SCRIPT_MAX_CODE_LEN past that does not help, because the payload
// can never arrive in one piece regardless of how big the destination
// buffer is. SCRIPTAPPEND sidesteps this entirely: each call's hex chunk is
// small (the caller's choice -- gsc.py --upload defaults to 200 decoded
// bytes), so no single call goes anywhere near the arena's ceiling no
// matter how large the total script is. A slot's buffer is only bounded by
// GAACE_SCRIPT_MAX_CODE_LEN, which is just RAM and can be set generously.
//
// Marks the slot not-loaded for the duration: a buffer mid-assembly must
// never be treated as runnable, even if a previous script happens to still
// be sitting in the same slot's memory from before this upload started.
// -----------------------------------------------------------------------
static void cmdScriptAppend(void) {
  commandProcessor &cp = *activeInstance->cp_;
  if (!cp.checkExpectedArgs(3)) return;

  // See cmdScriptLoad()/cmdScriptStatus() above for why these aren't
  // cp.getValue(&x, 0, upperBound) -- same ll==0&&ul==0 "no range check"
  // footgun when an upper bound can legitimately be 0.
  int slot;
  if (!cp.getValue(&slot) || slot < 0 || slot >= SCRIPT_SLOTS) {
    cp.sendNAK(ERR_BADARG);
    return;
  }

  int offset;
  if (!cp.getValue(&offset) || offset < 0 || offset >= (int)SCRIPT_MAX_CODE_LEN) {
    cp.sendNAK(ERR_BADARG);
    return;
  }

  char *hex;
  if (!cp.getValue(&hex)) { cp.sendNAK(ERR_BADARG); return; }

  ScriptSlot &s = activeInstance->slots[slot];
  s.loaded = false;   // a partially-written buffer must never be runnable

  uint16_t decodedLen = 0;
  bool ok = hexDecode(hex, s.code + offset,
                       (uint16_t)(SCRIPT_MAX_CODE_LEN - offset), decodedLen);
  cp.ca->free(hex);

  if (!ok) { cp.sendNAK(ERR_BADARG); return; }
  cp.sendACK();
}

// -----------------------------------------------------------------------
// SCRIPTCOMMIT,<slot>,<totalLen>[,<hash>] — finalize a buffer assembled via one or
// more SCRIPTAPPEND calls: validates the [codeLen:u16][code][pool] layout
// against <totalLen> exactly like SCRIPTLOAD does against its decoded
// length, then vmInit()/vmSetPool() and marks the slot loaded. <totalLen>
// is the exact byte count the caller wrote via SCRIPTAPPEND (not a hex
// character count) -- gsc.py --upload tracks and sends this itself.
// -----------------------------------------------------------------------
static void cmdScriptCommit(void) {
  commandProcessor &cp = *activeInstance->cp_;
  int numArgs = cp.getNumArgs();
  if (numArgs != 2 && numArgs != 3) { cp.sendNAK(ERR_BADARG); return; }
  bool hasHash = (numArgs == 3);

  int slot;
  if (!cp.getValue(&slot) || slot < 0 || slot >= SCRIPT_SLOTS) {
    cp.sendNAK(ERR_BADARG);
    return;
  }

  int totalLen;
  if (!cp.getValue(&totalLen) || totalLen < 2 || totalLen > (int)SCRIPT_MAX_CODE_LEN) {
    cp.sendNAK(ERR_BADARG);
    return;
  }

  ScriptSlot &s = activeInstance->slots[slot];

  uint16_t codeLen = (uint16_t)((uint8_t)s.code[0] | ((uint8_t)s.code[1] << 8));
  if (codeLen > (uint16_t)totalLen - 2) { cp.sendNAK(ERR_BADARG); return; }
  uint16_t poolLen = (uint16_t)((uint16_t)totalLen - 2 - codeLen);

  uint32_t hash;
  if (!readSyscallHash(hasHash, hash)) { cp.sendNAK(ERR_BADARG); return; }

  s.codeLen = codeLen;
  vmInit(s.vm, s.code + 2, codeLen);
  // Always called, even for poolLen 0 -- see cmdScriptLoad()'s identical note.
  vmSetPool(s.vm, s.code + 2 + codeLen, poolLen);
  s.syscallHash = hash;
  s.loaded = true;
  s.lastStatus = VM_OK;
  cp.sendACK();
}

// -----------------------------------------------------------------------
// GSCRIPTLIMITS — slots,maxCodeLen,stackSize,varSlots,maxSyscalls
// -----------------------------------------------------------------------
static void cmdScriptLimits(void) {
  commandProcessor &cp = *activeInstance->cp_;
  if (!cp.checkExpectedArgs(0)) return;
  cp.sendACK(false);
  cp.print((int)SCRIPT_SLOTS);
  cp.print(",");
  cp.print((int)SCRIPT_MAX_CODE_LEN);
  cp.print(",");
  cp.print((int)STACK_SIZE);
  cp.print(",");
  cp.print((int)VAR_SLOTS);
  cp.print(",");
  cp.println((int)MAX_SYSCALLS);
}

// -----------------------------------------------------------------------
// GSYSCALLS — the registered syscall table: "count,hash" (hash as 8 hex
// digits), then "index,name,argc,int|float" per syscall, one per line.
// NAKs (ERR_CMD) if the project never called registerSyscalls().
// -----------------------------------------------------------------------
static void cmdSyscalls(void) {
  commandProcessor &cp = *activeInstance->cp_;
  if (!cp.checkExpectedArgs(0)) return;
  if (activeInstance->syscallTable_ == nullptr) { cp.sendNAK(ERR_CMD); return; }

  cp.sendACK(false);
  char hashText[12];
  snprintf(hashText, sizeof(hashText), "%08lX", (unsigned long)activeInstance->syscallHash_);
  cp.print((int)activeInstance->syscallCount_);
  cp.print(",");
  cp.println(hashText);
  for (uint8_t i = 0; i < activeInstance->syscallCount_; i++) {
    const SyscallDesc &d = activeInstance->syscallTable_[i];
    cp.print((int)i);
    cp.print(",");
    cp.print(d.name);
    cp.print(",");
    cp.print((int)d.argc);
    cp.print(",");
    cp.println(d.returnsFloat ? "float" : "int");
  }
}

// -----------------------------------------------------------------------
// GSCRIPTST,<slot> — loaded(0|1),lastStatus
// -----------------------------------------------------------------------
static void cmdScriptStatus(void) {
  commandProcessor &cp = *activeInstance->cp_;
  if (!cp.checkExpectedArgs(1)) return;

  // See cmdScriptLoad() above for why this isn't cp.getValue(&slot, 0, ...).
  int slot;
  if (!cp.getValue(&slot) || slot < 0 || slot >= SCRIPT_SLOTS) {
    cp.sendNAK(ERR_BADARG);
    return;
  }

  ScriptSlot &s = activeInstance->slots[slot];
  cp.sendACK(false);
  cp.print((bool)s.loaded);
  cp.print(",");
  cp.println((int)s.lastStatus);
}

static Command scriptCmds[] = {
  {"SCRIPTLOAD",   CMDfunction, -1, (void *)cmdScriptLoad,   NULL,
   "SCRIPTLOAD,<slot>,<hex>[,<hash>] -- load a compiled script into a slot"},
  {"SCRIPTAPPEND", CMDfunction, 3, (void *)cmdScriptAppend, NULL,
   "SCRIPTAPPEND,<slot>,<offset>,<hexchunk> -- write a chunk into a slot's buffer"},
  {"SCRIPTCOMMIT", CMDfunction, -1, (void *)cmdScriptCommit, NULL,
   "SCRIPTCOMMIT,<slot>,<totalLen>[,<hash>] -- finalize a buffer assembled via SCRIPTAPPEND"},
  {"GSYSCALLS",     CMDfunction, 0, (void *)cmdSyscalls,      NULL,
   "count,hash then index,name,argc,int|float per syscall"},
  {"GSCRIPTLIMITS", CMDfunction, 0, (void *)cmdScriptLimits, NULL,
   "slots,maxCodeLen,stackSize,varSlots,maxSyscalls"},
  {"GSCRIPTST",     CMDfunction, 1, (void *)cmdScriptStatus, NULL,
   "GSCRIPTST,<slot> -- loaded(0|1),lastStatus"},
  {NULL}
};
static CommandList scriptCmdListNode = {scriptCmds, NULL};

CommandList *ScriptRuntime::scriptCmdList(void) {
  return &scriptCmdListNode;
}

} // namespace GAACEScript

#endif // defined(ARDUINO)
