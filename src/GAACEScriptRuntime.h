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
//
//   // Describe the project's syscalls once. A syscall's id is its index; the
//   // table is also what the device reports to gsc.py (GSYSCALLS) so scripts
//   // need no hand-written `syscall NAME(...) = ID;` lines. See GAACEScript.h.
//   static const SyscallDesc syscalls[] = {
//     {"read_adc",    sys_read_adc,    0, false},   // id 0
//     {"send_device", sys_send_device, 1, false},   // id 1
//   };
//   scripts.registerSyscalls(syscalls, 2);          // on every slot
//
// (The older scripts.registerSyscall(fn) still works for a project that
// wants numbered, unnamed syscalls, but then has no table to report and no
// hash to check -- scripts must declare their syscalls by hand.)
//
// Each slot is its own named Thread ("Script0".."Script<N-1>") added to the
// supplied ThreadController -- so starting/stopping a script and changing
// its rate are NOT new commands here, they're GAACE_Core's existing
// threadCommands (?TENA,Script0,... / ?TINT,Script0,...) used against these
// threads. The only genuinely new surface is: loading a script into a slot,
// and querying limits/status, which threadCommands has no notion of.
//
// New commands (see GAACEScriptRuntime.cpp for exact behavior):
//   GSYSCALLS                 The syscall table registerSyscalls() was given:
//                             first line "count,hash" (hash is 8 hex digits,
//                             see GAACEScript.h's syscallTableHash()), then
//                             one "index,name,argc,int|float" line per
//                             syscall. NAKs if no table was registered.
//                             gsc.py --upload reads this to compile scripts
//                             against the firmware's real syscalls.
//   SCRIPTLOAD,<slot>,<hex>[,<hash>]
//                             Decode <hex> ([codeLen:u16][code][pool] -- see
//                             gsc.py --scriptload) and load it into <slot>
//                             in one call. NAKs (ERR_BADARG) on a bad slot
//                             index, malformed hex, or a decoded payload
//                             over the per-project GAACE_SCRIPT_MAX_CODE_LEN
//                             limit. Only practical for small scripts -- see
//                             SCRIPTAPPEND below for why, and for anything
//                             bigger.
//   SCRIPTAPPEND,<slot>,<offset>,<hexchunk>
//                             Decode <hexchunk> directly into <slot>'s
//                             buffer at byte <offset>, without finalizing.
//                             Lets a script larger than one command's worth
//                             of hex (see the GAACE_SCRIPT_MAX_CODE_LEN note
//                             below) be assembled across many small calls --
//                             gsc.py --upload does this automatically once a
//                             script is too big for a single SCRIPTLOAD.
//                             Marks the slot not-loaded until SCRIPTCOMMIT.
//   SCRIPTCOMMIT,<slot>,<totalLen>[,<hash>]
//                             Finalize a buffer assembled via one or more
//                             SCRIPTAPPEND calls: validate the
//                             [codeLen:u16][code][pool] layout against
//                             <totalLen>, then load it into <slot> exactly
//                             like SCRIPTLOAD does.
//
//   Optional <hash> on SCRIPTLOAD/SCRIPTCOMMIT: the 8-hex-digit hash of the
//   syscall table the script was compiled against (gsc.py stamps it
//   automatically). The device NAKs the load if it differs from its own
//   table's hash, so a script can never silently run against a different
//   set of syscalls. Omitted = unchecked (the script's explicit syscall
//   ids are trusted, as before). A loaded slot remembers its hash in
//   ScriptSlot::syscallHash (0 = unchecked) so a project that persists
//   slots can re-check it on restore.
//   GSCRIPTLIMITS             slots,maxCodeLen,stackSize,varSlots,maxSyscalls
//                             (maxCodeLen is the payload budget per slot,
//                             i.e. the 2-byte length prefix + code + pool
//                             combined, not code alone -- this is the ceiling
//                             SCRIPTAPPEND/SCRIPTCOMMIT assemble up to, not
//                             SCRIPTLOAD's one-call limit)
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
//   -D GAACE_SCRIPT_MAX_CODE_LEN=<n>   Max payload bytes per slot (default
//                                      128) -- the 2-byte length prefix,
//                                      code, and pool (if the script uses
//                                      cmd()) combined, not code alone. Safe
//                                      to raise generously (it's just RAM):
//                                      this is a per-slot buffer size, not a
//                                      per-command transfer size. NOTE:
//                                      SCRIPTLOAD's hex argument specifically
//                                      is decoded through commandProcessor's
//                                      shared charAllocate scratch arena,
//                                      which is a fixed 512 bytes across the
//                                      whole commandProcessor instance (see
//                                      GAACE_Core's commandProcessor.cpp) --
//                                      a single SCRIPTLOAD call can only ever
//                                      deliver roughly half that in decoded
//                                      bytes, regardless of this setting. Use
//                                      SCRIPTAPPEND/SCRIPTCOMMIT (gsc.py
//                                      --upload does this automatically) to
//                                      actually fill a buffer larger than
//                                      that -- raising GAACE_SCRIPT_MAX_CODE_LEN
//                                      alone does not help SCRIPTLOAD reach
//                                      further, only how much SCRIPTAPPEND
//                                      can ultimately assemble.
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
  // Hash of the syscall table the loaded script was compiled against, as
  // given to SCRIPTLOAD/SCRIPTCOMMIT; 0 = the script was loaded unchecked.
  // A project that persists slots across reboots should save this with the
  // buffer and refuse to restore a script whose hash no longer matches
  // ScriptRuntime::syscallHash() (e.g. after a firmware update).
  uint32_t syscallHash = 0;
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

  // Registers a whole described table on every slot's VM; each syscall's id
  // is its index. Call once, before any registerSyscall(). The array must
  // outlive this object (use a static const array). Returns false -- and
  // registers nothing -- if a table is already registered, any syscall was
  // already registered, `count` is over GAACE_SCRIPT_MAX_SYSCALLS, or an
  // entry has a null name or fn.
  bool registerSyscalls(const SyscallDesc *table, uint8_t count);

  // Hash of the registered table (see syscallTableHash()), or 0 if no table
  // was registered via registerSyscalls().
  uint32_t syscallHash() const { return syscallHash_; }

  CommandList *scriptCmdList(void);

  commandProcessor *cp_;  // used by the file-scope command handlers (see .cpp)

  // State for GSYSCALLS / the optional <hash> check; set by registerSyscalls().
  const SyscallDesc *syscallTable_ = nullptr;
  uint8_t            syscallCount_ = 0;
  uint32_t           syscallHash_  = 0;
};

} // namespace GAACEScript

#endif // defined(ARDUINO)
