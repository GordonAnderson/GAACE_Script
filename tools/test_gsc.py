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
#include <vector>
#include "GAACEScript.h"
using namespace GAACEScript;
static int32_t sys_print(int32_t *args, uint8_t argc) { printf("%d\n", args[0]); return 0; }
int main(int argc, char **argv) {
  FILE *f = fopen(argv[1], "rb");
  std::vector<uint8_t> buf; uint8_t b;
  while (fread(&b, 1, 1, f) == 1) buf.push_back(b);
  fclose(f);
  VM vm;
  vmInit(vm, buf.data(), (uint16_t)buf.size());
  vmRegisterSyscall(vm, sys_print);
  Status s = vmRun(vm, 10000);
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

    def test_disassemble_roundtrip_is_stable(self):
        code = gsc.compile_source("i = 1; while (i <= 3) { i = i + 1; }")
        text = gsc.disassemble(code)
        self.assertIn("JZ ->", text)
        self.assertIn("JMP ->", text)


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

    def run_script(self, source):
        code = gsc.compile_source(source)
        bin_path = os.path.join(self.tmpdir, "prog.bin")
        with open(bin_path, "wb") as f:
            f.write(code)
        result = subprocess.run([self.harness_bin, bin_path],
                                 capture_output=True, text=True, check=True)
        status_line = [l for l in result.stderr.splitlines() if l.startswith("STATUS:")][0]
        status = int(status_line.split(":")[1])
        prints = [int(l) for l in result.stdout.splitlines() if l.strip()]
        return status, prints

    def test_bounded_loop_sum_1_to_5(self):
        status, prints = self.run_script("""
            syscall print(v) = 0;
            sum = 0; i = 1;
            while (i <= 5) { sum = sum + i; i = i + 1; }
            print(sum);
        """)
        self.assertEqual(status, 1)  # VM_HALTED
        self.assertEqual(prints, [15])

    def test_if_else_both_branches(self):
        status, prints = self.run_script("""
            syscall print(v) = 0;
            if (7 > 3) { print(100); } else { print(200); }
            if (2 > 3) { print(100); } else { print(200); }
        """)
        self.assertEqual(status, 1)
        self.assertEqual(prints, [100, 200])

    def test_logical_and_or_not(self):
        status, prints = self.run_script("""
            syscall print(v) = 0;
            print((1 && 0) + (1 || 0) + !0);
        """)
        self.assertEqual(status, 1)
        self.assertEqual(prints, [2])  # 0 + 1 + 1


if __name__ == "__main__":
    unittest.main()
