// Native unit tests for the GAACE_Script VM core.
// Run with: pio test -e native  (no hardware required)
#include <unity.h>
#include "GAACEScript.h"

using namespace GAACEScript;

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

int main(int argc, char **argv) {
  UNITY_BEGIN();
  RUN_TEST(test_arithmetic_and_stack);
  RUN_TEST(test_if_else_true_branch);
  RUN_TEST(test_if_else_false_branch);
  RUN_TEST(test_bounded_loop_sum_1_to_5);
  RUN_TEST(test_syscall_roundtrip);
  RUN_TEST(test_div_by_zero_is_reported);
  RUN_TEST(test_unbounded_script_hits_step_limit);
  return UNITY_END();
}
