#include "GAACEScript.h"
#include <string.h>  // memcpy() for int32<->float32 bit reinterpretation

namespace GAACEScript {

static inline int32_t readI32(const uint8_t *p) {
  return (int32_t)((uint32_t)p[0] | ((uint32_t)p[1] << 8) |
                    ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24));
}

static inline uint16_t readU16(const uint8_t *p) {
  return (uint16_t)(p[0] | (p[1] << 8));
}

// memcpy-based bit reinterpretation avoids strict-aliasing UB (unlike a
// pointer cast) and compiles down to a no-op register move on every target
// this library runs on (both types are 4 bytes).
static inline float asFloat(int32_t bits) {
  float f;
  memcpy(&f, &bits, sizeof(f));
  return f;
}

static inline int32_t asBits(float f) {
  int32_t bits;
  memcpy(&bits, &f, sizeof(bits));
  return bits;
}

static inline Status push(VM &vm, int32_t v) {
  if (vm.sp >= STACK_SIZE) return VM_ERR_STACK_OVERFLOW;
  vm.stack[vm.sp++] = v;
  return VM_OK;
}

static inline Status pop(VM &vm, int32_t &v) {
  if (vm.sp == 0) return VM_ERR_STACK_UNDERFLOW;
  v = vm.stack[--vm.sp];
  return VM_OK;
}

uint32_t syscallTableHash(const SyscallDesc *table, uint8_t count) {
  uint32_t h = 0x811C9DC5u;
  auto mix = [&h](uint8_t b) { h = (h ^ b) * 0x01000193u; };
  for (uint8_t i = 0; i < count; i++) {
    for (const char *p = table[i].name; *p != '\0'; p++) mix((uint8_t)*p);
    mix(':');
    // argc as decimal digits, no leading zeros (matches Python's str(int))
    char digits[3];
    uint8_t n = 0, v = table[i].argc;
    do { digits[n++] = (char)('0' + v % 10); v /= 10; } while (v != 0);
    while (n > 0) mix((uint8_t)digits[--n]);
    mix(':');
    mix(table[i].returnsFloat ? 'f' : 'i');
    mix(';');
  }
  return h == 0 ? 1u : h;
}

void vmInit(VM &vm, const uint8_t *code, uint16_t codeLen) {
  vm.code = code;
  vm.codeLen = codeLen;
  vm.pc = 0;
  vm.sp = 0;
  vm.paused = false;
  vm.waitRequested = false;
  vm.waitToken = 0;
  for (uint8_t i = 0; i < VAR_SLOTS; i++) vm.vars[i] = 0;
}

uint8_t vmRegisterSyscall(VM &vm, SyscallFn fn) {
  if (vm.syscallCount >= MAX_SYSCALLS) return 0xFF;
  vm.syscalls[vm.syscallCount] = fn;
  return vm.syscallCount++;
}

void vmSetPool(VM &vm, const uint8_t *pool, uint16_t poolLen) {
  vm.pool = pool;
  vm.poolLen = poolLen;
}

void vmSetCmdBridge(VM &vm, CmdBridgeFn fn) {
  vm.cmdBridge = fn;
}

void vmSetDeviceBridge(VM &vm, const DeviceBridge *bridge) {
  vm.deviceBridge = bridge;
}

// The NUL-terminated pool string at `offset`, or nullptr if the offset is
// out of range or the string has no terminator within the pool (a bounded
// scan, so a corrupt pool is reported rather than walked off the end).
static const char *poolString(const VM &vm, uint16_t offset) {
  if (offset >= vm.poolLen) return nullptr;
  uint16_t end = offset;
  while (end < vm.poolLen && vm.pool[end] != '\0') end++;
  if (end >= vm.poolLen) return nullptr;
  return (const char *)&vm.pool[offset];
}

// The VM executing a syscall right now, for vmRequestWait(). vmRun() is not
// re-entrant, so at most one VM is ever mid-syscall.
static VM *currentVm = nullptr;

void vmRequestWait(int32_t token) {
  if (currentVm == nullptr) return;
  currentVm->waitRequested = true;
  currentVm->waitToken     = token;
}

Status vmRun(VM &vm, uint16_t maxSteps) {
  for (uint16_t step = 0; step < maxSteps; step++) {
    if (vm.pc >= vm.codeLen) return VM_ERR_BAD_JUMP;
    uint8_t op = vm.code[vm.pc];

    switch (op) {
      case OP_HALT:
        return VM_HALTED;

      case OP_PUSH_I32: {
        if ((uint32_t)vm.pc + 5 > vm.codeLen) return VM_ERR_BAD_JUMP;
        int32_t v = readI32(&vm.code[vm.pc + 1]);
        Status s = push(vm, v);
        if (s != VM_OK) return s;
        vm.pc += 5;
        break;
      }
      case OP_DUP: {
        if (vm.sp == 0) return VM_ERR_STACK_UNDERFLOW;
        Status s = push(vm, vm.stack[vm.sp - 1]);
        if (s != VM_OK) return s;
        vm.pc += 1;
        break;
      }
      case OP_POP: {
        int32_t v;
        Status s = pop(vm, v);
        if (s != VM_OK) return s;
        vm.pc += 1;
        break;
      }
      case OP_LOAD: {
        if ((uint32_t)vm.pc + 2 > vm.codeLen) return VM_ERR_BAD_JUMP;
        uint8_t slot = vm.code[vm.pc + 1];
        if (slot >= VAR_SLOTS) return VM_ERR_BAD_OPCODE;
        Status s = push(vm, vm.vars[slot]);
        if (s != VM_OK) return s;
        vm.pc += 2;
        break;
      }
      case OP_STORE: {
        if ((uint32_t)vm.pc + 2 > vm.codeLen) return VM_ERR_BAD_JUMP;
        uint8_t slot = vm.code[vm.pc + 1];
        if (slot >= VAR_SLOTS) return VM_ERR_BAD_OPCODE;
        int32_t v;
        Status s = pop(vm, v);
        if (s != VM_OK) return s;
        vm.vars[slot] = v;
        vm.pc += 2;
        break;
      }

      case OP_ADD: case OP_SUB: case OP_MUL: case OP_DIV: case OP_MOD:
      case OP_EQ:  case OP_NE:  case OP_LT:  case OP_LE:
      case OP_GT:  case OP_GE:  case OP_AND: case OP_OR: {
        int32_t a, b;
        Status s = pop(vm, b);
        if (s != VM_OK) return s;
        s = pop(vm, a);
        if (s != VM_OK) return s;
        int32_t r = 0;
        switch (op) {
          case OP_ADD: r = a + b; break;
          case OP_SUB: r = a - b; break;
          case OP_MUL: r = a * b; break;
          case OP_DIV: if (b == 0) return VM_ERR_DIV_ZERO; r = a / b; break;
          case OP_MOD: if (b == 0) return VM_ERR_DIV_ZERO; r = a % b; break;
          case OP_EQ:  r = (a == b); break;
          case OP_NE:  r = (a != b); break;
          case OP_LT:  r = (a < b);  break;
          case OP_LE:  r = (a <= b); break;
          case OP_GT:  r = (a > b);  break;
          case OP_GE:  r = (a >= b); break;
          case OP_AND: r = (a != 0) && (b != 0); break;
          case OP_OR:  r = (a != 0) || (b != 0); break;
        }
        s = push(vm, r);
        if (s != VM_OK) return s;
        vm.pc += 1;
        break;
      }

      case OP_NEG: case OP_NOT: {
        int32_t a;
        Status s = pop(vm, a);
        if (s != VM_OK) return s;
        int32_t r = (op == OP_NEG) ? -a : (a == 0);
        s = push(vm, r);
        if (s != VM_OK) return s;
        vm.pc += 1;
        break;
      }

      case OP_JMP: {
        if ((uint32_t)vm.pc + 3 > vm.codeLen) return VM_ERR_BAD_JUMP;
        uint16_t target = readU16(&vm.code[vm.pc + 1]);
        if (target >= vm.codeLen) return VM_ERR_BAD_JUMP;
        vm.pc = target;
        break;
      }
      case OP_JZ: case OP_JNZ: {
        if ((uint32_t)vm.pc + 3 > vm.codeLen) return VM_ERR_BAD_JUMP;
        int32_t cond;
        Status s = pop(vm, cond);
        if (s != VM_OK) return s;
        uint16_t target = readU16(&vm.code[vm.pc + 1]);
        bool take = (op == OP_JZ) ? (cond == 0) : (cond != 0);
        if (take) {
          if (target >= vm.codeLen) return VM_ERR_BAD_JUMP;
          vm.pc = target;
        } else {
          vm.pc += 3;
        }
        break;
      }

      case OP_PUSH_F32: {
        // Same mechanics as OP_PUSH_I32 -- readI32 just gets the 4 raw
        // bytes; asFloat() below is what actually makes them a float.
        if ((uint32_t)vm.pc + 5 > vm.codeLen) return VM_ERR_BAD_JUMP;
        int32_t bits = readI32(&vm.code[vm.pc + 1]);
        Status s = push(vm, bits);
        if (s != VM_OK) return s;
        vm.pc += 5;
        break;
      }

      case OP_FADD: case OP_FSUB: case OP_FMUL: case OP_FDIV:
      case OP_FEQ:  case OP_FNE:  case OP_FLT:  case OP_FLE:
      case OP_FGT:  case OP_FGE: {
        int32_t aBits, bBits;
        Status s = pop(vm, bBits);
        if (s != VM_OK) return s;
        s = pop(vm, aBits);
        if (s != VM_OK) return s;
        float a = asFloat(aBits), b = asFloat(bBits);
        int32_t r = 0;
        switch (op) {
          case OP_FADD: r = asBits(a + b); break;
          case OP_FSUB: r = asBits(a - b); break;
          case OP_FMUL: r = asBits(a * b); break;
          case OP_FDIV:
            // Consistent with integer DIV: an error, not IEEE inf/nan --
            // one "division by zero is always an error" rule for the whole
            // VM, rather than type-dependent semantics.
            if (b == 0.0f) return VM_ERR_DIV_ZERO;
            r = asBits(a / b);
            break;
          case OP_FEQ: r = (a == b); break;
          case OP_FNE: r = (a != b); break;
          case OP_FLT: r = (a < b);  break;
          case OP_FLE: r = (a <= b); break;
          case OP_FGT: r = (a > b);  break;
          case OP_FGE: r = (a >= b); break;
        }
        s = push(vm, r);
        if (s != VM_OK) return s;
        vm.pc += 1;
        break;
      }

      case OP_FNEG: {
        int32_t aBits;
        Status s = pop(vm, aBits);
        if (s != VM_OK) return s;
        s = push(vm, asBits(-asFloat(aBits)));
        if (s != VM_OK) return s;
        vm.pc += 1;
        break;
      }

      case OP_I2F: {
        int32_t a;
        Status s = pop(vm, a);
        if (s != VM_OK) return s;
        s = push(vm, asBits((float)a));
        if (s != VM_OK) return s;
        vm.pc += 1;
        break;
      }

      case OP_F2I: {
        int32_t aBits;
        Status s = pop(vm, aBits);
        if (s != VM_OK) return s;
        s = push(vm, (int32_t)asFloat(aBits));  // truncates toward zero
        if (s != VM_OK) return s;
        vm.pc += 1;
        break;
      }

      case OP_CALL: {
        if ((uint32_t)vm.pc + 3 > vm.codeLen) return VM_ERR_BAD_JUMP;
        uint8_t id = vm.code[vm.pc + 1];
        uint8_t argc = vm.code[vm.pc + 2];
        if (id >= vm.syscallCount) return VM_ERR_BAD_SYSCALL;
        if (argc > vm.sp) return VM_ERR_STACK_UNDERFLOW;
        int32_t args[STACK_SIZE];
        for (int8_t i = (int8_t)argc - 1; i >= 0; i--) {
          Status s = pop(vm, args[i]);
          if (s != VM_OK) return s;
        }
        vm.waitRequested = false;
        currentVm = &vm;
        int32_t r = vm.syscalls[id](args, argc);
        currentVm = nullptr;
        vm.pc += 3;
        if (vm.waitRequested) {
          // Result comes later via vmResume(); nothing is pushed now.
          vm.waitRequested = false;
          vm.paused = true;
          return VM_WAITING;
        }
        Status s = push(vm, r);
        if (s != VM_OK) return s;
        break;
      }

      case OP_CMDCALL:
      case OP_CMDCALLF:
      case OP_CMDCALLM:
      case OP_CMDCALLMF: {
        const bool masked = (op == OP_CMDCALLM || op == OP_CMDCALLMF);
        const uint8_t instrLen = masked ? 6 : 4;
        if ((uint32_t)vm.pc + instrLen > vm.codeLen) return VM_ERR_BAD_JUMP;
        uint16_t poolOffset = readU16(&vm.code[vm.pc + 1]);
        uint8_t  argc       = vm.code[vm.pc + 3];
        uint16_t floatMask  = masked ? readU16(&vm.code[vm.pc + 4]) : 0;

        if (vm.cmdBridge == nullptr) return VM_ERR_NO_CMD_BRIDGE;
        if (poolOffset >= vm.poolLen) return VM_ERR_BAD_POOL;

        // Bounded scan for the NUL terminator -- never reads past poolLen,
        // so a corrupt/truncated pool is reported, not walked off the end.
        uint16_t end = poolOffset;
        while (end < vm.poolLen && vm.pool[end] != '\0') end++;
        if (end >= vm.poolLen) return VM_ERR_BAD_POOL;  // no terminator found

        if (argc > vm.sp) return VM_ERR_STACK_UNDERFLOW;
        int32_t args[STACK_SIZE];
        for (int8_t i = (int8_t)argc - 1; i >= 0; i--) {
          Status s = pop(vm, args[i]);
          if (s != VM_OK) return s;
        }
        int32_t r = vm.cmdBridge((const char *)&vm.pool[poolOffset], args, argc,
                                (op == OP_CMDCALLF || op == OP_CMDCALLMF),
                                floatMask);
        Status s = push(vm, r);
        if (s != VM_OK) return s;
        vm.pc += instrLen;
        break;
      }

      case OP_DEVICE: {
        if ((uint32_t)vm.pc + 3 > vm.codeLen) return VM_ERR_BAD_JUMP;
        if (vm.deviceBridge == nullptr) return VM_ERR_NO_DEVICE_BRIDGE;
        const char *name = poolString(vm, readU16(&vm.code[vm.pc + 1]));
        if (name == nullptr) return VM_ERR_BAD_POOL;
        Status s = push(vm, vm.deviceBridge->lookup(name));
        if (s != VM_OK) return s;
        vm.pc += 3;
        break;
      }

      case OP_DREADY: {
        if (vm.deviceBridge == nullptr) return VM_ERR_NO_DEVICE_BRIDGE;
        int32_t handle;
        Status s = pop(vm, handle);
        if (s != VM_OK) return s;
        s = push(vm, vm.deviceBridge->ready(handle));
        if (s != VM_OK) return s;
        vm.pc += 1;
        break;
      }

      case OP_DCMDCALL:
      case OP_DCMDCALLF: {
        if ((uint32_t)vm.pc + 6 > vm.codeLen) return VM_ERR_BAD_JUMP;
        if (vm.deviceBridge == nullptr) return VM_ERR_NO_DEVICE_BRIDGE;
        const char *name = poolString(vm, readU16(&vm.code[vm.pc + 1]));
        if (name == nullptr) return VM_ERR_BAD_POOL;
        uint8_t  argc      = vm.code[vm.pc + 3];
        uint16_t floatMask = readU16(&vm.code[vm.pc + 4]);
        if ((uint16_t)argc + 1 > vm.sp) return VM_ERR_STACK_UNDERFLOW;
        int32_t args[STACK_SIZE];
        for (int8_t i = (int8_t)argc - 1; i >= 0; i--) {
          Status s = pop(vm, args[i]);
          if (s != VM_OK) return s;
        }
        int32_t handle;
        Status s = pop(vm, handle);
        if (s != VM_OK) return s;

        vm.waitRequested = false;
        currentVm = &vm;
        int32_t r = vm.deviceBridge->command(handle, name, args, argc,
                                             op == OP_DCMDCALLF, floatMask);
        currentVm = nullptr;
        vm.pc += 6;
        if (vm.waitRequested) {
          vm.waitRequested = false;
          vm.paused = true;
          return VM_WAITING;
        }
        s = push(vm, r);
        if (s != VM_OK) return s;
        break;
      }

      default:
        return VM_ERR_BAD_OPCODE;
    }
  }
  return VM_ERR_STEP_LIMIT;
}

Status vmResume(VM &vm, int32_t result, uint16_t maxSteps) {
  if (!vm.paused) return VM_ERR_BAD_OPCODE;
  vm.paused = false;
  vm.waitToken = 0;
  Status s = push(vm, result);
  if (s != VM_OK) return s;
  return vmRun(vm, maxSteps);
}

} // namespace GAACEScript
