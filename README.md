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
- **~20 opcodes.** `if`/`while`-style control flow isn't built in — a
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

- **The compiler lives on the host, not the device.** The firmware only
  ever needs the interpreter loop — there's no text parser on-device. A
  script is compiled to bytecode on the PC and sent over to the device
  (e.g. as a command-processor command carrying hex bytes); the examples
  here hand-assemble bytecode directly to demonstrate the VM without
  requiring that host-side tool yet.
- **Scripts must be bounded.** `vmRun()` is meant to be called from a
  cooperative scheduler (e.g. `ArduinoThread`), the same way a periodic
  ADC-read-and-inject feature would run today — it must return promptly, so
  loops inside a script need a fixed iteration count, not "loop forever."
  `VM_ERR_STEP_LIMIT` means a script didn't finish within its budget.

## Layout

```
src/                  VM core — GAACEScript.h / GAACEScript.cpp
lib/GAACE_Script_core  symlink to ../src, so PlatformIO's Library
                       Dependency Finder picks up the core for every
                       environment (native included)
examples/BasicScript/  a bounded loop + if/else + a syscall round-trip
test/test_vm/          native unit tests (Unity), no hardware required
```

## Building

```
pio test -e native                        # unit tests, no hardware
pio run -e teensy41 -t upload             # BasicScript example, Teensy 4.1
pio run -e adafruit_feather_m0 -t upload  # BasicScript example, SAMD21
```

## Using it in another project

```ini
lib_deps =
    https://github.com/GordonAnderson/GAACE_Script.git
```

then register whatever syscalls your project needs and call `vmRun()` from
wherever you'd otherwise have hardcoded the behavior.

## Status

Early scaffold — the opcode set, encoding, and syscall boundary are settled
and tested; what's not yet decided is documented in
[USBrepeater/TODO.md](https://github.com/GordonAnderson/USBrepeater/blob/main/TODO.md)
(host-side compiler, script persistence, USBrepeater's specific syscall
table). Not yet integrated into any product firmware.
