// GAACE_Script — a minimal stack-based bytecode VM for GAACE firmware projects.
//
// Design goals (see USBrepeater/TODO.md for the discussion this came from):
//   - No heap, no garbage collector, no dynamic allocation. A VM instance is
//     a fixed-size struct; total footprint is well under 1 KB.
//   - The core has zero hardware-specific code (no Serial, no board headers)
//     so it can be reused unmodified across GAACE projects on different MCU
//     families (Teensy today, SAM-based boards later) — each embedding
//     project supplies its own syscall table, nothing else changes.
//   - `if`/`while`-style control flow is not built in; a host-side compiler
//     lowers it to plain conditional/unconditional jumps, same as any real
//     compiler's control-flow lowering.
#pragma once

#include <stdint.h>

namespace GAACEScript {

// ---------------------------------------------------------------------------
// Bytecode format: 1 opcode byte, followed by 0+ operand bytes (little-endian).
//   OP_PUSH_I32 / OP_PUSH_F32 <4 bytes>  5 bytes total
//   OP_LOAD/STORE <slot>                 2 bytes total
//   OP_JMP/JZ/JNZ <u16 abs>              3 bytes total (absolute, not relative)
//   OP_CALL <id> <argc>                   3 bytes total
//   OP_CMDCALL  <poolOffset:u16> <argc>   4 bytes total
//   OP_CMDCALLF <poolOffset:u16> <argc>   4 bytes total (float-returning)
//   OP_CMDCALLM  <poolOffset:u16> <argc> <floatMask:u16>   6 bytes total
//   OP_CMDCALLMF <poolOffset:u16> <argc> <floatMask:u16>   6 bytes total
//       (same as CMDCALL/CMDCALLF, but bit i of floatMask set => argument i
//       is a float32 and the bridge formats it as a decimal number, not as
//       its raw bit pattern. The compiler emits these only when a call has
//       at least one float argument; all-int calls keep the 4-byte form.)
//   everything else                      1 byte (no operand)
//
// Floats: a stack/variable slot is just 4 bytes with no type tag anywhere --
// OP_PUSH_F32's operand bytes are the IEEE754 bit pattern of the constant,
// and F-prefixed opcodes (FADD, FLT, ...) reinterpret whatever bits are on
// the stack as float32 rather than int32. Nothing else about the VM changes:
// it's the opcode, not the value, that carries the type. The compiler
// (gsc.py) is responsible for emitting the right opcode for each operand's
// static type; the VM itself does no runtime type checking.
//
// CMDCALL: lets a script invoke a literal, human-readable command name
// (e.g. `cmd("SADCPIN", pin);`) against whatever command surface the
// embedding project already exposes to a PC/human -- rather than a fixed
// set of hand-written syscalls, a script gets access to anything already
// registered with the project's command processor. The name lives in a
// read-only string-constant pool compiled alongside the code (see `pool`/
// `poolLen` below and vmSetPool()); poolOffset is a byte offset to a
// NUL-terminated string within it. The VM core still has zero
// hardware-specific code -- it hands (name, args, argc) to a single
// registered CmdBridgeFn and pushes back whatever int32 comes out. See
// GAACEScriptRuntime.h for the real bridge (into commandProcessor).
// ---------------------------------------------------------------------------
enum Opcode : uint8_t {
  OP_HALT = 0x00,
  OP_PUSH_I32,
  OP_DUP,
  OP_POP,
  OP_LOAD,
  OP_STORE,
  OP_ADD,
  OP_SUB,
  OP_MUL,
  OP_DIV,
  OP_MOD,
  OP_NEG,
  OP_EQ,
  OP_NE,
  OP_LT,
  OP_LE,
  OP_GT,
  OP_GE,
  OP_AND,
  OP_OR,
  OP_NOT,
  OP_JMP,
  OP_JZ,
  OP_JNZ,
  OP_CALL,
  OP_PUSH_F32,
  OP_FADD,
  OP_FSUB,
  OP_FMUL,
  OP_FDIV,
  OP_FNEG,
  OP_FEQ,
  OP_FNE,
  OP_FLT,
  OP_FLE,
  OP_FGT,
  OP_FGE,
  OP_I2F,   // pop int32, push its float32 conversion
  OP_F2I,   // pop float32, push its int32 truncation (toward zero)
  OP_CMDCALL,
  OP_CMDCALLF,  // same as CMDCALL, but asks the bridge for a float32 result
  OP_CMDCALLM,  // CMDCALL with a float-argument mask (see header comment)
  OP_CMDCALLMF, // CMDCALLF with a float-argument mask
};

enum Status : uint8_t {
  VM_OK = 0,               // step limit not yet reached, still running (internal use)
  VM_HALTED,               // ran to completion (OP_HALT)
  VM_ERR_STACK_OVERFLOW,
  VM_ERR_STACK_UNDERFLOW,
  VM_ERR_BAD_OPCODE,
  VM_ERR_BAD_JUMP,         // jump target or operand read past end of code
  VM_ERR_BAD_SYSCALL,      // unregistered syscall id
  VM_ERR_DIV_ZERO,
  VM_ERR_STEP_LIMIT,       // maxSteps reached without halting — script did not
                           // finish in this call; embedding project decides
                           // whether that's an error or expected (see below)
  VM_ERR_BAD_POOL,         // CMDCALL's poolOffset is out of range, or the
                           // string at that offset isn't NUL-terminated
                           // within the pool
  VM_ERR_NO_CMD_BRIDGE,    // CMDCALL used but vmSetCmdBridge() was never called
};

// Overridable per project (e.g. -D GAACE_SCRIPT_STACK_SIZE=64 in
// platformio.ini) so a resource-rich board can run bigger/more complex
// scripts than a tight one, without forking the VM. Defaults match the
// original fixed values.
#if !defined(GAACE_SCRIPT_STACK_SIZE)
#define GAACE_SCRIPT_STACK_SIZE 32
#endif
#if !defined(GAACE_SCRIPT_VAR_SLOTS)
#define GAACE_SCRIPT_VAR_SLOTS 16
#endif
#if !defined(GAACE_SCRIPT_MAX_SYSCALLS)
#define GAACE_SCRIPT_MAX_SYSCALLS 32
#endif

static const uint8_t STACK_SIZE   = GAACE_SCRIPT_STACK_SIZE;
static const uint8_t VAR_SLOTS    = GAACE_SCRIPT_VAR_SLOTS;
static const uint8_t MAX_SYSCALLS = GAACE_SCRIPT_MAX_SYSCALLS;

// A syscall receives its arguments in evaluation order (args[0] is the first
// argument pushed) and returns one int32_t that gets pushed back.
typedef int32_t (*SyscallFn)(int32_t *args, uint8_t argc);

// The CMDCALL bridge: `name` is a NUL-terminated string from the VM's pool
// (e.g. "SADCPIN"); args/argc are the script's arguments, same convention
// as SyscallFn. Exactly one bridge per VM (not a table -- there's only ever
// one "the command processor" to reach), registered with vmSetCmdBridge().
//
// `wantFloat` is true for OP_CMDCALLF (script wrote `cmdf(...)`): the bridge
// must then return the response parsed as a float32, bit-cast into the
// int32_t return (the same convention syscalls use for `: float`). For plain
// CMDCALL it is false and the bridge returns an ordinary int32.
//
// `floatMask`: bit i set means args[i] holds a float32's bit pattern (the
// script passed a float expression) and must be formatted as a number, not
// an integer. 0 for plain CMDCALL/CMDCALLF.
typedef int32_t (*CmdBridgeFn)(const char *name, int32_t *args, uint8_t argc,
                               bool wantFloat, uint16_t floatMask);

// ---------------------------------------------------------------------------
// Syscall descriptor table
//
// A project describes its syscalls once, in one array, instead of hand-
// numbering registerSyscall() calls and mirroring the order in every script:
//
//   static const SyscallDesc syscalls[] = {
//     {"read_adc",   sys_read_adc,   0, false},   // id 0
//     {"set_volts",  sys_set_volts,  1, false},   // id 1
//     {"get_volts",  sys_get_volts,  1, true },   // id 2 (returns float)
//   };
//   scripts.registerSyscalls(syscalls, 3);
//
// A syscall's id is its index in the array. The table is what the device
// reports to the host (GSYSCALLS) so gsc.py can compile a script against the
// firmware's real syscalls, and its hash (below) is what lets the device
// refuse a script compiled against a different table.
// ---------------------------------------------------------------------------
struct SyscallDesc {
  const char *name;      // identifier a script calls it by
  SyscallFn   fn;
  uint8_t     argc;      // number of arguments the syscall takes
  bool        returnsFloat;  // true: fn's return is a float32 bit pattern
};

// FNV-1a (32-bit) over "name:argc:i|f;" for each entry in order, so the hash
// changes if a syscall is added, removed, renamed, reordered, or has its
// argument count or return type changed -- anything that would make a
// previously compiled script call the wrong thing. Never returns 0 (0 means
// "not checked" wherever a hash is optional). tools/gsc.py computes the same
// value; both are pinned to a shared test vector.
uint32_t syscallTableHash(const SyscallDesc *table, uint8_t count);

struct VM {
  int32_t stack[STACK_SIZE];
  int32_t vars[VAR_SLOTS];
  const uint8_t *code = nullptr;
  uint16_t codeLen = 0;
  uint16_t pc = 0;
  uint8_t sp = 0;

  SyscallFn syscalls[MAX_SYSCALLS];
  uint8_t syscallCount = 0;

  const uint8_t *pool = nullptr;
  uint16_t poolLen = 0;
  CmdBridgeFn cmdBridge = nullptr;
};

// Resets pc/stack/vars and points the VM at a bytecode buffer. Does not
// touch the registered syscall table, the pool, or the command bridge, so
// a VM instance can be reused for many scripts against the same syscall
// set -- but see vmSetPool()'s note about reloading when scripts differ.
void vmInit(VM &vm, const uint8_t *code, uint16_t codeLen);

// Registers a syscall, returning its id (used as the CALL operand), or
// 0xFF if the table is full.
uint8_t vmRegisterSyscall(VM &vm, SyscallFn fn);

// Points the VM at a script's string-constant pool (for CMDCALL). Like
// vmInit(), does not allocate or copy -- `pool` must outlive its use.
// Call this every time a *different* script is loaded into a reused VM
// instance, even with poolLen 0, so a stale pointer from a previous
// script's pool can't be read through this one's (possibly different)
// CMDCALL offsets.
void vmSetPool(VM &vm, const uint8_t *pool, uint16_t poolLen);

// Registers the single CMDCALL bridge for this VM instance (see
// CmdBridgeFn above). Not touched by vmInit()/vmSetPool().
void vmSetCmdBridge(VM &vm, CmdBridgeFn fn);

// Runs up to maxSteps instructions. Intended to be called from a
// cooperative scheduler (e.g. ArduinoThread) the same way ADCThread runs
// today — a script must be written to finish (or hit HALT) within its
// step budget; VM_ERR_STEP_LIMIT means it didn't, which for a bounded
// control script is a bug in the script, not a VM fault.
Status vmRun(VM &vm, uint16_t maxSteps);

} // namespace GAACEScript
