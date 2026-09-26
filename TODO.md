# TODO

Open issues surfaced while building the VM, compiler, standard runtime, and
`cmd()`/`executeLine()` bridge. Design history and rationale live in
README.md, tools/README.md, WORKFLOW.md, and the commit log — this is just
the working list of what's still unresolved.

## Decisions deliberately deferred

- [ ] **License.** Placeholder `MIT` in `library.json`. Existing GAACE
      libraries aren't consistent (`GAACE_Core` is GPLv3, `ArduinoThread`
      is Public Domain) — on hold at the user's request, not forgotten.
- [ ] **Host-application (Qt) integration.** The user's Qt application
      already talks to hardware and hosts control panels; editing/
      compiling/downloading scripts *inside* it was considered and
      deliberately not pursued yet. The compiler is Python; integrating it
      into a (presumably C++) Qt app means either shelling out to
      `gsc.py` (adds a Python-install dependency wherever the app runs),
      porting the compiler to C++ (a real duplication — two
      implementations of the bytecode format to keep in sync), or only
      integrating the "send precompiled hex" half. None of those are
      small, and the feature itself is still young (no persistence, no
      real product usage yet). Revisit once/if scripting earns its place
      in a real product; the `--upload` CLI flag is the right-sized
      interim answer.

## Known, accepted limitations (not bugs — documented behavior)

- [ ] `cmd()`'s return value can't distinguish NAK from "ACK with value
      0" — both come back as `0`. A script that needs to know for sure
      has to know which commands it's calling and design around it.
- [ ] `cmd()` response parsing is integer-only (`atoi` on the response
      text after the ACK byte). A GET-style command whose value is a
      float (e.g. `"3.14"`) comes back truncated at the decimal point
      (`3`), not rounded or rejected. Getting real float values back
      through `cmd()` would need a parsing convention this doesn't have
      yet.
- [ ] Neither syscalls nor `cmd()` type-check arguments — the compiler
      pushes whatever an expression evaluates to; the receiving syscall/
      command decides how to interpret it. A script can pass a float
      where a syscall expects an int (or vice versa) with no compile-time
      or run-time warning.

## Not yet built

- [ ] **Script persistence across reboot.** `SCRIPTLOAD`-loaded scripts
      are RAM-only; a reboot loses them. The filesystem-based save/load/
      load-on-boot idea (for platforms that have one, e.g. Teensy 4.1's
      LittleFS) is still just an idea — see `USBrepeater/TODO.md`.
- [ ] **A general "device just rebooted" notification.** Raised during
      design discussion as a real gap (a host has no way to know a
      script's state was lost to an unexpected reset) — it's a
      GAACE_Core-level concern independent of scripting, not started.
- [ ] **`stm32` branch parity.** `commandProcessor::executeLine()` (which
      `cmd()` depends on) only exists on GAACE_Core's `main` branch.
      `stm32` is WIP and was deliberately left untouched — whenever that
      branch resumes, the same capability needs porting there by hand
      against its `GStream`/C-string style (`main`'s implementation won't
      cherry-pick cleanly; that file already diverges in exactly this
      area — see the commit that added `executeLine()`).
- [ ] **No CI.** `pio test -e native` and `python3 -m unittest
      tools/test_gsc.py` both run cleanly locally but nothing runs them
      automatically on push.

## Worth remembering, not necessarily broken

- [ ] `SCRIPTLOAD`'s hex argument is decoded through commandProcessor's
      shared 512-byte `charAllocate` scratch arena (see
      `GAACEScriptRuntime.h`'s build-configuration comment). A script that
      leans heavily on `cmd()` grows its string pool inside the same
      `GAACE_SCRIPT_MAX_CODE_LEN` budget as its code — watch this if
      scripts get more ambitious.
- [ ] USBrepeater is still pinned to the `GAACE_Script` commit from before
      `cmd()`/floats/the standard runtime's later refinements existed. The
      capstone proof for all of that so far is `test/test_runtime` (an
      isolated test rig on an Adafruit QT Py M0), not a real product's
      actual command set. Worth doing once there's an actual reason to
      (e.g. USBrepeater's ADC demo rewritten to use `cmd()` instead of its
      three hand-written syscalls).
