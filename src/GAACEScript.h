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
//   OP_PUSH_I32 <i32>        5 bytes total
//   OP_LOAD/STORE <slot>     2 bytes total
//   OP_JMP/JZ/JNZ <u16 abs>  3 bytes total (absolute jump target, not relative)
//   OP_CALL <id> <argc>      3 bytes total
//   everything else          1 byte (no operand)
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

struct VM {
  int32_t stack[STACK_SIZE];
  int32_t vars[VAR_SLOTS];
  const uint8_t *code = nullptr;
  uint16_t codeLen = 0;
  uint16_t pc = 0;
  uint8_t sp = 0;

  SyscallFn syscalls[MAX_SYSCALLS];
  uint8_t syscallCount = 0;
};

// Resets pc/stack/vars and points the VM at a bytecode buffer. Does not
// touch the registered syscall table, so a VM instance can be reused for
// many scripts against the same syscall set.
void vmInit(VM &vm, const uint8_t *code, uint16_t codeLen);

// Registers a syscall, returning its id (used as the CALL operand), or
// 0xFF if the table is full.
uint8_t vmRegisterSyscall(VM &vm, SyscallFn fn);

// Runs up to maxSteps instructions. Intended to be called from a
// cooperative scheduler (e.g. ArduinoThread) the same way ADCThread runs
// today — a script must be written to finish (or hit HALT) within its
// step budget; VM_ERR_STEP_LIMIT means it didn't, which for a bounded
// control script is a bug in the script, not a VM fault.
Status vmRun(VM &vm, uint16_t maxSteps);

} // namespace GAACEScript
