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

- [x] **Resolved (2026-10-27): script persistence across reboot —
      implemented, but at the consuming-project level, not in this
      library.** `SCRIPTLOAD`/`SCRIPTAPPEND`-loaded scripts are still
      RAM-only as far as `ScriptRuntime` itself is concerned; USBrepeater
      added `SCRIPTSAVE`/`SCRIPTRESTORE` plus boot-time auto-restore on top
      of it (`src/USBrepeater.cpp`, "Script slot persistence" in Section
      6b), using `LittleFS_Program` (Teensy-only) to save/restore a slot's
      raw `code[]` buffer verbatim — no new wire format needed, since that
      buffer already *is* `SCRIPTLOAD`'s `[codeLen][code][pool]` payload.
      Deliberately not pulled into `ScriptRuntime`/`GAACEScript.h`
      themselves: this library's whole value is staying hardware-agnostic
      (no `Serial`, no Teensy headers in the VM core — see the syscall-
      boundary design in `USBrepeater/TODO.md`), and `LittleFS_Program` is
      Teensy-specific. `ScriptSlot::code[]`, `VM::codeLen`, and
      `VM::poolLen` being public fields was exactly what made this
      possible to build entirely in the consuming project with zero
      changes here — worth keeping that way if a different project ever
      wants its own persistence mechanism (an SD card, a different flash
      API, etc.). See `USBrepeater/TODO.md` for the actual implementation
      and its real-hardware-testing status.
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

- [x] **Resolved (2026-10-27): `SCRIPTAPPEND`/`SCRIPTCOMMIT`.** `SCRIPTLOAD`'s
      hex argument is decoded through commandProcessor's shared 512-byte
      `charAllocate` scratch arena, so one `SCRIPTLOAD` call can only ever
      deliver roughly half that in decoded bytes — raising
      `GAACE_SCRIPT_MAX_CODE_LEN` past that doesn't help, the payload
      simply can't arrive in one piece. `SCRIPTAPPEND,<slot>,<offset>,
      <hexchunk>` + `SCRIPTCOMMIT,<slot>,<totalLen>` assemble a slot's
      buffer across many small calls instead, each individually well under
      the arena's ceiling regardless of total script size. `gsc.py
      --upload` picks single-`SCRIPTLOAD` vs. chunked automatically based
      on size (`--chunk-bytes`, default 200). `GAACE_SCRIPT_MAX_CODE_LEN`
      is now meaningfully raisable (it bounds what `SCRIPTAPPEND` can
      ultimately assemble, not what one command can transfer) — ultimately
      a USBrepeater `platformio.ini` decision, not this repo's.
      A script that leans heavily on `cmd()` still grows its string pool
      inside the same per-slot budget as its code — watch this if scripts
      get more ambitious, chunking doesn't change that tradeoff.
- [x] **Resolved (2026-10-27): USBrepeater pin.** It's building against
      current `main` (confirmed via its own `Dependency Graph` output,
      commit `32139a2` — the one with `cmd()`/floats/`--upload`/this note's
      own predecessor). This item was stale when written; likely true
      immediately after a fresh `lib_deps` pull, false again after any
      `rm -rf .pio` since `lib_deps` tracks a branch HEAD, not a pin. Not
      a real pin — re-verify by reading `Dependency Graph` output rather
      than assuming either way.
