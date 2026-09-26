#if defined(ARDUINO)

#include "GAACEScriptRuntime.h"
#include "GAACEScriptHex.h"
#include <Errors.h>
#include <stdio.h>

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

ScriptRuntime::ScriptRuntime(commandProcessor *cpArg, ThreadController *tasks,
                              unsigned long defaultIntervalMs) {
  activeInstance = this;
  cp_ = cpArg;
  for (uint8_t i = 0; i < SCRIPT_SLOTS; i++) {
    char name[12];
    snprintf(name, sizeof(name), "Script%u", (unsigned)i);
    slots[i].setName(name);
    slots[i].setInterval(defaultIntervalMs);
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
// SCRIPTLOAD,<slot>,<hex> — decode <hex> into slots[slot].code and vmInit()
// it. NAKs (ERR_BADARG) on a bad slot index, malformed hex (odd length or a
// non-hex character), or a decoded length over SCRIPT_MAX_CODE_LEN.
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

  if (!ok) { cp.sendNAK(ERR_BADARG); return; }

  s.codeLen = decodedLen;
  vmInit(s.vm, s.code, s.codeLen);
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
