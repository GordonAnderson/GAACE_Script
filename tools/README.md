# tools/

`gsc.py` — the GAACE_Script compiler. Turns a small C-like script into the
bytecode the VM in `../src` runs. This is host-side tooling; nothing here
runs on the device.

## Language

```
syscall print(v) = 0;      // declares: CALL opcode id 0, takes 1 argument
                            // id must match registration order of
                            // vmRegisterSyscall() calls in firmware

sum = 0;
i = 1;
while (i <= 5) {
  sum = sum + i;
  i = i + 1;
}
print(sum);

if (sum > 10) {
  print(100);
} else {
  print(200);
}
```

- Two numeric types: `int` (int32) and `float` (IEEE754 float32). No
  strings. A stack/variable slot is just 4 bytes with no type tag anywhere —
  it's the opcode (`ADD` vs `FADD`, etc.) that carries the type, decided by
  the compiler at compile time; the VM does no runtime type checking.
- Float literals need a decimal point (`3.5`, `0.0`); `3` is always `int`.
  `+ - * /` auto-promote int to float when mixed with a float operand; `%`
  has no float form. Assigning `int` to a `float` variable auto-promotes;
  assigning `float` to an `int` variable is a compile error — cast
  explicitly: `(int)x` / `(float)x` (truncates toward zero, like a C cast).
- Variables: plain slots, auto-assigned (and typed) on first assignment
  (max 16). Reading a variable before it's ever been assigned is a compile
  error. `var NAME;` / `var NAME: float;` reserves a slot without assigning
  it (default type `int`) — for a value meant to persist *across* separate
  `vmRun()` calls (a periodic script whose caller only resets `pc`/`sp`
  between ticks, not `vars`), so the script can read it before ever
  writing it in the current run.
- `syscall NAME(...) : float = ID;` declares a syscall whose C++
  implementation returns a float (bit-cast into the same `int32_t` return
  value — see `../src/GAACEScript.h`); omit `: float` for an `int`-returning
  syscall (the default). Arguments aren't type-checked — the compiler just
  evaluates and pushes each one; it's up to the syscall's C++ side to
  interpret an argument as int or float as documented/agreed.
- `&&` / `||` do **not** short-circuit — both sides always evaluate. Both
  require `int` operands (comparisons already produce `int`, so
  `a > b && c > d` works fine either way); using a `float` directly is a
  compile error.
- Operators: `+ - * / % == != < <= > >= && || !`, and parentheses.
- A bare call used as a statement (e.g. `print(x);`) discards its return
  value.

## Usage

```
python3 gsc.py program.gs -o program.bin              # raw bytecode
python3 gsc.py program.gs --format hex                 # ASCII hex to stdout
python3 gsc.py program.gs --format carray --carray-name script
                                                        # C array, for pasting
                                                        # straight into firmware
python3 gsc.py program.gs --disasm                     # print disassembly
```

## Tests

```
python3 -m unittest tools/test_gsc.py -v
```

Structural tests need nothing but Python. The end-to-end tests additionally
build the real VM (`../src/GAACEScript.cpp`) with `g++` and run compiled
bytecode through it — this is what actually catches the compiler's opcode
numbering drifting out of sync with the VM, and is skipped automatically if
`g++` isn't on `PATH`.
