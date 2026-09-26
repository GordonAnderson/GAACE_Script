/*
    BasicScript example — GAACE_Script VM

    Demonstrates:
      1. Hand-assembling a small bytecode program with a bounded loop and
         an if/else branch (a real host-side compiler would generate this
         from readable script source instead)
      2. Registering a syscall so the script can call back into firmware code
      3. Running the script from setup() and printing the result

    Program computed:
      sum = 0; i = 1;
      while (i <= 5) { sum = sum + i; i = i + 1; }   // sum = 15
      print(sum);
      print((sum > 10) ? 100 : 200);                  // 100

    Hardware: any board — this example only uses Serial, no other pins.
*/

#include <Arduino.h>
#include <GAACEScript.h>

using namespace GAACEScript;

// ---------------------------------------------------------------------------
// A tiny hand-assembler for this example. A real host-side compiler would
// generate bytecode like this from readable script source; the VM itself
// never parses text, it only ever sees the bytes below.
// ---------------------------------------------------------------------------
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

enum { SLOT_I = 0, SLOT_SUM = 1 };

static int32_t sys_print(int32_t *args, uint8_t argc) {
  if (argc >= 1) {
    Serial.print("script says: ");
    Serial.println(args[0]);
  }
  return 0;
}

static VM vm;
static Asm program;

static void buildProgram() {
  // sum = 0; i = 1;
  program.pushI32(0); program.slotOp(OP_STORE, SLOT_SUM);
  program.pushI32(1); program.slotOp(OP_STORE, SLOT_I);

  // while (i <= 5) { sum = sum + i; i = i + 1; }
  uint16_t loopStart = program.len;
  program.slotOp(OP_LOAD, SLOT_I);
  program.pushI32(5);
  program.op(OP_LE);
  uint16_t jzLoopEnd = program.jumpPlaceholder(OP_JZ);

  program.slotOp(OP_LOAD, SLOT_SUM);
  program.slotOp(OP_LOAD, SLOT_I);
  program.op(OP_ADD);
  program.slotOp(OP_STORE, SLOT_SUM);

  program.slotOp(OP_LOAD, SLOT_I);
  program.pushI32(1);
  program.op(OP_ADD);
  program.slotOp(OP_STORE, SLOT_I);

  uint16_t jmpLoopStart = program.jumpPlaceholder(OP_JMP);
  program.patch(jmpLoopStart, loopStart);
  uint16_t loopEnd = program.len;
  program.patch(jzLoopEnd, loopEnd);

  // print(sum)
  program.slotOp(OP_LOAD, SLOT_SUM);
  program.call(0, 1);

  // print((sum > 10) ? 100 : 200)
  program.slotOp(OP_LOAD, SLOT_SUM);
  program.pushI32(10);
  program.op(OP_GT);
  uint16_t jzElse = program.jumpPlaceholder(OP_JZ);
  program.pushI32(100);
  uint16_t jmpEnd = program.jumpPlaceholder(OP_JMP);
  uint16_t elseLabel = program.len;
  program.patch(jzElse, elseLabel);
  program.pushI32(200);
  uint16_t endLabel = program.len;
  program.patch(jmpEnd, endLabel);
  program.call(0, 1);

  program.op(OP_HALT);
}

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000) {}

  buildProgram();

  vmInit(vm, program.buf, program.len);
  vmRegisterSyscall(vm, sys_print);

  Status s = vmRun(vm, 1000);
  Serial.print("vmRun status: ");
  Serial.println(s);
}

void loop() {
  // Nothing to do — the script ran once in setup(). A real integration
  // would call vmRun() from a Thread callback the way ADCThread does today.
}
