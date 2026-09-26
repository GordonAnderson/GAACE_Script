# GAACE_Script

A minimal stack-based bytecode VM for host-downloadable control scripts on
GAACE firmware — no heap, no garbage collector, no board-specific code in
the core.

## Origin

Grew out of design discussion on the
[USBrepeater](https://github.com/GordonAnderson/USBrepeater) project (see
its `TODO.md`), which needed a way to let a host application download small
control programs (conditionals, bounded loops, arithmetic) to run against
multiple downstream devices — without either shipping raw native machine
code (no MPU/OS process isolation on these MCUs, so malformed code can
corrupt or hang the device) or pulling in a full interpreter like
[uLisp](http://www.ulisp.com/) (its default Teensy 4.x heap alone is
~469 KB, and its garbage collector introduces pauses that don't sit well
next to timing-sensitive firmware). This is the alternative: a VM small and
deterministic enough to not be a special case, built as its own reusable
library from day one rather than a one-off inside a single project.

## Design

- **A VM instance is a fixed-size struct** — a 32-entry operand stack, 16
  variable slots, a program counter. No dynamic allocation anywhere. Total
  footprint is well under 1 KB.
- **~30 opcodes, two numeric types.** `int` (int32) and `float` (IEEE754
  float32) — a stack/variable slot is just 4 bytes with no type tag
  anywhere; it's the opcode (`ADD` vs `FADD`, `LT` vs `FLT`, ...) that
  carries the type, decided by the compiler, not tracked at runtime. See
  [tools/README.md](tools/README.md) for the language's promotion/cast
  rules. `if`/`while`-style control flow isn't built in either — the
  host-side compiler lowers it to plain conditional/unconditional jumps,
  the same way any real compiler handles control flow:

  ```
  if (a > b) { send(0, X) } else { send(1, Y) }       while (cond) { body }
  ---------------------------------------------        --------------------
  LOAD a                                                loop:
  LOAD b                                                  <cond>
  GT                                                      JZ  end
  JZ  else                                                <body>
    <then body>                                           JMP loop
    JMP end                                              end:
  else:
    <else body>
  end:
  ```

- **The syscall boundary is what makes this a library, not a one-off.**
  `CALL` invokes a function pointer from a table the *embedding project*
  supplies — the VM core (`src/GAACEScript.h`/`.cpp`) has zero
  hardware-specific code in it: no `Serial`, no board headers, nothing.
  A project registers its own primitives:

  ```c
  typedef int32_t (*SyscallFn)(int32_t *args, uint8_t argc);
  uint8_t vmRegisterSyscall(VM &vm, SyscallFn fn);
  ```

  USBrepeater would register things like `send_device`, `read_adc`,
  `read_ring_buffer`. A future SAM-based GAACE project registers a
  completely different table suited to its own hardware, against the
  identical VM core — proven by this repo's own examples, which build
  unmodified for both Teensy 4.1 and an Adafruit Feather M0 (SAMD21).

- **`cmd()` reaches the command processor directly, with zero per-project
  wiring.** Instead of a hand-written syscall per capability, a script can
  call `cmd("SADCPIN", 5)` against anything the embedding project's
  `commandProcessor` already exposes to a PC/human. The literal command
  name lives in a string-constant pool compiled alongside the code (see
  `CMDCALL`/`vmSetPool()` in `GAACEScript.h`); the actual dispatch runs
  through GAACE_Core's `commandProcessor::executeLine()`, in complete
  isolation from whatever a human/PC might be mid-typing into the shared
  input buffer at that same moment. `ScriptRuntime` (below) wires this up
  automatically for every slot — see tools/README.md's `cmd()` section for
  the language side.

- **The compiler lives on the host, not the device.** The firmware only
  ever needs the interpreter loop — there's no text parser on-device.
  `tools/gsc.py` compiles a small C-like script (variables, `if`/`else`,
  bounded `while`, syscalls) to bytecode on the PC; see
  [tools/README.md](tools/README.md). The example under `examples/` still
  hand-assembles bytecode directly, to demonstrate the VM without depending
  on the compiler.
- **Scripts must be bounded.** `vmRun()` is meant to be called from a
  cooperative scheduler (e.g. `ArduinoThread`), the same way a periodic
  ADC-read-and-inject feature would run today — it must return promptly, so
  loops inside a script need a fixed iteration count, not "loop forever."
  `VM_ERR_STEP_LIMIT` means a script didn't finish within its budget.

## Standard runtime (`GAACEScriptRuntime.h`)

The VM core above is deliberately minimal; most projects won't drive it
directly. `ScriptRuntime` is the standard way to embed N independent
scripts in a GAACE project — mirroring GAACE_Core's `threadCommands`
(same constructor shape, same "one instance, file-scope statics" pattern):

```c++
ThreadController   control;
commandProcessor   cp;
ScriptRuntime       scripts(&cp, &control);   // GAACE_SCRIPT_SLOTS slots (default 4)

cp.registerCommands(scripts.scriptCmdList());
scripts.registerSyscall(sys_read_adc);         // id 0, registered on every slot
scripts.registerSyscall(sys_send_device);      // id 1
```

Each slot is its own named `Thread` ("Script0".."Script&lt;N-1&gt;") added to
`control` — so **starting/stopping a script and changing its rate are not
new commands**, they're GAACE_Core's existing `threadCommands`
(`?TENA,Script0,...` / `?TINT,Script0,...`) used against these threads. The
only genuinely new commands are:

| Command | Purpose |
| --- | --- |
| `SCRIPTLOAD,<slot>,<hex>` | Decode `<hex>` (`gsc.py --scriptload --format hex`, i.e. `[codeLen][code][pool]`) and load it into `<slot>`. NAKs on a bad slot, malformed hex, or a payload over the configured size limit — never silently accepts something it can't run. |
| `GSCRIPTLIMITS` | `slots,maxCodeLen,stackSize,varSlots,maxSyscalls` — lets a host discover the compile-time limits below at runtime instead of hardcoding them. |
| `GSCRIPTST,<slot>` | `loaded(0\|1),lastStatus` — the one piece of VM-specific state `threadCommands` doesn't know about. |

Every limit is a per-project `#define` with a sensible default, so a
resource-tight board and a resource-rich one can both use this without
forking anything:

| Define | Default | What it controls |
| --- | --- | --- |
| `GAACE_SCRIPT_SLOTS` | 4 | Number of independent script slots (Threads) |
| `GAACE_SCRIPT_MAX_CODE_LEN` | 128 | Max `SCRIPTLOAD` payload bytes per slot — the length prefix, code, *and* pool combined, not code alone. See the header comment in `GAACEScriptRuntime.h` for why this also interacts with `commandProcessor`'s fixed 512-byte scratch arena |
| `GAACE_SCRIPT_STACK_SIZE` | 32 | VM operand stack depth |
| `GAACE_SCRIPT_VAR_SLOTS` | 16 | Variable slots per script |
| `GAACE_SCRIPT_MAX_SYSCALLS` | 32 | Syscall table size per VM |

Loading a *default* script at boot (rather than waiting for a host to send
one) doesn't need a new mechanism either — just populate
`scripts.slots[0].code`/`codeLen`, call `vmInit()`, and set `loaded = true`
in `setup()`, the same way `SCRIPTLOAD` does it internally. See
`examples/BasicScript` for exactly that.

Bytecode transport is hex over the command processor's existing ASCII line
protocol, not a new binary framing — `gsc.py --scriptload --format hex`
already produces exactly what `SCRIPTLOAD` expects.

**Not yet built**: persisting a loaded script across a reboot (RAM-only for
now — see `USBrepeater/TODO.md` for the filesystem-based
save/load/load-on-boot idea for platforms that have one), and a general
"device just rebooted" notification (a GAACE_Core-level concern, not
specific to this module).

## Layout

```
src/GAACEScript.{h,cpp}         VM core (now including CMDCALL/pool support)
src/GAACEScriptHex.{h,cpp}      hex encode/decode (no Arduino dependency,
                                 natively testable)
src/GAACEScriptRuntime.{h,cpp}  standard N-slot runtime + commands, incl.
                                 the real cmd() bridge into executeLine()
                                 (Arduino-only; compiles to nothing under
                                 `platform = native`)
lib/GAACE_Script_core           symlink to ../src, so PlatformIO's Library
                                 Dependency Finder picks up all of the
                                 above for every environment
examples/BasicScript/            bounded loop + if/else + syscall round-trip,
                                 loaded into ScriptRuntime slot 0
test/test_vm/, test/test_hex/    native unit tests (Unity), no hardware
test/test_runtime/               on-device capstone test (real commandProcessor,
                                 not a fake bridge) -- runs on adafruit_qt_py_m0
tools/gsc.py                    host-side compiler (script source -> bytecode)
tools/test_gsc.py               compiler tests, including end-to-end runs
                                 against the real VM
```

## Building

```
pio test -e native                                    # unit tests, no hardware
pio run -e teensy41 -t upload                         # BasicScript example, Teensy 4.1
pio run -e adafruit_feather_m0 -t upload              # BasicScript example, SAMD21
pio test -e adafruit_qt_py_m0 --upload-port <port>    # on-device cmd()/executeLine() test
```

## Using it in another project

```ini
lib_deps =
    https://github.com/GordonAnderson/GAACE_Script.git
```

then register whatever syscalls your project needs and call `vmRun()` from
wherever you'd otherwise have hardcoded the behavior.

## Status

VM core, compiler, standard runtime, and `cmd()` (scripts calling directly
into the command processor via GAACE_Core's `executeLine()`) are
implemented and tested — including on real hardware: `test/test_runtime`
proves the full chain (a `gsc.py --scriptload`-compiled script, loaded via
a real `SCRIPTLOAD` command, calling `cmd()` into a real `Command` table)
on an Adafruit QT Py M0, not just against a fake bridge.

Integrated into [USBrepeater](https://github.com/GordonAnderson/USBrepeater)
as a real (if still demo-scoped) feature — see its `TODO.md` for what's
still open there (script persistence across reboot, a boot-notification
primitive, real-hardware validation of that specific integration). License
is still an open decision (placeholder `MIT` in `library.json`),
intentionally not settled yet.
