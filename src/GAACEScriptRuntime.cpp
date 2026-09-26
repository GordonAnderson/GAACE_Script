#if defined(ARDUINO)

#include "GAACEScriptRuntime.h"
#include "GAACEScriptHex.h"
#include <Errors.h>
#include <stdio.h>
#include <stdlib.h>  // atoi()

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
// Return value: the response parsed as a number on ACK, 0 on NAK or a
// response with no trailing number (e.g. a plain action command). NAK and
// "ACK with value 0" are not distinguishable from the return value alone.
// -----------------------------------------------------------------------
static int32_t cmdBridge(const char *name, int32_t *args, uint8_t argc) {
  commandProcessor &cp = *activeInstance->cp_;

  char line[64];
  int len = snprintf(line, sizeof(line), "%s", name);
  for (uint8_t i = 0; i < argc && len < (int)sizeof(line) - 1; i++) {
    len += snprintf(line + len, sizeof(line) - (size_t)len, ",%d", (int)args[i]);
  }

  char response[48];
  cp.executeLine(line, response, sizeof(response));

  if ((uint8_t)response[0] == 0x06) return (int32_t)atoi(response + 1);
  return 0;
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

// -----------------------------------------------------------------------
// SCRIPTLOAD,<slot>,<hex> — decode <hex> into slots[slot].code and load it.
// <hex> carries [codeLen:u16 LE][code bytes][pool bytes] -- pool is
// whatever's left after codeLen code bytes, empty for a script that
// doesn't use cmd() (see gsc.py --scriptload, which produces exactly this
// layout). NAKs (ERR_BADARG) on a bad slot index, malformed hex, a
// decoded length over SCRIPT_MAX_CODE_LEN, or a codeLen prefix that
// doesn't fit within what was actually decoded.
// -----------------------------------------------------------------------
static void cmdScriptLoad(void) {
  commandProcessor &cp = *activeInstance->cp_;
  if (!cp.checkExpectedArgs(2)) return;

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
  uint16_t decodedLen = 0;
  bool ok = hexDecode(hex, s.code, SCRIPT_MAX_CODE_LEN, decodedLen);
  cp.ca->free(hex);

  if (!ok || decodedLen < 2) { cp.sendNAK(ERR_BADARG); return; }

  uint16_t codeLen = (uint16_t)((uint8_t)s.code[0] | ((uint8_t)s.code[1] << 8));
  if (codeLen > decodedLen - 2) { cp.sendNAK(ERR_BADARG); return; }
  uint16_t poolLen = (uint16_t)(decodedLen - 2 - codeLen);

  s.codeLen = codeLen;
  vmInit(s.vm, s.code + 2, codeLen);
  // Always called, even for poolLen 0: a slot being reloaded with a script
  // that has no pool must not keep pointing at a previous script's pool.
  vmSetPool(s.vm, s.code + 2 + codeLen, poolLen);
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
  {"SCRIPTLOAD",   CMDfunction, 2, (void *)cmdScriptLoad,   NULL,
   "SCRIPTLOAD,<slot>,<hex> -- load a compiled script into a slot"},
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
