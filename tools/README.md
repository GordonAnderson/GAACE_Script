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

- Types: everything is `int32_t`. No floats, no strings.
- Variables: plain slots, auto-assigned on first assignment (max 16).
  Reading a variable before it's ever been assigned is a compile error.
- `&&` / `||` do **not** short-circuit — both sides always evaluate.
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
