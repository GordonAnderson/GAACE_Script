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
- **Syscalls need no declaration when you compile against a device's
  table.** With `--upload` (which asks the device) or `--syscalls FILE`
  (a saved table), write `volts = get_volts(1);` and nothing else — see
  "Syscall tables" below. The `syscall` declarations in this section are
  for firmware that doesn't describe its syscalls, or to compile with no
  table at all.
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
  NAK or if the response's text isn't a number. A bare ACK (a set or action
  command that succeeded, with nothing after the ACK) returns `1`, so
  `ok = cmd("SADCEN", true);` tells you the set worked. A `TRUE`/`FALSE`
  reply (any case, e.g. from a `CMDbool` getter) comes back as `1`/`0`.
  NAK, `FALSE` and "ACK with value 0" aren't distinguishable from the
  return value alone.
- Bool commands: `true` and `false` are int literals (`1`/`0`), so
  `cmd("SADCEN", true)` sends `SADCEN,1`. GAACE_Core's `CMDbool` and
  `STENA` accept `1`/`0` as well as `TRUE`/`FALSE` (GAACE_Core `main`,
  2026-11-05 or later); a project's own `CMDfunction` bool handler only
  works from a script if it accepts `1`/`0` too.
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

### Devices — talking to other instruments by name

```
q = device("QUAD");                 // handle for the instrument named QUAD
e = device("IsoElectrometer");
if (ready(q) && ready(e)) {
    v = dcmdf(q, "GRFADRV", 1);     // read a value from one instrument
    dcmd(e, "SVOLT", 2, v * 0.5);   // set a value on another
}
```

- `device("NAME")` returns a handle (an `int`). What a name means is up to
  the firmware: on the USBrepeater it's the instrument's `GNAME`, and the
  handle follows the instrument if it's moved to another connector.
- `ready(dev)` is `1` if the instrument can be reached right now, else `0`.
- `dcmd(dev, "CMD", args...)` sends `CMD,args` to it, formatting arguments
  exactly as `cmd()` does (ints as integers, floats as decimals, `true`/
  `false` as `1`/`0`); `dcmdf(...)` is the same but returns a `float`.
  Results follow `cmd()`'s rules: ACK + number -> that number, a bare ACK
  (a set command that worked) -> `1`, `TRUE`/`FALSE` -> `1`/`0`, and `0`
  for a NAK, no reply, or an instrument that isn't there.
- The script **pauses** at `dcmd()`/`dcmdf()` until the reply arrives,
  then carries on from that point; nothing else on the controller waits
  (see "Waiting syscalls" in the top-level README).
- Use single-line commands: the reply is taken to end at its first line.
- These are built-ins, not syscalls, so they don't change the syscall
  table hash. Firmware without device support stops such a script with
  `VM_ERR_NO_DEVICE_BRIDGE` (status 12).

### The string pool: how `cmd()`/`cmdf()` carry command names

Bytecode can only hold 4-byte numbers, so a command name like `"SADCPIN"`
can't live in the instruction stream. The compiler splits a script into two
blobs: the **code**, and a **pool** of NUL-terminated name strings. Each
command call in the code is `CMDCALL <poolOffset:u16> <argc>` (`CMDCALLF`
for `cmdf()`), where `poolOffset` is the byte offset of its name in the pool.

**Command names are not declared, and the compiler can only warn.** Unlike
syscalls, a command used with `cmd()` needs no declaration and no table
entry: the compiler stores the name string in the pool. Whether `"SDRV"`
exists is only discovered on the device, when the line is run. An unknown
command, a typo (`"SDRW"`), or wrong arguments get a NAK from the command
processor, which `cmd()` turns into a return value of `0`; the script keeps
running and the VM reports no error (`GSCRIPTST` looks normal). A script
can't reliably tell that from a command that succeeded with 0.

To catch typos before they reach the device, `gsc.py` can check names against
the device's **command list**. Every GAACE_Core device already answers
`GCMDS` (one `NAME,help` line per command; `?` commands appear as both
`GNAME` and `SNAME`), so no firmware change is needed:

- `--upload` asks the device (`GCMDS`) and warns, e.g.
  `warning: line 2, column 5: the device has no command 'SDRW'; at run time
  cmd() would return 0 -- did you mean 'SDRV'?`. It's a **warning, not an
  error**: the script still compiles and uploads (the list could be out of
  date, or the device case-insensitive).
- `--list-commands PORT` prints the list; add `--save-commands FILE` to keep
  it, and `--commands FILE` to check offline. `--no-device-commands` skips the
  query during `--upload`.
- With `--json`, warnings come back in a `"warnings"` array (same shape as an
  error: `message`, `line`, `column`, ...), so an editor can mark them.

What it does **not** check: argument counts or types (`GCMDS` doesn't report
them), `dcmd()` names (those run on another instrument), or that the command
does what you want. `GCMDS` has no terminator, so the list ends when the
device falls silent for 0.4 s; that adds a short pause to each `--upload`.
Still **test new scripts against the real device**.

**Float arguments.** `cmd("SETV", 23.76);` sends the text `SETV,23.76` —
the compiler knows each argument's type, so a float expression is formatted
as a decimal number (up to 7 significant digits), an int as an integer. A
call with at least one float argument compiles to `CMDCALLM`/`CMDCALLMF`,
which add a 2-byte mask (bit *i* set = argument *i* is a float); all-int
calls keep the original 4-byte form, so existing bytecode is unchanged.
Limit: at most 16 arguments, and the whole command line must fit the
bridge's 64-byte buffer.

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

### Syscall tables: scripts without declarations

If the firmware registers its syscalls with `ScriptRuntime::registerSyscalls()`
(see the top-level README), the device can describe them, and the compiler
uses that description instead of your declarations:

```
volts = get_volts(1);           // no `syscall get_volts(...) = 2;` line needed
if (volts > 3.0) { set_volts(2.5); }
```

- **Source of the table.** `--upload PORT` queries the device itself
  (`GSYSCALLS`) before compiling. `--syscalls FILE` uses a saved table
  instead (no device needed to compile). `--no-device-syscalls` skips the
  query and goes back to explicit declarations only.
- **What it checks.** A call to a name the device doesn't have is a compile
  error that lists what it does have. Argument count and `float` return
  type come from the table, so they can't be mis-declared. If you do write
  a `syscall` declaration for a name in the table, it must match (id,
  arguments, return type) or compilation stops — delete it and let the
  table decide.
- **The load is checked too.** When a script calls at least one syscall,
  `--upload` sends the table's hash as the optional last argument of
  `SCRIPTLOAD`/`SCRIPTCOMMIT`, and the device NAKs if its own table hashes
  differently (the firmware was updated since you saved your table, or
  you're pointed at the wrong device). A script that only uses `cmd()` /
  `cmdf()` isn't stamped.
- **Older firmware.** A device with no table (NAK, or nothing to answer
  `GSYSCALLS`) isn't an error: you get a note, the script must declare its
  own syscalls as before, and the load is unchecked.

```
python3 gsc.py --list-syscalls /dev/ttyUSB0                   # print the device's syscalls
python3 gsc.py --list-syscalls /dev/ttyUSB0 --save-syscalls dev.json
python3 gsc.py prog.gs --syscalls dev.json --scriptload --format hex
                                                              # offline; prints the hash to
                                                              # append to SCRIPTLOAD
```

The saved file is plain JSON (`hash` plus a `syscalls` list of `name`,
`argc`, `returns`); ids are list positions. Re-save it whenever the firmware
changes — an out-of-date file is caught at load time by the hash, not
silently trusted.

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
python3 gsc.py program.gs --upload /dev/ttyUSB0 --slot 1 --save-syscalls dev.json
                                                        # --upload first asks the device for its
                                                        # syscall table (see above); this also
                                                        # keeps a copy for offline use
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
python3 gsc.py program.gs --json --syscalls dev.json
                                                        # compile only, report as ONE JSON
                                                        # object (see "Driving gsc.py from
                                                        # another program" below)
python3 gsc.py --list-commands /dev/ttyUSB0 --save-commands cmds.json
                                                        # the device's cmd()-callable commands
python3 gsc.py program.gs --commands cmds.json ...     # warn about unknown cmd() names offline
python3 gsc.py --version                               # gsc 1.2.0
```

`--upload`, `--list-syscalls` and `--list-commands` need pyserial. If you see "the
'serial' module found here is not pyserial", the PyPI package named `serial` (a
different project) is installed: `pip uninstall serial; pip install pyserial`.

### Compile errors

A compile error is reported with its location:

```
error: line 2, column 5: call to undeclared syscall 'foo' -- declare it ...
```

The location points at the offending token or expression (for an operator
error, at the operator). Exit code is 1.

### Driving gsc.py from another program (`--json`)

`--json` compiles only (it can't be combined with `--upload`/`--list-syscalls`)
and prints exactly one JSON object on stdout; stderr stays empty. Exit code
is 0 on success, 1 on error. Add `--syscalls FILE` to compile against a saved
device table. This is the interface the MIPS host app uses; see
[../MIPS_HOST_APP_INTEGRATION.md](../MIPS_HOST_APP_INTEGRATION.md).

```
{"version": "1.2.0", "ok": true,
 "payload": "0d00190000c03f29...",   // [codeLen][code][pool]: what SCRIPTLOAD / SCRIPTAPPEND carry
 "code_len": 13, "pool_len": 5,
 "uses_syscalls": false,
 "syscall_hash": null,                // 8 hex digits: last arg of SCRIPTLOAD/SCRIPTCOMMIT; null if unused
 "disasm": "   0: PUSH_F32 1.5\n ...",
 "warnings": []}                       // same shape as "error"; e.g. an unknown cmd() name

{"version": "1.2.0", "ok": false,
 "error": {"message": "unexpected token ';' (';')",
           "line": 2, "column": 5, "offset": 11,   // 1-based line/column; offset is a 0-based character index
           "source_line": "y = ;"}}                 // line/column/offset/source_line are null if the error has no location
```

`version` is gsc.py's `__version__`; it changes whenever the command line or
this output changes in a way a calling program could notice. Errors that
aren't about the script (an unreadable source or `--syscalls` file) come
back as `{"ok": false, "error": {"message": "..."}}` with no location.

## Tests

```
python3 -m unittest tools/test_gsc.py -v
```

Structural tests need nothing but Python. The end-to-end tests additionally
build the real VM (`../src/GAACEScript.cpp`) with `g++` and run compiled
bytecode through it — this is what actually catches the compiler's opcode
numbering drifting out of sync with the VM, and is skipped automatically if
`g++` isn't on `PATH`.
