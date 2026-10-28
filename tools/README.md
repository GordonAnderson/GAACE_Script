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

### `cmd()` — calling into the command processor directly

```
cmd("SADCPIN", 5);            // fire-and-forget: send SADCPIN,5
result = cmd("GVER");         // GET-style: no args, capture the response
```

Instead of a hand-written syscall per capability, `cmd("NAME", args...)`
invokes command `NAME` against whatever the embedding project's
`commandProcessor` already exposes — anything a PC/human could type, a
script can call, with zero per-project wiring (`GAACEScriptRuntime.h`'s
`ScriptRuntime` registers the bridge automatically on every slot).

- The literal name is stored once in a string-constant pool compiled
  alongside the code; repeated calls with the same name share one copy.
  The VM core never touches the string itself — `CMDCALL` carries a pool
  offset, and the actual dispatch happens in GAACE_Core's
  `commandProcessor::executeLine()`, which runs the line through the same
  logic a real typed command uses, in complete isolation from whatever a
  human/PC might be mid-typing into the shared input buffer at that same
  moment.
- `cmd()` returns an `int`: the response parsed as a number on ACK, `0` on
  NAK or if the response has no trailing number (e.g. a plain action
  command with no return value). NAK and "ACK with value 0" aren't
  distinguishable from the return value alone.
- `cmdf("NAME", args...)` is identical but returns a `float`: the reply is
  parsed as float32, so `"3.14"` comes back as `3.14` instead of `3`. Use
  it for any command whose reply can be fractional (`volts = cmdf("GVOLT",
  1);`). The script picks `cmd` vs `cmdf` up front because values carry no
  runtime type tag; an int-returning `cmd()` assigned to a `float`
  variable still auto-promotes as usual.
- Arguments aren't type-checked (same as syscalls); floats can be passed,
  the receiving command decides how to interpret them.
- A script that uses `cmd()` compiles to *two* artifacts — code and pool —
  see `--scriptload` below for the wire format that combines them for
  `SCRIPTLOAD`, or use `--format carray` to get both as separate arrays for
  hand-written `vmInit()`/`vmSetPool()` calls.

### The string pool: how `cmd()`/`cmdf()` carry command names

Bytecode can only hold 4-byte numbers, so a command name like `"SADCPIN"`
can't live in the instruction stream. The compiler splits a script into two
blobs: the **code**, and a **pool** of NUL-terminated name strings. Each
command call in the code is `CMDCALL <poolOffset:u16> <argc>` (`CMDCALLF`
for `cmdf()`), where `poolOffset` is the byte offset of its name in the pool.

For `cmd("SADCPIN", 5); cmd("GVER");`:

```
code:   PUSH_I32 5
        CMDCALL  offset=0 argc=1      <- "SADCPIN"
        CMDCALL  offset=8 argc=0      <- "GVER"

pool:   S A D C P I N \0 G V E R \0
        0 1 2 3 4 5 6 7  8 9 10 11 12
```

A name used more than once is stored once; every call shares its offset.

At runtime the VM pops the arguments, finds the name at `poolOffset` (a
bounds-checked scan for the terminating NUL — an offset past the end or a
missing terminator stops the script with `VM_ERR_BAD_POOL`), and hands
`(name, args, argc)` to the bridge, which builds `NAME,arg0,arg1` and runs it
through `executeLine()`. `cmd()` and `cmdf()` use the pool identically; they
differ only in how the bridge parses the reply (int vs float32).

What this means in practice:

- **The pool must be loaded together with the code.** `--upload` and
  `--scriptload` do this (`[codeLen:u16][code][pool]`). Plain `-o` /
  `--format hex` write the code only and warn that the pool was dropped;
  loaded that way, every `cmd()`/`cmdf()` call fails.
- **The pool counts toward the per-slot size limit** along with the length
  prefix and the code (`GAACE_SCRIPT_MAX_CODE_LEN`, see README.md). A script
  that calls many *different* commands spends more of that budget; repeated
  calls to the same command cost nothing extra.
- **Each slot has its own pool.** `SCRIPTLOAD` always sets it, even for a
  script with no pool, so a reloaded slot can't keep pointing at the
  previous script's strings.
- `--disasm` reads the pool to show `CMDCALL "SADCPIN"` rather than a bare
  offset.

## Usage

```
python3 gsc.py program.gs -o program.bin              # raw bytecode (code only;
                                                        # warns if the script uses
                                                        # cmd() and drops the pool)
python3 gsc.py program.gs --format hex                 # ASCII hex to stdout (same
                                                        # code-only caveat as above)
python3 gsc.py program.gs --format carray --carray-name script
                                                        # C arrays: script[]/script_len,
                                                        # plus script_pool[]/script_pool_len
                                                        # if the script uses cmd()
python3 gsc.py program.gs --scriptload --format hex    # [codeLen][code][pool] combined --
                                                        # paste straight into
                                                        # SCRIPTLOAD,<slot>,<hex>
python3 gsc.py program.gs --disasm                     # print disassembly (shows cmd()
                                                        # names resolved from the pool)
python3 gsc.py program.gs --upload /dev/ttyUSB0 --slot 1
                                                        # compile AND send it: builds the
                                                        # --scriptload payload and sends
                                                        # SCRIPTLOAD,1,<hex> to that serial
                                                        # port itself, printing ACK/NAK.
                                                        # Requires pyserial (pip install
                                                        # pyserial). --baud (default 115200)
                                                        # and --timeout (default 3.0s) tune
                                                        # the connection; --disasm can be
                                                        # combined with --upload to preview
                                                        # what's being sent. See WORKFLOW.md.
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
