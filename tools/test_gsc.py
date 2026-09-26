#!/usr/bin/env python3
"""
Tests for gsc.py.

Two layers:
  - Structural tests against compile_source()/disassemble() — no external
    toolchain needed, catch compiler bugs directly.
  - End-to-end tests that build the *real* C++ VM (src/GAACEScript.cpp) with
    g++ and run compiled bytecode through it, to catch any drift between
    this compiler's opcode numbering/encoding and the actual VM. Skipped if
    g++ isn't available.

Run with: python3 -m unittest tools/test_gsc.py -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import gsc  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src")

HARNESS_CPP = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "GAACEScript.h"
using namespace GAACEScript;

static float asFloat(int32_t bits) { float f; memcpy(&f, &bits, sizeof(f)); return f; }
static int32_t asBits(float f) { int32_t bits; memcpy(&bits, &f, sizeof(bits)); return bits; }

// Fixed syscall table shared by every test script (ids are positional, see
// tools/test_gsc.py for which id means what):
//   0: print(v)          int -> prints "<v>"
//   1: printf(v)         float -> prints "F<v>"
//   2: read_power()      float -> fixed fixture value, 125.0
//   3: max_drive()       float -> fixed fixture value, 80.0
//   4: set_max_drive(v)  float -> prints "S<v>"
static int32_t sys_print(int32_t *args, uint8_t argc)   { printf("%d\n", args[0]); return 0; }
static int32_t sys_printf(int32_t *args, uint8_t argc)  { printf("F%g\n", asFloat(args[0])); return 0; }
static int32_t sys_read_power(int32_t *args, uint8_t argc) { return asBits(125.0f); }
static int32_t sys_max_drive(int32_t *args, uint8_t argc)  { return asBits(80.0f); }
static int32_t sys_set_max_drive(int32_t *args, uint8_t argc) {
  printf("S%g\n", asFloat(args[0]));
  return 0;
}

// Fake cmd() bridge: prints "CMD:<name>,<arg0>,<arg1>,...\n" so tests can
// verify the right name/args reached it, and returns a fixed value the
// script can act on.
static int32_t fake_cmd_bridge(const char *name, int32_t *args, uint8_t argc) {
  printf("CMD:%s", name);
  for (uint8_t i = 0; i < argc; i++) printf(",%d", args[i]);
  printf("\n");
  return 123;
}

static std::vector<uint8_t> readFile(const char *path) {
  std::vector<uint8_t> buf;
  FILE *f = fopen(path, "rb");
  if (!f) return buf;
  uint8_t b;
  while (fread(&b, 1, 1, f) == 1) buf.push_back(b);
  fclose(f);
  return buf;
}

int main(int argc, char **argv) {
  // argv: <codefile> <poolfile> <reps>
  int reps = (argc > 3) ? atoi(argv[3]) : 1;
  std::vector<uint8_t> code = readFile(argv[1]);
  std::vector<uint8_t> pool = readFile(argv[2]);

  VM vm;
  vmInit(vm, code.data(), (uint16_t)code.size());
  vmSetPool(vm, pool.data(), (uint16_t)pool.size());
  vmSetCmdBridge(vm, fake_cmd_bridge);
  vmRegisterSyscall(vm, sys_print);
  vmRegisterSyscall(vm, sys_printf);
  vmRegisterSyscall(vm, sys_read_power);
  vmRegisterSyscall(vm, sys_max_drive);
  vmRegisterSyscall(vm, sys_set_max_drive);
  Status s = VM_OK;
  for (int i = 0; i < reps; i++) {
    vm.pc = 0; vm.sp = 0;   // rerun without vmInit: vars persist across reps
    s = vmRun(vm, 10000);
  }
  fprintf(stderr, "STATUS:%d\n", s);
  return 0;
}
"""


class StructuralTests(unittest.TestCase):
    def test_arithmetic(self):
        code = gsc.compile_source("syscall print(v) = 0; print(2 + 3 * 4);")
        self.assertIn(gsc.OP_MUL, code)
        self.assertIn(gsc.OP_ADD, code)
        self.assertEqual(code[-1], gsc.OP_HALT)

    def test_variable_before_assignment_is_error(self):
        with self.assertRaises(gsc.CompileError):
            gsc.compile_source("x = y + 1;")

    def test_undeclared_syscall_is_error(self):
        with self.assertRaises(gsc.CompileError):
            gsc.compile_source("foo(1);")

    def test_wrong_argc_is_error(self):
        with self.assertRaises(gsc.CompileError):
            gsc.compile_source("syscall foo(a, b) = 0; foo(1);")

    def test_duplicate_syscall_decl_is_error(self):
        with self.assertRaises(gsc.CompileError):
            gsc.compile_source("syscall foo(a) = 0; syscall foo(a) = 1;")

    def test_too_many_variables_is_error(self):
        stmts = "".join(f"v{i} = {i};\n" for i in range(gsc.VAR_SLOTS + 1))
        with self.assertRaises(gsc.CompileError):
            gsc.compile_source(stmts)

    def test_var_decl_allows_read_before_assignment(self):
        code = gsc.compile_source("var prev; x = prev + 1;")
        self.assertIn(gsc.OP_LOAD, code)

    def test_disassemble_roundtrip_is_stable(self):
        code = gsc.compile_source("i = 1; while (i <= 3) { i = i + 1; }")
        text = gsc.disassemble(code)
        self.assertIn("JZ ->", text)
        self.assertIn("JMP ->", text)

    def test_float_literal_emits_push_f32(self):
        code = gsc.compile_source("x = 3.5;")
        self.assertIn(gsc.OP_PUSH_F32, code)
        self.assertNotIn(gsc.OP_PUSH_I32, code)

    def test_assigning_float_to_int_var_is_error(self):
        with self.assertRaises(gsc.CompileError):
            gsc.compile_source("x = 1; x = 2.5;")

    def test_assigning_int_to_float_var_promotes(self):
        code = gsc.compile_source("var x: float; x = 5;")
        self.assertIn(gsc.OP_I2F, code)

    def test_mod_with_float_operand_is_error(self):
        with self.assertRaises(gsc.CompileError):
            gsc.compile_source("x = 5.0 % 2;")

    def test_logic_op_with_float_operand_is_error(self):
        with self.assertRaises(gsc.CompileError):
            gsc.compile_source("x = 5.0 && 1;")

    def test_syscall_float_return_type(self):
        code = gsc.compile_source("syscall watts() : float = 0; x = watts() + 1.0;")
        self.assertIn(gsc.OP_FADD, code)

    def test_cmd_call_emits_cmdcall_and_pool_entry(self):
        code, pool = gsc.compile_source_with_pool('cmd("SADCPIN", 5);')
        self.assertIn(gsc.OP_CMDCALL, code)
        self.assertEqual(pool, b"SADCPIN\x00")

    def test_cmd_call_dedupes_repeated_names(self):
        code, pool = gsc.compile_source_with_pool(
            'cmd("SADCPIN", 5); cmd("SADCPIN", 6);')
        self.assertEqual(pool, b"SADCPIN\x00")  # one copy, not two

    def test_cmd_call_with_no_args(self):
        code, pool = gsc.compile_source_with_pool('x = cmd("GVER");')
        self.assertIn(gsc.OP_CMDCALL, code)
        self.assertEqual(pool, b"GVER\x00")

    def test_disassemble_shows_cmd_name_from_pool(self):
        code, pool = gsc.compile_source_with_pool('cmd("SADCPIN", 5);')
        text = gsc.disassemble(code, pool)
        self.assertIn('CMDCALL "SADCPIN"', text)


@unittest.skipUnless(shutil.which("g++"), "g++ not available")
class EndToEndVmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="gsc_test_")
        harness_cpp = os.path.join(cls.tmpdir, "harness.cpp")
        with open(harness_cpp, "w") as f:
            f.write(HARNESS_CPP)
        cls.harness_bin = os.path.join(cls.tmpdir, "harness")
        subprocess.run(
            ["g++", "-std=c++14", "-I", SRC_DIR, harness_cpp,
             os.path.join(SRC_DIR, "GAACEScript.cpp"), "-o", cls.harness_bin],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def run_script(self, source, reps=1):
        code, pool = gsc.compile_source_with_pool(source)
        bin_path = os.path.join(self.tmpdir, "prog.bin")
        pool_path = os.path.join(self.tmpdir, "prog.pool.bin")
        with open(bin_path, "wb") as f:
            f.write(code)
        with open(pool_path, "wb") as f:
            f.write(pool)
        result = subprocess.run([self.harness_bin, bin_path, pool_path, str(reps)],
                                 capture_output=True, text=True, check=True)
        status_line = [l for l in result.stderr.splitlines() if l.startswith("STATUS:")][0]
        status = int(status_line.split(":")[1])
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        return status, lines

    def test_bounded_loop_sum_1_to_5(self):
        status, lines = self.run_script("""
            syscall print(v) = 0;
            sum = 0; i = 1;
            while (i <= 5) { sum = sum + i; i = i + 1; }
            print(sum);
        """)
        self.assertEqual(status, 1)  # VM_HALTED
        self.assertEqual([int(l) for l in lines], [15])

    def test_if_else_both_branches(self):
        status, lines = self.run_script("""
            syscall print(v) = 0;
            if (7 > 3) { print(100); } else { print(200); }
            if (2 > 3) { print(100); } else { print(200); }
        """)
        self.assertEqual(status, 1)
        self.assertEqual([int(l) for l in lines], [100, 200])

    def test_var_persists_across_reruns_without_reinit(self):
        # Mirrors the intended firmware pattern: vmInit() once, then only
        # pc/sp reset between ticks so a `var`-declared slot carries state.
        status, lines = self.run_script("""
            syscall print(v) = 0;
            var total;
            total = total + 1;
            print(total);
        """, reps=3)
        self.assertEqual(status, 1)
        self.assertEqual([int(l) for l in lines], [1, 2, 3])

    def test_logical_and_or_not(self):
        status, lines = self.run_script("""
            syscall print(v) = 0;
            print((1 && 0) + (1 || 0) + !0);
        """)
        self.assertEqual(status, 1)
        self.assertEqual([int(l) for l in lines], [2])  # 0 + 1 + 1

    def test_float_literal_arithmetic(self):
        status, lines = self.run_script("""
            syscall printf(v) = 1;
            printf((3.5 + 1.25) * 2.0);
        """)
        self.assertEqual(status, 1)
        self.assertEqual(lines, ["F9.5"])

    def test_int_promotes_to_float_in_mixed_expression(self):
        status, lines = self.run_script("""
            syscall printf(v) = 1;
            x = 5;
            y = x + 2.5;
            printf(y);
        """)
        self.assertEqual(status, 1)
        self.assertEqual(lines, ["F7.5"])

    def test_explicit_float_to_int_cast_truncates(self):
        status, lines = self.run_script("""
            syscall print(v) = 0;
            print((int)(7.9));
        """)
        self.assertEqual(status, 1)
        self.assertEqual([int(l) for l in lines], [7])

    def test_power_to_drive_example(self):
        # The motivating use case: read a power level in watts (float), and
        # if it's over a threshold, reduce a max-drive percentage (float).
        status, lines = self.run_script("""
            syscall read_power() : float = 2;
            syscall max_drive() : float = 3;
            syscall set_max_drive(v) = 4;

            watts = read_power();
            if (watts > 100.0) {
                drive = max_drive() - 5.0;
                set_max_drive(drive);
            }
        """)
        self.assertEqual(status, 1)
        # fixture: read_power()=125.0, max_drive()=80.0 -> 125>100, so 80-5=75
        self.assertEqual(lines, ["S75"])

    def test_cmd_call_reaches_bridge_with_name_and_args(self):
        status, lines = self.run_script("""
            syscall print(v) = 0;
            result = cmd("SADCPIN", 5, 6);
            print(result);
        """)
        self.assertEqual(status, 1)
        self.assertEqual(lines, ["CMD:SADCPIN,5,6", "123"])

    def test_cmd_call_no_args_reaches_bridge(self):
        status, lines = self.run_script("""
            syscall print(v) = 0;
            print(cmd("GVER"));
        """)
        self.assertEqual(status, 1)
        self.assertEqual(lines, ["CMD:GVER", "123"])


if __name__ == "__main__":
    unittest.main()
