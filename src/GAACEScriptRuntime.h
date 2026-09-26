// GAACEScriptRuntime — standard command surface for running GAACE_Script
// scripts inside a GAACE project, following the same shape as GAACE_Core's
// threadCommands (see that module's header for the pattern this mirrors).
//
// Usage
// -----
//   ThreadController         control;
//   commandProcessor         cp;
//   GAACEScript::ScriptRuntime scripts(&cp, &control);
//
//   cp.registerCommands(scripts.scriptCmdList());
//   scripts.registerSyscall(sys_read_adc);    // id 0, on every slot
//   scripts.registerSyscall(sys_send_device); // id 1, on every slot
//
// Each slot is its own named Thread ("Script0".."Script<N-1>") added to the
// supplied ThreadController -- so starting/stopping a script and changing
// its rate are NOT new commands here, they're GAACE_Core's existing
// threadCommands (?TENA,Script0,... / ?TINT,Script0,...) used against these
// threads. The only genuinely new surface is: loading a script into a slot,
// and querying limits/status, which threadCommands has no notion of.
//
// New commands (see GAACEScriptRuntime.cpp for exact behavior):
//   SCRIPTLOAD,<slot>,<hex>   Decode <hex> ([codeLen:u16][code][pool] -- see
//                             gsc.py --scriptload) and load it into <slot>.
//                             NAKs (ERR_BADARG) on a bad slot index,
//                             malformed hex, or a decoded payload over the
//                             per-project GAACE_SCRIPT_MAX_CODE_LEN limit.
//   GSCRIPTLIMITS             slots,maxCodeLen,stackSize,varSlots,maxSyscalls
//                             (maxCodeLen is the SCRIPTLOAD payload budget,
//                             i.e. the 2-byte length prefix + code + pool
//                             combined, not code alone)
//   GSCRIPTST,<slot>          loaded(0|1),lastStatus  (see GAACEScript.h's
//                             Status enum; 1 = VM_HALTED)
//
// cmd() support: every slot gets a working CMDCALL bridge automatically
// (registered in the constructor below) -- a script anywhere can call
// cmd("SOMECOMMAND", args...) against whatever this project's own
// commandProcessor already exposes, with no per-project wiring beyond
// constructing ScriptRuntime itself. See GAACEScript.h (CmdBridgeFn/
// vmSetPool) and GAACE_Core's commandProcessor::executeLine() for how.
//
// Build configuration
// --------------------
//   -D GAACE_SCRIPT_SLOTS=<n>          Number of script slots (default 4).
//   -D GAACE_SCRIPT_MAX_CODE_LEN=<n>   Max SCRIPTLOAD payload bytes per slot
//                                      (default 128) -- the 2-byte length
//                                      prefix, code, and pool (if the
//                                      script uses cmd()) combined, not
//                                      code alone. NOTE: SCRIPTLOAD's hex argument
//                                      is decoded through commandProcessor's
//                                      shared charAllocate scratch arena,
//                                      which is a fixed 512 bytes across the
//                                      whole commandProcessor instance (see
//                                      GAACE_Core's commandProcessor.cpp) --
//                                      raising this well past the default
//                                      risks that one SCRIPTLOAD call (hex
//                                      is 2x the byte count) can no longer
//                                      fit alongside the slot-index argument
//                                      in that arena.
//   -D GAACE_SCRIPT_STACK_SIZE / GAACE_SCRIPT_VAR_SLOTS / GAACE_SCRIPT_MAX_SYSCALLS
//                                      Per-VM limits -- see GAACEScript.h.
//
// Limitations
// -----------
//  - Only one ScriptRuntime instance may exist at a time -- like
//    threadCommands, the CMDfunction callbacks are plain C functions sharing
//    a file-scope static pointer to the one instance.
#pragma once

// Arduino-only: pulls in Thread/ThreadController/commandProcessor, none of
// which exist under `platform = native`. Compiles to nothing there so the
// native test env (which only exercises GAACEScript.h/.cpp and
// GAACEScriptHex.h/.cpp) keeps working without a separate source layout.
#if defined(ARDUINO)

#include "GAACEScript.h"
#include <Thread.h>
#include <ThreadController.h>
#include <commandProcessor.h>

namespace GAACEScript {

#if !defined(GAACE_SCRIPT_SLOTS)
#define GAACE_SCRIPT_SLOTS 4
#endif

#if !defined(GAACE_SCRIPT_MAX_CODE_LEN)
#define GAACE_SCRIPT_MAX_CODE_LEN 128
#endif

static const uint8_t  SCRIPT_SLOTS        = GAACE_SCRIPT_SLOTS;
static const uint16_t SCRIPT_MAX_CODE_LEN = GAACE_SCRIPT_MAX_CODE_LEN;

// One script slot: a VM plus the Thread that periodically runs it. Public
// so a project can reach in for advanced cases (e.g. tuning maxSteps), but
// normal use only needs ScriptRuntime below.
class ScriptSlot : public Thread {
public:
  VM       vm;
  uint8_t  code[SCRIPT_MAX_CODE_LEN];
  uint16_t codeLen = 0;
  bool     loaded  = false;
  Status   lastStatus = VM_OK;
  uint16_t maxSteps   = 500;   // vmRun() step budget per tick

  void run() override;
};

class ScriptRuntime {
public:
  ScriptSlot slots[SCRIPT_SLOTS];

  // Names each slot ("Script0".."Script<N-1>"), sets its default interval,
  // and adds it to *tasks. cp/tasks must outlive this object.
  ScriptRuntime(commandProcessor *cp, ThreadController *tasks,
                unsigned long defaultIntervalMs = 1000);

  // Registers a syscall on every slot's VM (the common case: all slots
  // share one syscall table, so a script can run in any slot). Returns the
  // assigned id (the same id on every slot, since registration order is
  // identical), or 0xFF if any slot's table is full.
  uint8_t registerSyscall(SyscallFn fn);

  CommandList *scriptCmdList(void);

  commandProcessor *cp_;  // used by the file-scope command handlers (see .cpp)
};

} // namespace GAACEScript

#endif // defined(ARDUINO)
