/*
    BasicScript example — GAACE_Script VM + standard runtime

    Demonstrates:
      1. Hand-assembling a small bytecode program with a bounded loop and
         an if/else branch (a real host-side compiler would generate this
         from readable script source instead)
      2. Registering a syscall so the script can call back into firmware code
      3. Loading it into slot 0 of the standard ScriptRuntime (the same path
         a project takes to preload a default script at boot) instead of
         driving a bare VM directly
      4. Wiring ScriptRuntime's commands (SCRIPTLOAD, GSCRIPTLIMITS,
         GSCRIPTST) into a commandProcessor over Serial — start/stop/rate
         for the slot come from GAACE_Core's threadCommands (?TENA,Script0
         / ?TINT,Script0), not from anything in this library, once
         GAACE_THREAD_CMDS is also enabled by the consuming project

    Program computed (in slot 0, ticking once a second):
      sum = 0; i = 1;
      while (i <= 5) { sum = sum + i; i = i + 1; }   // sum = 15
      print(sum);
      print((sum > 10) ? 100 : 200);                  // 100

    Hardware: any board — this example only uses Serial, no other pins.
    Try it: open a serial monitor at 115200 baud, watch slot 0 print its
    result once a second, and type GSCRIPTLIMITS or GSCRIPTST,0 to see the
    new standard commands respond.
*/

#include <Arduino.h>
#include <GAACEScript.h>
#include <GAACEScriptRuntime.h>
#include <commandProcessor.h>
#include <ThreadController.h>
#include <string.h>

using namespace GAACEScript;

commandProcessor  cp;
ThreadController  control;
ScriptRuntime      scripts(&cp, &control, /*defaultIntervalMs=*/1000);

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

  cp.registerStream(&Serial);
  cp.registerCommands(scripts.scriptCmdList());
  scripts.registerSyscall(sys_print);  // id 0, on every slot

  buildProgram();
  ScriptSlot &slot0 = scripts.slots[0];
  memcpy(slot0.code, program.buf, program.len);
  slot0.codeLen = program.len;
  vmInit(slot0.vm, slot0.code, slot0.codeLen);
  slot0.loaded = true;

  Serial.println("GAACE_Script BasicScript example ready.");
  Serial.println("Slot 0 runs once a second; try GSCRIPTLIMITS or GSCRIPTST,0.");
}

void loop() {
  cp.processStreams();
  cp.processCommands();
  control.run();  // drives all SCRIPT_SLOTS threads, including slot 0 above
}
