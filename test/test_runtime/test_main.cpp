// On-device capstone test: proves the full chain works against REAL
// hardware -- a script's cmd() call reaches GAACE_Core's real
// commandProcessor::executeLine() and a real Command table, not a fake
// bridge (unlike the native/g++ tests in ../../tools/test_gsc.py, which
// intentionally use a fake bridge to test the compiler in isolation).
//
// Runs on real hardware (Adafruit QT Py M0) via:
//   pio test -e adafruit_qt_py_m0 --upload-port <port>
#include <Arduino.h>
#include <unity.h>
#include <string.h>
#include <GAACEScriptRuntime.h>
#include <commandProcessor.h>
#include <ThreadController.h>

using namespace GAACEScript;

void setUp(void) {}
void tearDown(void) {}

// A write-only Stream that captures everything written to it -- used the
// same way as GAACE_Core's own MockStream, to drive SCRIPTLOAD through the
// real command dispatch and inspect its ACK/NAK.
class MockStream : public Stream {
public:
  uint8_t buf[256];
  size_t len = 0;
  int available() override { return 0; }
  int read() override { return -1; }
  int peek() override { return -1; }
  size_t write(uint8_t b) override {
    if (len < sizeof(buf) - 1) buf[len++] = b;
    buf[len] = 0;
    return 1;
  }
  void reset() { len = 0; buf[0] = 0; }
};

static int testValue = 0;

// -----------------------------------------------------------------------
// Direct vmInit()+vmSetPool(): cmd("SFOO", 42) should reach the real
// commandProcessor and set testValue via a real registered "?FOO" command.
// -----------------------------------------------------------------------
static void test_cmd_set_reaches_real_command(void) {
  commandProcessor cp;
  ThreadController control;
  ScriptRuntime scripts(&cp, &control);

  MockStream ms;
  cp.registerStream(&ms);
  cp.registerCommands(scripts.scriptCmdList());

  int range[2] = {0, 1000};
  Command cmds[] = {
    {"?FOO", CMDint, -1, (void *)&testValue, (void *)range, "test"},
    {NULL}
  };
  CommandList list = {cmds, NULL};
  cp.registerCommands(&list);

  // cmd("SFOO", 42);  ->  PUSH_I32 42 ; CMDCALL poolOffset=0 argc=1 ; POP ; HALT
  static uint8_t code[] = {
    0x01, 42, 0, 0, 0,      // OP_PUSH_I32 42
    0x27, 0, 0, 1,          // OP_CMDCALL offset=0 argc=1
    0x03,                   // OP_POP
    0x00                    // OP_HALT
  };
  static const uint8_t pool[] = "SFOO\0";

  ScriptSlot &slot0 = scripts.slots[0];
  memcpy(slot0.code, code, sizeof(code));
  slot0.codeLen = sizeof(code);
  vmInit(slot0.vm, slot0.code, slot0.codeLen);
  vmSetPool(slot0.vm, pool, sizeof(pool));
  slot0.loaded = true;

  testValue = 0;
  Status s = vmRun(slot0.vm, 100);

  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL(42, testValue);
}

// -----------------------------------------------------------------------
// cmd("GFOO") with no args should return the current value, parsed from
// the real command's response.
// -----------------------------------------------------------------------
static void test_cmd_get_returns_parsed_value(void) {
  commandProcessor cp;
  ThreadController control;
  ScriptRuntime scripts(&cp, &control);

  MockStream ms;
  cp.registerStream(&ms);
  cp.registerCommands(scripts.scriptCmdList());

  Command cmds[] = {
    {"?FOO", CMDint, -1, (void *)&testValue, NULL, "test"},
    {NULL}
  };
  CommandList list = {cmds, NULL};
  cp.registerCommands(&list);
  testValue = 77;

  // result = cmd("GFOO"); -> CMDCALL offset=0 argc=0 ; STORE slot0 ; HALT
  static uint8_t code[] = {
    0x27, 0, 0, 0,   // OP_CMDCALL offset=0 argc=0
    0x05, 0,         // OP_STORE slot 0
    0x00             // OP_HALT
  };
  static const uint8_t pool[] = "GFOO\0";

  ScriptSlot &slot0 = scripts.slots[0];
  memcpy(slot0.code, code, sizeof(code));
  slot0.codeLen = sizeof(code);
  vmInit(slot0.vm, slot0.code, slot0.codeLen);
  vmSetPool(slot0.vm, pool, sizeof(pool));
  slot0.loaded = true;

  Status s = vmRun(slot0.vm, 100);

  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL_INT32(77, slot0.vm.vars[0]);
}

// -----------------------------------------------------------------------
// cmd() against an unknown command name returns 0 (the documented NAK
// convention), not a crash or a stale value.
// -----------------------------------------------------------------------
static void test_cmd_unknown_command_returns_zero(void) {
  commandProcessor cp;
  ThreadController control;
  ScriptRuntime scripts(&cp, &control);

  MockStream ms;
  cp.registerStream(&ms);
  cp.registerCommands(scripts.scriptCmdList());

  // result = cmd("NOSUCHCMD"); -> CMDCALL offset=0 argc=0 ; STORE slot0 ; HALT
  static uint8_t code[] = {
    0x27, 0, 0, 0,
    0x05, 0,
    0x00
  };
  static const uint8_t pool[] = "NOSUCHCMD\0";

  ScriptSlot &slot0 = scripts.slots[0];
  memcpy(slot0.code, code, sizeof(code));
  slot0.codeLen = sizeof(code);
  vmInit(slot0.vm, slot0.code, slot0.codeLen);
  vmSetPool(slot0.vm, pool, sizeof(pool));
  slot0.loaded = true;

  Status s = vmRun(slot0.vm, 100);

  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL_INT32(0, slot0.vm.vars[0]);
}

// -----------------------------------------------------------------------
// SCRIPTLOAD end to end: the hex below is gsc.py's real --scriptload
// output for `cmd("SFOO", 42);` (verified by hand: 0b00 = codeLen 11,
// then 11 code bytes, then "SFOO\0"). Proves the [codeLen][code][pool]
// split in cmdScriptLoad() actually works against real compiler output,
// not just hand-built test bytecode.
// -----------------------------------------------------------------------
static void test_scriptload_with_pool_end_to_end(void) {
  commandProcessor cp;
  ThreadController control;
  ScriptRuntime scripts(&cp, &control);

  MockStream ms;
  cp.registerStream(&ms);
  cp.registerCommands(scripts.scriptCmdList());

  int range[2] = {0, 1000};
  Command cmds[] = {
    {"?FOO", CMDint, -1, (void *)&testValue, (void *)range, "test"},
    {NULL}
  };
  CommandList list = {cmds, NULL};
  cp.registerCommands(&list);

  const char *loadLine = "SCRIPTLOAD,0,0b00012a00000027000001030053464f4f00\n";
  for (const char *p = loadLine; *p; p++) cp.rb->put(*p);

  ms.reset();
  TEST_ASSERT_TRUE(cp.processCommands());
  TEST_ASSERT_EQUAL(0x06, ms.buf[0]);  // SCRIPTLOAD itself ACKed
  TEST_ASSERT_TRUE(scripts.slots[0].loaded);

  testValue = 0;
  Status s = vmRun(scripts.slots[0].vm, 100);
  TEST_ASSERT_EQUAL(VM_HALTED, s);
  TEST_ASSERT_EQUAL(42, testValue);  // the loaded script's cmd("SFOO", 42) ran
}

void setup() {
  delay(2000);
  UNITY_BEGIN();
  RUN_TEST(test_cmd_set_reaches_real_command);
  RUN_TEST(test_cmd_get_returns_parsed_value);
  RUN_TEST(test_cmd_unknown_command_returns_zero);
  RUN_TEST(test_scriptload_with_pool_end_to_end);
  UNITY_END();
}

void loop() {}
