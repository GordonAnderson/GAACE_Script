// Native unit tests for the GAACE_Script VM core.
// Run with: pio test -e native  (no hardware required)
#include <unity.h>
#include <string.h>
#include "GAACEScript.h"

using namespace GAACEScript;

static int32_t f32bits(float f) {
  int32_t bits;
  memcpy(&bits, &f, sizeof(bits));
  return bits;
}

static float bitsF32(int32_t bits) {
  float f;
  memcpy(&f, &bits, sizeof(f));
  return f;
}

// Tiny hand-assembler used only by these tests. A real host-side compiler
// would do this from readable script source; these tests write bytecode
// directly to exercise the VM itself.
struct Asm {
  uint8_t buf[128];
  uint16_t len = 0;

  void op(uint8_t o) { buf[len++] = o; }
  void pushI32(int32_t v) {
    buf[len++] = OP_PUSH_I32;
    buf[len++] = (uint8_t)(v & 0xFF);
    buf[len++] = (uint8_t)((v >> 8) & 0xFF);
    buf[len++] = (uint8_t)((v >> 16) & 0xFF);
    buf[len++] = (uint8_t)((v >> 24) & 0xFF);
  }
  void pushF32(float f) {
    int32_t bits;
    memcpy(&bits, &f, sizeof(bits));
    buf[len++] = OP_PUSH_F32;
    buf[len++] = (uint8_t)(bits & 0xFF);
    buf[len++] = (uint8_t)((bits >> 8) & 0xFF);
    buf[len++] = (uint8_t)((bits >> 16) & 0xFF);
    buf[len++] = (uint8_t)((bits >> 24) & 0xFF);
  }
  void slotOp(uint8_t o, uint8_t slot) { buf[len++] = o; buf[len++] = slot; }
  uint16_t jumpPlaceholder(uint8_t o) {
    buf[len++] = o;
    uint16_t pos = len;
    buf[len++] = 0; buf[len++] = 0;
    return pos;
  }
  void patch(uint16_t pos, uint16_t target) {
    buf[pos] = (uint8_t)(target & 0xFF);
    buf[pos + 1] = (uint8_t)((target >> 8) & 0xFF);
  }
  void call(uint8_t id, uint8_t argc) {
    buf[len++] = OP_CALL; buf[len++] = id; buf[len++] = argc;
  }
  void cmdCall(uint16_t poolOffset, uint8_t argc) {
    buf[len++] = OP_CMDCALL;
    buf[len++] = (uint8_t)(poolOffset & 0xFF);
    buf[len++] = (uint8_t)((poolOffset >> 8) & 0xFF);
    buf[len++] = argc;
  }
};

void setUp(void) {}
void tearDown(void) {}

static void test_arithmetic_and_stack(void) {
  Asm a;
  a.pushI32(2);
  a.pushI32(3);
  a.op(OP_ADD);      // 5
  a.pushI32(4);
  a.op(OP_MUL);      // 20
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  Status s = vmRun(vm, 100);

  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL(1, vm.sp);
  TEST_ASSERT_EQUAL_INT32(20, vm.stack[0]);
}

static void test_if_else_true_branch(void) {
  // (7 > 3) ? 100 : 200  ->  100
  Asm a;
  a.pushI32(7);
  a.pushI32(3);
  a.op(OP_GT);
  uint16_t jz = a.jumpPlaceholder(OP_JZ);
  a.pushI32(100);
  uint16_t jmp = a.jumpPlaceholder(OP_JMP);
  uint16_t elseLabel = a.len;
  a.patch(jz, elseLabel);
  a.pushI32(200);
  uint16_t endLabel = a.len;
  a.patch(jmp, endLabel);
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  Status s = vmRun(vm, 100);

  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL_INT32(100, vm.stack[vm.sp - 1]);
}

static void test_if_else_false_branch(void) {
  // (2 > 3) ? 100 : 200  ->  200
  Asm a;
  a.pushI32(2);
  a.pushI32(3);
  a.op(OP_GT);
  uint16_t jz = a.jumpPlaceholder(OP_JZ);
  a.pushI32(100);
  uint16_t jmp = a.jumpPlaceholder(OP_JMP);
  uint16_t elseLabel = a.len;
  a.patch(jz, elseLabel);
  a.pushI32(200);
  uint16_t endLabel = a.len;
  a.patch(jmp, endLabel);
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  Status s = vmRun(vm, 100);

  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL_INT32(200, vm.stack[vm.sp - 1]);
}

static void test_bounded_loop_sum_1_to_5(void) {
  enum { SLOT_I = 0, SLOT_SUM = 1 };
  Asm a;
  a.pushI32(0); a.slotOp(OP_STORE, SLOT_SUM);
  a.pushI32(1); a.slotOp(OP_STORE, SLOT_I);

  uint16_t loopStart = a.len;
  a.slotOp(OP_LOAD, SLOT_I);
  a.pushI32(5);
  a.op(OP_LE);
  uint16_t jz = a.jumpPlaceholder(OP_JZ);

  a.slotOp(OP_LOAD, SLOT_SUM);
  a.slotOp(OP_LOAD, SLOT_I);
  a.op(OP_ADD);
  a.slotOp(OP_STORE, SLOT_SUM);

  a.slotOp(OP_LOAD, SLOT_I);
  a.pushI32(1);
  a.op(OP_ADD);
  a.slotOp(OP_STORE, SLOT_I);

  uint16_t jmp = a.jumpPlaceholder(OP_JMP);
  a.patch(jmp, loopStart);

  uint16_t endLabel = a.len;
  a.patch(jz, endLabel);
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  Status s = vmRun(vm, 1000);

  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL_INT32(15, vm.vars[SLOT_SUM]);
}

static int32_t sys_double_it(int32_t *args, uint8_t argc) {
  return args[0] * 2;
}

static void test_syscall_roundtrip(void) {
  Asm a;
  a.pushI32(21);
  a.call(0, 1);
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  uint8_t id = vmRegisterSyscall(vm, sys_double_it);
  TEST_ASSERT_EQUAL(0, id);

  Status s = vmRun(vm, 100);
  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL_INT32(42, vm.stack[vm.sp - 1]);
}

static void test_div_by_zero_is_reported(void) {
  Asm a;
  a.pushI32(10);
  a.pushI32(0);
  a.op(OP_DIV);
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  Status s = vmRun(vm, 100);

  TEST_ASSERT_EQUAL(VM_ERR_DIV_ZERO, s);
}

static void test_unbounded_script_hits_step_limit(void) {
  // Deliberately infinite loop: JMP back to itself.
  Asm a;
  uint16_t start = a.len;
  uint16_t jmp = a.jumpPlaceholder(OP_JMP);
  a.patch(jmp, start);

  VM vm;
  vmInit(vm, a.buf, a.len);
  Status s = vmRun(vm, 50);

  TEST_ASSERT_EQUAL(VM_ERR_STEP_LIMIT, s);
}

static void test_float_arithmetic(void) {
  // (3.5 + 1.25) * 2.0 = 9.5
  Asm a;
  a.pushF32(3.5f);
  a.pushF32(1.25f);
  a.op(OP_FADD);
  a.pushF32(2.0f);
  a.op(OP_FMUL);
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  Status s = vmRun(vm, 100);

  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL(1, vm.sp);
  TEST_ASSERT_EQUAL_FLOAT(9.5f, bitsF32(vm.stack[0]));
}

static void test_float_comparison(void) {
  // 3.5 > 2.0 -> 1
  Asm a;
  a.pushF32(3.5f);
  a.pushF32(2.0f);
  a.op(OP_FGT);
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  Status s = vmRun(vm, 100);

  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL_INT32(1, vm.stack[vm.sp - 1]);
}

static void test_float_negate(void) {
  Asm a;
  a.pushF32(3.5f);
  a.op(OP_FNEG);
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  Status s = vmRun(vm, 100);

  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL_FLOAT(-3.5f, bitsF32(vm.stack[vm.sp - 1]));
}

static void test_int_to_float_and_back(void) {
  // (int)((float)7 / 2.0f) == 3  (i.e. i2f, float divide, f2i truncates)
  Asm a;
  a.pushI32(7);
  a.op(OP_I2F);
  a.pushF32(2.0f);
  a.op(OP_FDIV);
  a.op(OP_F2I);
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  Status s = vmRun(vm, 100);

  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL_INT32(3, vm.stack[vm.sp - 1]);
}

static void test_float_div_by_zero_is_reported(void) {
  Asm a;
  a.pushF32(1.0f);
  a.pushF32(0.0f);
  a.op(OP_FDIV);
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  Status s = vmRun(vm, 100);

  TEST_ASSERT_EQUAL(VM_ERR_DIV_ZERO, s);
}

static void test_power_to_drive_example(void) {
  // Mirrors the motivating use case: read a power level in watts (float),
  // and if it's over a threshold, reduce a max-drive percentage (float).
  //   watts = read_power();           // syscall, id 0
  //   if (watts > 100.0) {
  //     drive = max_drive() - 5.0;    // syscall id 1, then float sub
  //     set_max_drive(drive);         // syscall id 2
  //   }
  Asm a;
  a.call(0, 0);                 // watts = read_power()
  a.slotOp(OP_STORE, 0);        // slot 0 = watts
  a.slotOp(OP_LOAD, 0);
  a.pushF32(100.0f);
  a.op(OP_FGT);
  uint16_t jz = a.jumpPlaceholder(OP_JZ);
  a.call(1, 0);                 // max_drive()
  a.pushF32(5.0f);
  a.op(OP_FSUB);
  a.slotOp(OP_STORE, 1);        // slot 1 = drive
  a.slotOp(OP_LOAD, 1);
  a.call(2, 1);                 // set_max_drive(drive)
  a.op(OP_POP);
  uint16_t end = a.len;
  a.patch(jz, end);
  a.op(OP_HALT);

  static float lastSetDrive = 0.0f;
  struct Syscalls {
    static int32_t readPower(int32_t *args, uint8_t argc) { return f32bits(125.0f); }
    static int32_t maxDrive(int32_t *args, uint8_t argc)  { return f32bits(80.0f); }
    static int32_t setMaxDrive(int32_t *args, uint8_t argc) {
      lastSetDrive = bitsF32(args[0]);
      return 1;
    }
  };

  VM vm;
  vmInit(vm, a.buf, a.len);
  vmRegisterSyscall(vm, Syscalls::readPower);
  vmRegisterSyscall(vm, Syscalls::maxDrive);
  vmRegisterSyscall(vm, Syscalls::setMaxDrive);

  Status s = vmRun(vm, 100);

  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL_FLOAT(75.0f, lastSetDrive);  // 125 W > 100 W -> 80% - 5% = 75%
}

// ---------------------------------------------------------------------------
// CMDCALL / string pool
// ---------------------------------------------------------------------------

static char lastBridgeName[32];
static int32_t lastBridgeArgs[4];
static uint8_t lastBridgeArgc;

static int32_t fakeCmdBridge(const char *name, int32_t *args, uint8_t argc) {
  strncpy(lastBridgeName, name, sizeof(lastBridgeName) - 1);
  lastBridgeName[sizeof(lastBridgeName) - 1] = '\0';
  lastBridgeArgc = argc;
  for (uint8_t i = 0; i < argc && i < 4; i++) lastBridgeArgs[i] = args[i];
  return 99;
}

static void test_cmdcall_invokes_bridge_with_name_and_args(void) {
  static const uint8_t pool[] = "SADCPIN\0";  // offset 0, NUL-terminated
  Asm a;
  a.pushI32(5);
  a.pushI32(6);
  a.cmdCall(0, 2);
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  vmSetPool(vm, pool, sizeof(pool));
  vmSetCmdBridge(vm, fakeCmdBridge);

  lastBridgeName[0] = '\0';
  lastBridgeArgc = 0;
  Status s = vmRun(vm, 100);

  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL_STRING("SADCPIN", lastBridgeName);
  TEST_ASSERT_EQUAL(2, lastBridgeArgc);
  TEST_ASSERT_EQUAL_INT32(5, lastBridgeArgs[0]);
  TEST_ASSERT_EQUAL_INT32(6, lastBridgeArgs[1]);
  TEST_ASSERT_EQUAL_INT32(99, vm.stack[vm.sp - 1]);
}

static void test_cmdcall_without_bridge_registered_errors(void) {
  static const uint8_t pool[] = "FOO\0";
  Asm a;
  a.cmdCall(0, 0);
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  vmSetPool(vm, pool, sizeof(pool));
  // no vmSetCmdBridge() call

  Status s = vmRun(vm, 100);
  TEST_ASSERT_EQUAL(VM_ERR_NO_CMD_BRIDGE, s);
}

static void test_cmdcall_offset_out_of_range_errors(void) {
  static const uint8_t pool[] = "FOO\0";
  Asm a;
  a.cmdCall(100, 0);  // way past the 4-byte pool
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  vmSetPool(vm, pool, sizeof(pool));
  vmSetCmdBridge(vm, fakeCmdBridge);

  Status s = vmRun(vm, 100);
  TEST_ASSERT_EQUAL(VM_ERR_BAD_POOL, s);
}

static void test_cmdcall_missing_terminator_errors(void) {
  static const uint8_t pool[] = {'A', 'B', 'C'};  // no NUL anywhere
  Asm a;
  a.cmdCall(0, 0);
  a.op(OP_HALT);

  VM vm;
  vmInit(vm, a.buf, a.len);
  vmSetPool(vm, pool, sizeof(pool));
  vmSetCmdBridge(vm, fakeCmdBridge);

  Status s = vmRun(vm, 100);
  TEST_ASSERT_EQUAL(VM_ERR_BAD_POOL, s);
}

int main(int argc, char **argv) {
  UNITY_BEGIN();
  RUN_TEST(test_arithmetic_and_stack);
  RUN_TEST(test_if_else_true_branch);
  RUN_TEST(test_if_else_false_branch);
  RUN_TEST(test_bounded_loop_sum_1_to_5);
  RUN_TEST(test_syscall_roundtrip);
  RUN_TEST(test_div_by_zero_is_reported);
  RUN_TEST(test_unbounded_script_hits_step_limit);
  RUN_TEST(test_float_arithmetic);
  RUN_TEST(test_float_comparison);
  RUN_TEST(test_float_negate);
  RUN_TEST(test_int_to_float_and_back);
  RUN_TEST(test_float_div_by_zero_is_reported);
  RUN_TEST(test_power_to_drive_example);
  RUN_TEST(test_cmdcall_invokes_bridge_with_name_and_args);
  RUN_TEST(test_cmdcall_without_bridge_registered_errors);
  RUN_TEST(test_cmdcall_offset_out_of_range_errors);
  RUN_TEST(test_cmdcall_missing_terminator_errors);
  return UNITY_END();
}
