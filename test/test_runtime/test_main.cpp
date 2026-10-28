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

// -----------------------------------------------------------------------
// Syscall table: registerSyscalls() / GSYSCALLS / the optional <hash> on
// SCRIPTLOAD. Real commandProcessor dispatch, real ACK/NAK bytes.
// -----------------------------------------------------------------------
static int32_t dummySyscall(int32_t *args, uint8_t argc) { return 0; }

static const SyscallDesc testSyscalls[] = {
  {"read_adc",  dummySyscall, 0, false},
  {"get_volts", dummySyscall, 1, true},
};

static void sendLine(commandProcessor &cp, MockStream &ms, const char *line) {
  for (const char *p = line; *p; p++) cp.rb->put(*p);
  cp.rb->put('\n');
  ms.reset();
  cp.processCommands();
}

static void test_register_syscalls_rules(void) {
  commandProcessor cp;
  ThreadController control;
  ScriptRuntime scripts(&cp, &control);

  TEST_ASSERT_EQUAL_UINT32(0, scripts.syscallHash());          // no table yet
  TEST_ASSERT_TRUE(scripts.registerSyscalls(testSyscalls, 2));
  TEST_ASSERT_EQUAL_UINT32(syscallTableHash(testSyscalls, 2), scripts.syscallHash());
  TEST_ASSERT_EQUAL(2, scripts.slots[0].vm.syscallCount);
  TEST_ASSERT_EQUAL(2, scripts.slots[SCRIPT_SLOTS - 1].vm.syscallCount);  // every slot
  TEST_ASSERT_FALSE(scripts.registerSyscalls(testSyscalls, 2)); // only once
}

static void test_register_syscalls_refused_after_legacy_registration(void) {
  commandProcessor cp;
  ThreadController control;
  ScriptRuntime scripts(&cp, &control);

  scripts.registerSyscall(dummySyscall);                        // legacy, id 0
  TEST_ASSERT_FALSE(scripts.registerSyscalls(testSyscalls, 2)); // ids would not start at 0
  TEST_ASSERT_EQUAL_UINT32(0, scripts.syscallHash());
}

static void test_gsyscalls_reports_the_table(void) {
  commandProcessor cp;
  ThreadController control;
  ScriptRuntime scripts(&cp, &control);
  MockStream ms;
  cp.registerStream(&ms);
  cp.registerCommands(scripts.scriptCmdList());
  scripts.registerSyscalls(testSyscalls, 2);

  sendLine(cp, ms, "GSYSCALLS");

  char expect[96];
  snprintf(expect, sizeof(expect), "2,%08lX", (unsigned long)scripts.syscallHash());
  TEST_ASSERT_EQUAL(0x06, ms.buf[0]);
  TEST_ASSERT_NOT_NULL(strstr((char *)ms.buf + 1, expect));
  TEST_ASSERT_NOT_NULL(strstr((char *)ms.buf, "0,read_adc,0,int"));
  TEST_ASSERT_NOT_NULL(strstr((char *)ms.buf, "1,get_volts,1,float"));
}

static void test_gsyscalls_naks_without_a_table(void) {
  commandProcessor cp;
  ThreadController control;
  ScriptRuntime scripts(&cp, &control);
  MockStream ms;
  cp.registerStream(&ms);
  cp.registerCommands(scripts.scriptCmdList());

  sendLine(cp, ms, "GSYSCALLS");
  TEST_ASSERT_EQUAL(0x15, ms.buf[0]);
}

static void test_scriptload_hash_accepted_rejected_and_remembered(void) {
  commandProcessor cp;
  ThreadController control;
  ScriptRuntime scripts(&cp, &control);
  MockStream ms;
  cp.registerStream(&ms);
  cp.registerCommands(scripts.scriptCmdList());
  scripts.registerSyscalls(testSyscalls, 2);

  const char *payload = "0b00012a00000027000001030053464f4f00";   // see the test above
  char line[160];

  // matching hash: loads, and the slot remembers it
  snprintf(line, sizeof(line), "SCRIPTLOAD,0,%s,%08lX", payload, (unsigned long)scripts.syscallHash());
  sendLine(cp, ms, line);
  TEST_ASSERT_EQUAL(0x06, ms.buf[0]);
  TEST_ASSERT_TRUE(scripts.slots[0].loaded);
  TEST_ASSERT_EQUAL_UINT32(scripts.syscallHash(), scripts.slots[0].syscallHash);

  // no hash: loads unchecked, and clears the remembered one
  snprintf(line, sizeof(line), "SCRIPTLOAD,1,%s", payload);
  sendLine(cp, ms, line);
  TEST_ASSERT_EQUAL(0x06, ms.buf[0]);
  TEST_ASSERT_EQUAL_UINT32(0, scripts.slots[1].syscallHash);

  // wrong hash: NAK, and the slot it targeted is not marked loaded
  snprintf(line, sizeof(line), "SCRIPTLOAD,2,%s,%08lX", payload,
           (unsigned long)(scripts.syscallHash() ^ 1u));
  sendLine(cp, ms, line);
  TEST_ASSERT_EQUAL(0x15, ms.buf[0]);
  TEST_ASSERT_FALSE(scripts.slots[2].loaded);

  // malformed hash: NAK
  snprintf(line, sizeof(line), "SCRIPTLOAD,2,%s,notahash", payload);
  sendLine(cp, ms, line);
  TEST_ASSERT_EQUAL(0x15, ms.buf[0]);
  TEST_ASSERT_FALSE(scripts.slots[2].loaded);
}

static void test_scriptload_hash_naks_when_no_table_registered(void) {
  commandProcessor cp;
  ThreadController control;
  ScriptRuntime scripts(&cp, &control);
  MockStream ms;
  cp.registerStream(&ms);
  cp.registerCommands(scripts.scriptCmdList());

  sendLine(cp, ms, "SCRIPTLOAD,0,0b00012a00000027000001030053464f4f00,892E7C6F");
  TEST_ASSERT_EQUAL(0x15, ms.buf[0]);   // nothing to check the hash against
  TEST_ASSERT_FALSE(scripts.slots[0].loaded);
}

static void test_scriptcommit_checks_the_hash_too(void) {
  commandProcessor cp;
  ThreadController control;
  ScriptRuntime scripts(&cp, &control);
  MockStream ms;
  cp.registerStream(&ms);
  cp.registerCommands(scripts.scriptCmdList());
  scripts.registerSyscalls(testSyscalls, 2);

  sendLine(cp, ms, "SCRIPTAPPEND,0,0,0b00012a00000027000001030053464f4f00");
  TEST_ASSERT_EQUAL(0x06, ms.buf[0]);

  char line[64];
  snprintf(line, sizeof(line), "SCRIPTCOMMIT,0,18,%08lX", (unsigned long)(scripts.syscallHash() ^ 1u));
  sendLine(cp, ms, line);
  TEST_ASSERT_EQUAL(0x15, ms.buf[0]);
  TEST_ASSERT_FALSE(scripts.slots[0].loaded);

  snprintf(line, sizeof(line), "SCRIPTCOMMIT,0,18,%08lX", (unsigned long)scripts.syscallHash());
  sendLine(cp, ms, line);
  TEST_ASSERT_EQUAL(0x06, ms.buf[0]);
  TEST_ASSERT_TRUE(scripts.slots[0].loaded);
}

void setup() {
  delay(2000);
  UNITY_BEGIN();
  RUN_TEST(test_cmd_set_reaches_real_command);
  RUN_TEST(test_cmd_get_returns_parsed_value);
  RUN_TEST(test_cmd_unknown_command_returns_zero);
  RUN_TEST(test_scriptload_with_pool_end_to_end);
  RUN_TEST(test_register_syscalls_rules);
  RUN_TEST(test_register_syscalls_refused_after_legacy_registration);
  RUN_TEST(test_gsyscalls_reports_the_table);
  RUN_TEST(test_gsyscalls_naks_without_a_table);
  RUN_TEST(test_scriptload_hash_accepted_rejected_and_remembered);
  RUN_TEST(test_scriptload_hash_naks_when_no_table_registered);
  RUN_TEST(test_scriptcommit_checks_the_hash_too);
  UNITY_END();
}

void loop() {}
