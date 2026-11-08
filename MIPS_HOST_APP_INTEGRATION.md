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

- **Windows:** python.org's *embeddable package* (about 12 MB zip), which is the
  real interpreter and is signed by the Python Software Foundation, in a
  `python` folder beside `MIPS.exe`.
- **macOS:** a python-build-standalone build (arm64) in
  `MIPS.app/Contents/Resources/python`. It has to be in `Resources`: `codesign
  --deep` rejects it in `Contents/MacOS` or `Contents/Frameworks`. Every
  Mach-O file in it is signed explicitly (`tools/python/sign_macos_python.sh`)
  before the app is signed, and notarization covers it like any bundled code.
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

## Qt app implementation plan

Based on a read-only review of `MIPSapp/MIPS_QT6` (branch `dev`, 2026-11-07).
Scope decisions: **serial-connected systems only** (the `MIPS::Systems` list;
Ethernet devices found by `GAACEDiscovery` live in `ControlPanel` and are out
of scope), **the user picks where `.gs` files live** (file dialogs, no managed
folder), **macOS and Windows only** (Linux builds are not a concern).

### What the existing app code means for the design

- **Two build files.** `MIPS.pro` (qmake) is the real build; `CMakeLists.txt`
  mirrors its file list. Every new file is added to both.
- **Bundling precedent.** `tools/bossac` is copied into the macOS bundle /
  Windows release folder by `copyBossac` rules in `MIPS.pro`, and `program.cpp`
  runs it with `QProcess`. The Python runtime and `gsc.py` follow the same
  pattern.
- **`Comms` cannot yet do what scripting needs.** `SendMessage()` returns one
  line and treats an ACK-only reply as "no reply" (waits, then retries);
  `SendCommand()` returns `true` on a timeout; neither handles multi-line
  replies (`GSYSCALLS`, `GCMDS`); and the 10,000-character `RingBuffer` drops
  characters when full, so a long `GCMDS` list must be drained as it arrives.
- **Blocking, GUI thread.** `Comms` busy-waits on the GUI thread. An upload of
  a few KB freezes the window for a second or two, which is acceptable.
  Because every chunk is a self-contained, offset-addressed command, the
  event loop may run between chunks (progress bar) without harm.
- **`Systems` is rebuilt, and old `Comms` objects are deleted**
  (`FindAllMIPSsystems`, `MIPSconnect`). The scripting dialog must hold a
  system's *name* and resolve it with `FindCommPort` on every operation, never
  keep a `Comms*`.
- **Naming.** Tools -> Scripting is the existing PC-side JavaScript console.
  The new item is "Device Scripts".

### Pieces

1. **`Comms::Transact(message, &lines, timeoutMs, idleMs)`** (small addition to
   `comms.h/.cpp`): returns Ack, Nak or Timeout, collects reply lines, and with
   `idleMs` keeps reading until the device is quiet (for `GSYSCALLS`/`GCMDS`),
   emptying the ring buffer as it goes. Reuses the `serialBusy` guard.
2. **`devicescriptproto.{h,cpp}`**: pure protocol helpers with no `Comms` or
   widgets, so they can be unit-tested away from hardware: parse `GSYSCALLS`,
   `GCMDS`, `GSCRIPTLIMITS`, `GSCRIPTST`; the FNV-1a table hash (pinned to
   the same test vector as the firmware and `gsc.py`); the JSON files
   `gsc.py --syscalls/--commands` read; and the upload command sequence
   (single `SCRIPTLOAD`, or `SCRIPTAPPEND` chunks + `SCRIPTCOMMIT`, hash as the
   last argument) as a pure function mirroring `gsc.py`'s `_upload_on`.
3. **`DeviceScriptClient`**: runs those sequences over a `Comms`: query tables,
   upload with retry-once on a timeout (safe: chunks are offset-addressed),
   slot status, start/stop via the `threadCommands` threads. A NAK on
   `GSCRIPTLIMITS` means "no scripting on this device".
4. **`GscRunner`** (phase 2): finds the bundled Python and `gsc.py`, runs
   `python gsc.py - --json --syscalls T --commands C` with the source on stdin,
   parses the JSON.
5. **`DeviceScriptDialog`**: a modeless dialog modelled on `ScriptingConsole`,
   opened from a new Tools menu action. Phase 1: system and slot pickers,
   device limits and syscall list, "send a precompiled `.hex`" with an optional
   table hash, slot status, a log. Phase 2 adds the editor, Check/Send, the
   error and warning list (click to jump to the line).
6. **Small edits to existing files:** `mips.ui`/`mips.cpp` (action, member,
   slot), `properties.*` (remember the last `.gs`/`.hex` folder: two lines in
   `Save`/`Load`, one field), the About dialog (show the `gsc` version),
   `Revision.md`, `MIPS.pro` and `CMakeLists.txt` (file lists).
7. **Packaging** (phase 3): runtime + `gsc.py` under `tools/`, copy rules next
   to `copyBossac` for macOS and Windows. `macdeployqt` does not sign the
   Python interpreter, so sign it explicitly with the hardened runtime before
   notarization. Keep `gsc.py` in the app repo with its version recorded (like
   `bossac`) and have the app check `--version` at startup. Include Python's
   license text.

### Phases

| Phase | Work | Rough effort |
| --- | --- | --- |
| 1 | `Comms::Transact`, protocol helpers + tests, `DeviceScriptClient`, minimal dialog that uploads a precompiled `.hex` | 3-5 days |
| 2 | `GscRunner`, editor, error/warning list, Check/Send | 3-4 days |
| 3 | Bundle the runtime for macOS and Windows, signing, version display | 2-4 days |
| 4 | Remember which source went to which slot, offer recompile after a firmware change, status and start/stop polish | 2-3 days |

Phase 1 should not be trusted until the firmware handlers have run on real
hardware (see `TODO.md`).

### Phase 1 status

Built 2026-11-07 on the `dev` branch of `MIPS_QT6` (uncommitted there; see
that repo's `Revision.md`): `Comms::Transact()`, `devicescriptproto.*` (24
unit tests in `tests/devicescriptproto_test`),
`devicescript.*` (the client), `devicescriptdialog.*` and the Tools -> Device
Scripts menu item. The whole app builds with Qt 6.7.1 (qmake). A throwaway
integration test drove `Comms::Transact()`, the client and the dialog against a
fake device on a loopback TCP socket: limits, syscall table, a 501-line
`GCMDS` reply (about 33 KB, in one burst), NAK, timeout, single and chunked
uploads with the hash, a hash rejection, and sending from the dialog. That test
caught a real bug (`waitForReadyRead()` triggers `readData2RingBuffer()`,
which would have taken the reply before `Transact()` saw it).

### Phase 2 status

Built 2026-11-07 (also uncommitted, on `dev`): `GscRunner` (`gscrunner.*`, runs
`gsc.py --json` with the device's tables as temporary files, checks the
`gsc.py` version, handles a missing Python, bad output, a hung compiler and
non-ASCII source), `CodeEditor` (`codeeditor.*`: line numbers and a
highlighted problem line), and the reworked Device Scripts dialog: source
editor, New/Open/Save/Save As (the user picks the location; unsaved changes are
asked about on close), Check, Send, a clickable error and warning list, and the
phase 1 hex sending kept under "Send a precompiled .hex file instead". Sending
a script stamps it with the table hash only if it uses syscalls (the compiler
decides). The syscall table and command list are read when a system is
selected and kept in memory only, per the decision above. The app's copy of
`gsc.py` lives in `tools/gsc/` (a copy of this repo's `tools/gsc.py`) and is
copied next to the executable by `MIPS.pro`; development builds also find it
in the source tree, and `GSC_PATH` overrides it.

Tested: 31 unit tests (including `gsc.py`'s real `--json` output), and the
throwaway loopback test now also drives the dialog end to end with the real
`gsc.py` against the fake device: a warning for an unknown command, a script
using a syscall sent with the hash, a located syntax error that blocks the send
and puts the cursor on the right line, an unknown syscall, an oversize script,
and the failure messages for an old or broken compiler. Until Python is bundled
(phase 3) the app needs a system `python3`.

### Phase 3 status

Built 2026-11-07 (also uncommitted, on `dev`): the bundled runtime. Python
3.14.10 on both platforms, pinned with checksums in `tools/python/versions.txt`;
`fetch_python.sh` / `fetch_python.ps1` download, verify and prune it (not
committed: about 47 MB on macOS); `MIPS.pro` copies it and `gsc.py` into the
build; the release, pre-release and build workflows fetch it, the Windows deploy
step copies it, and the macOS workflows sign it (`sign_macos_python.sh`, before
the app is signed; the Developer ID workflow `MACsigning.yml` too). The app finds
the bundled runtime first and runs it with `-I -B` (isolated; never writes
`.pyc` files, which would change the signed bundle). About shows
"Device Scripts compiler: gsc 1.2.0, Python 3.14.10". Full details in the app
repo's `tools/python/README.md`.

Tested on macOS (arm64): `gsc.py`'s own 115 tests pass under the bundled
Python 3.14.10; the runtime survives a build into `MIPS.app`, signs (ad hoc,
hardened runtime) with `codesign --deep` and verifies with `--strict`; the signed
bundle runs the whole loopback test (dialog, real compiler, fake device) using
only the bundled Python and `gsc.py`, and still verifies afterwards. One real
finding: the runtime must live in `Contents/Resources`, because `codesign --deep`
fails on it anywhere in `Contents/MacOS` or `Contents/Frameworks`.

**Not tested:** anything on Windows (the PowerShell fetch script, the `.pro` copy
rule, the deploy step, the console-window suppression), the Developer ID
signature and notarization (they need the certificate and Apple credentials),
and the GitHub Actions edits (none has been run). Windows signing needs no
change: the runtime's files are already signed by the Python Software
Foundation, and the signing workflow does not set the action to recurse into
subfolders (worth confirming).

### Hardware results (2026-11-08)

First run on a real USBrepeater (Teensy 4.1, alone, no downstream device), from the Mac
app. Passed: the device is found; limits, syscall table (hash `6501A214` matches the
firmware's), and the 92-command `GCMDS` list over real USB serial; compile, check and
send for a command script, a float-argument script (`SADCFILTER,12.5` read back as
12.5), a syscall script (sent with the hash), and a chunked 429-byte upload (reassembled
correctly: it ran and returned the right value); located errors and the unknown-command
warning; the replace prompt; oversize refusal; save/open; unplugging mid-use.

Two bugs only hardware could show, both fixed: `GSCRIPTST` prints `TRUE`/`FALSE` for the
loaded flag (our docs and fakes said `0|1`), and the replace prompt was skipped when that
reply could not be parsed. See `TESTING_CHECKLIST.md` for what is still untested
(the command-line compiler against the device, Windows, notarization).

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
- **Qt version.** Qt 6 only (built and tested with 6.7.1; the app's own
  `MIPS.pro` notes 6.8.2). Qt 5 is not a target, so new code may use Qt 6 APIs.
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
