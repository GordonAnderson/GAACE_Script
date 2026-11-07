# MIPS host app integration

Design note for letting the MIPS Qt application (`MIPSapp/MIPS_QT6`) edit,
compile and download GAACE_Script scripts to a connected device.

**Status: design only (2026-11-06). Nothing here is implemented.** No code
in the Qt app or in this repo has changed for it.

## Decision

- The **Qt app does the device side**: it already owns the connection, so it
  queries the device's syscall table, uploads the script, and shows slot
  status, all over its existing `Comms` link.
- The **compiler stays `tools/gsc.py`**, and the app runs it as an external
  process (`QProcess`). There is one compiler, so there is one
  implementation of the bytecode format to maintain. A C++ port is *not*
  planned.

## Why the app, and why not just `gsc.py --upload`

- `gsc.py --upload` opens the serial port itself. If the app is already
  connected, a second program can't open the same port, so the user would
  have to disconnect, run the tool, and reconnect. The app can upload over
  the connection it has.
- The app also reaches Ethernet devices (`GAACEDiscovery`, TCP), which
  `gsc.py` can't.
- It fits the UI: a script editor, a syscall list taken from the device, a
  "save to slot" button, slot status.

## What exists in the Qt app today

(Read-only review of `MIPSapp/MIPS_QT6`.)

- `Comms` owns the `QSerialPort` and `QTcpSocket`, with ACK/NAK handling,
  retries, and synchronous `SendMessage()`/`SendCommand()`/`getline()`.
- `GAACEDiscovery` finds Ethernet devices (UDP discovery).
- `scriptingconsole.cpp` runs JavaScript in a `QJSEngine` **on the PC**. That
  is a different thing from GAACE_Script, which is bytecode that runs **on the
  device** with no PC attached. The UI needs to keep the two clearly apart.
- `program.cpp` bundles `bossac` and runs it with `QProcess`. That is the
  precedent for shipping and launching an external tool.
- No support yet for `SCRIPTLOAD`, `SCRIPTAPPEND`, `SCRIPTCOMMIT`,
  `GSCRIPTST`, `GSCRIPTLIMITS` or `GSYSCALLS`.

## How the pieces fit

```
 script text (.gs)
        │
        ▼
 Qt app ── GSYSCALLS ──► device            1. ask the device what it can do
   │  ◄── table + hash ──┘
   │  write table to a temp JSON file
   ▼
 QProcess: gsc.py --syscalls table.json --scriptload src.gs
   │  ◄── payload hex (stdout), errors (stderr, exit 1)
   ▼
 Qt app ── SCRIPTLOAD / SCRIPTAPPEND… SCRIPTCOMMIT ──► device
           (with the table hash as the last argument)
```

1. **Syscall table.** The app sends `GSYSCALLS` through `Comms`, parses the
   reply (`count,HASH8HEX`, then `index,name,argc,int|float` per syscall),
   and writes it in the JSON format `gsc.py --syscalls` reads
   (`{"hash": "XXXXXXXX", "syscalls": [{"name","argc","returns"}]}`) in a
   temporary file. The table is held in memory for the connection and is not
   stored anywhere (see "Compiling needs a connected device"). A firmware
   without `GSYSCALLS` (NAK) means compiling with explicit `syscall`
   declarations instead.
2. **Compile.** The app runs `gsc.py` with `--syscalls` and `--scriptload`.
   Nothing here touches a serial port, so compile-only use needs no
   pyserial (`gsc.py` imports it lazily, only for `--upload` and
   `--list-syscalls`). A compile error is `error: ...` on stderr with exit
   code 1; the app shows it in the editor.
3. **Upload.** The app sends the payload itself: a single
   `SCRIPTLOAD,<slot>,<hex>[,<hash>]` if it fits, otherwise
   `SCRIPTAPPEND,<slot>,<offset>,<hexchunk>` calls and a final
   `SCRIPTCOMMIT,<slot>,<totalLen>[,<hash>]`. The chunk size is 200 decoded
   bytes, as in `gsc.py --upload` (`--chunk-bytes`). The hash is passed only
   when the script calls syscalls, so the device can refuse a script built
   against a different table. `GSCRIPTLIMITS` tells the app the slot size
   before it tries.

The upload logic (steps 1 and 3) is the part that gets rewritten in C++ on
top of `Comms`. It is short, and the wire protocol is already documented in
`README.md` and `GAACEScriptRuntime.h`. The compiler (lexer, parser, code
generator, hash) is not rewritten.

## Shipping Python with the app

**Decision (2026-11-06): bundle Python's own runtime and ship `gsc.py` as a
plain text file; the app runs `python gsc.py --json ...` with `QProcess`.**
Frozen executables (PyInstaller, Nuitka and similar) are ruled out: their
generic launchers are commonly flagged as malware by antivirus products,
which has already been a problem for this project.

- **Windows:** python.org's *embeddable package* (about 10 MB), which is the
  real interpreter and is signed by the Python Software Foundation.
- **macOS / Linux:** a standalone Python build placed inside the app bundle /
  install folder. On macOS the interpreter (and any library inside the
  bundle) must be code-signed with the app's identity and covered by
  notarization, as for any bundled executable.
- **`gsc.py` itself** sits next to it as source. Updating the compiler means
  replacing that one text file; nothing is built per platform.
- **The app finds the interpreter** by looking for the bundled one first. A
  developer build may fall back to a system `python3` on the path, and should
  say clearly in the UI if neither is found.
- **Version:** the compile path uses only the standard library (pyserial is
  imported lazily, for `--upload`/`--list-*` only, which the app doesn't use).
  The full test suite passes on Python 3.9.7 and 3.12.5; a grep found no
  syntax newer than that, but 3.8 and older are untested. Bundle a currently
  supported release (3.12 was the one tested), not 3.9, which is end-of-life.
- **Log the versions:** the app should record the `gsc` version
  (`gsc.py --version`) and the interpreter version it uses, so a bug report
  says which compiler produced a given payload.
- **Fallback if this proves troublesome:** a C++ port of the compiler. It
  removes Python and all packaging concerns, but at the cost of two
  implementations of the bytecode format to keep in step (see the original
  analysis above); revisit only if bundling is a real problem.

## `gsc.py` support for being driven by the app

Done (2026-11-06, `gsc.py` 1.2.0; documented in `tools/README.md`):

- **`--json`**: compile only, one JSON object on stdout. Success carries the
  `SCRIPTLOAD` payload hex, lengths, `uses_syscalls`, the `syscall_hash` to
  send (null if the script doesn't use syscalls) and a disassembly. Failure
  carries `error.message`, `line`, `column`, `offset` and `source_line`.
- **Error locations.** Every compile error now carries a position (parse and
  lexer errors, and code-generation errors such as an undeclared syscall or
  a float used with `&&`, which previously had none). Plain-text mode prints
  `error: line 2, column 5: ...`.
- **`--version`** and a `version` field in the JSON.
- **Command list.** `gsc.py --list-commands PORT --save-commands FILE` (and
  `--upload`) read the device's `GCMDS` list; `--commands FILE` makes `--json`
  return `warnings` for any `cmd()` name the device doesn't have, with line
  and column. The app can fetch `GCMDS` itself over `Comms`, save the same
  JSON (`{"commands": [{"name","help"}]}`), and pass it with `--commands`;
  the names and help strings also suit editor auto-completion.
- The compile path is stdlib-only (pyserial is imported lazily for
  `--upload`/`--list-*` only), so the bundled runtime needs no extra packages.

## Compiling needs a connected device

**Decision (2026-11-06): compiling and sending a script require a connected
device.** The device is the only source of truth for its syscalls (and for
the command names `cmd()` can call), so the app reads both from it when it
connects, keeps them in memory, and compiles against them. There is no
syscall file for anyone to create, name, share or keep in step with the
firmware, and no persistent cache that could go stale or need migrating when
the app updates.

- **Offline:** the user can still open, write and save scripts as text. "Check"
  and "Send to device" are disabled (with a note saying a device is needed)
  until one is connected.
- **On connect / device change:** the app fetches `GSYSCALLS` and `GCMDS` for
  that connection; with several devices, each connection has its own tables.
  A script is compiled against the table of the device it will be sent to.
- **Stale or wrong tables can't ship a bad script:** the table's hash goes with
  the script, and the device refuses the load if its own table differs.
- **CLI:** `gsc.py --upload` already works the same way (it asks the device).
  `--syscalls FILE` / `--commands FILE` remain for automated builds and
  tests, not for everyday authoring.

*If offline compiling is ever wanted:* store tables in the platform cache
folder (`QStandardPaths::CacheLocation`), one entry per table hash with a
schema number; verify an entry by recomputing its hash on load and ignore
entries that fail either check, so an app update silently falls back to
refetching. Needs a rule for what identifies a "device type" (model plus
firmware version from `GVER`). Not planned.

## What the author saves: the source, never the bytecode

**Decision (2026-11-06).** A script's `.gs` source is the only thing an author
saves. Bytecode is produced on each Check/Send and is not stored by the app
as a deliverable.

- **Why:** bytecode depends on the device's syscall table and on the compiler
  version. After a firmware update changes the table, old bytecode is refused
  (the load-time hash check) and must be rebuilt; the source is unaffected.
- **The bytecode still exists on the device,** in its slot, and a project may
  save it to flash so it survives a reboot (USBrepeater's `SCRIPTSAVE`/
  `SCRIPTRESTORE`). That is device-side persistence, not something the author
  manages.
- **The device does not keep the source,** and bytecode can't be turned back
  into it (names and comments are gone; only a disassembly is possible). So
  the app must keep the source, and should record which source (and which
  table hash it was compiled against) went to which device and slot.
- **After a firmware update that changes the syscall table,** the app should
  offer to recompile and resend the scripts that were on that device, using
  the recorded sources. A script the device refused at boot as stale is the
  same case.
- **Build-time scripts** (USBrepeater's ADC demo, generated into
  `include/ScriptBytecode.h`) are firmware build artifacts, outside this
  workflow.

## Editing and showing errors

- **Authoring happens in the app.** The app holds the script text and
  provides the editor; `gsc.py` never edits anything, it receives text (on
  stdin, `-`) and returns bytecode or an error.
- **Error display.** The app runs `gsc.py --json` on save (or on a "Check"
  button), and on `ok: false` it:
  1. marks `error.line` in the editor (underline or gutter marker; `column`
     is 1-based, and `source_line` is the text of that line if the editor
     wants to show it without re-reading);
  2. lists `error.message` in an error panel under the editor, where
     upload failures (NAK from the device, no response) are also shown;
  3. does not enable "Send to device" until the script compiles.
  Errors with a null `line` (for example an unreadable syscall-table file)
  go to the panel only.
- Only the first error is reported: the compiler stops at the first one.
  That is enough for a small-script editor; reporting several would be a
  later parser change.

## Trade-offs accepted

- **A process launch per compile** and a bundled interpreter to ship, sign and
  update.
  Compiles are small and infrequent, so speed doesn't matter.
- **The app depends on `gsc.py`'s command line and output format.** Treat
  those as a contract: the existing `tools/test_gsc.py` tests, plus one test
  that runs the real CLI the way the app will, keep it honest.
- **Editor features** (completion, live error squiggles) come from running
  `gsc.py` repeatedly rather than from an in-process library. Fine for error
  checking on save; a keystroke-by-keystroke experience would be sluggish.
  If that ever matters, revisit a C++ port then.

## Open questions

- **What does the user edit?** Raw `.gs` text, or a visual builder? This
  changes how much the editor needs from the compiler (a builder could emit
  `.gs` text and reuse the same pipeline).
- **Where do scripts live?** Files in a project folder, or stored inside the
  app's own device/config storage? Persistence on the *device* is a separate
  matter, handled per project (USBrepeater's `SCRIPTSAVE`/`SCRIPTRESTORE`).
- **Which devices?** The editor fetches the syscall table and command list
  from whichever device is selected (see "Compiling needs a connected
  device"); multi-device sessions need a set per connection.
- **Qt versions.** The project files target both Qt 5 and Qt 6; the new code
  should avoid Qt 6-only APIs.
- **Hardware verification.** The `GSYSCALLS` and hash-checking handlers in
  the firmware have so far only been exercised against a fake serial port;
  see `TODO.md`. The app work should not start before they have been run on
  a real device.

## Suggested order of work

1. Run the existing firmware handlers on real hardware (see `TODO.md`).
2. ~~Add the `gsc.py` changes (`--json`, `--version`, error locations).~~ Done.
3. Qt app: `GSYSCALLS`/`GCMDS` query on connect (held in memory); upload of
   a precompiled payload; slot status display. This alone fixes the port-conflict problem.
4. Qt app: run `gsc.py` through `QProcess`; a plain-text editor with error
   reporting.
5. Bundle the Python runtime and `gsc.py` with the app builds (signing the
   interpreter on macOS).
6. Anything more (visual builder, richer editor) once real use shows what is
   needed.

## Related

- `tools/README.md`: language reference, the string pool, syscall tables,
  and `gsc.py` usage.
- `README.md`: `SCRIPTLOAD`/`SCRIPTAPPEND`/`SCRIPTCOMMIT`/`GSYSCALLS` wire
  commands.
- `TODO.md`: open items, including hardware verification.
