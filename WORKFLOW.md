# Writing and downloading a script

This is the practical, step-by-step version of what's designed in
[README.md](README.md) and [tools/README.md](tools/README.md) — how you
actually get a script running on a controller, without reflashing firmware.

Assumes the project already has `GAACEScript::ScriptRuntime` wired up (see
README.md's "Standard runtime" section) and its command port reachable over
a serial terminal.

## 1. Write the script

A plain `.gs` file. Call the project's syscalls by name (the device tells
the compiler what they are — nothing to declare), or call straight into
whatever its command processor already exposes with `cmd()`/`cmdf()` — no
firmware change needed either way. Example (`drive_limit.gs`):

```
watts = read_power();
if (watts > 100.0) {
  drive = max_drive() - 5.0;
  set_max_drive(drive);
}
```

## 2. Compile it and send it

```
python3 tools/gsc.py drive_limit.gs --upload /dev/ttyUSB0 --slot 1
```

Opens that serial port, asks the device for its syscall table
(`GSYSCALLS`) and compiles against it — so `read_power()` above resolves to
the firmware's real syscall, with its real id, argument count and `float`
return type, and a typo or a syscall the device doesn't have is a compile
error here instead of a wrong call on the device. (Firmware without a table
works as before: declare each syscall at the top of the script with
`syscall NAME(...) = ID;`.) It then sends the result (the code and
string-constant pool combined with the `[codeLen][code][pool]` length
prefix the device expects). For a small script that's a single
`SCRIPTLOAD,1,<hex>`; past `--chunk-bytes` (default 200 decoded bytes —
`SCRIPTLOAD`'s real ceiling, see `GAACEScriptRuntime.h`), it switches to
several `SCRIPTAPPEND,1,<offset>,<hexchunk>` calls finished with
`SCRIPTCOMMIT,1,<totalLen>` automatically — nothing to choose by hand:

```
OK: script loaded into slot 1 (12 bytes, single SCRIPTLOAD)
```
```
OK: script loaded into slot 1 (640 bytes, 4 SCRIPTAPPEND chunks + SCRIPTCOMMIT)
```

or, on failure:

```
NAK: device rejected SCRIPTLOAD (bad slot/offset, malformed hex, over the
configured size limit -- see GSCRIPTLIMITS -- or, if a syscall table hash was
sent, the firmware's syscalls differ ...)
```

A script that calls syscalls is sent with the table's hash, and the device
NAKs the load if its own table differs — so a script is never run against
syscalls other than the ones it was compiled for. `--list-syscalls PORT`
shows what a device offers; add `--save-syscalls dev.json` to keep a copy and
compile later with `--syscalls dev.json` and no device attached.

Requires `pyserial` (`pip install pyserial`). `--baud` (default 115200) and
`--timeout` (default 3.0s) tune the connection if needed. Add `--disasm` to
preview the actual opcodes before they're sent. Nothing here requires a
firmware rebuild or reflash.

**Without `--upload`** — e.g. compiling on one machine and sending from
another, or wanting to inspect the exact bytes first — split it into two
steps: `python3 tools/gsc.py drive_limit.gs --scriptload --format hex`
prints the same hex string without sending anything, for pasting into
whatever serial terminal you already use for GAACE commands.

## 3. Confirm it loaded

```
GSCRIPTST,1
```

→ `1,0` (loaded=1, lastStatus=0/`VM_OK` — hasn't run yet).

## 4. Turn it on

Loading doesn't start it running — a fresh load shouldn't immediately start
firing before you're ready. Enable and set its rate with GAACE_Core's
existing thread commands, not anything script-specific:

```
STENA,Script1,TRUE
STINT,Script1,1000
```

## 5. Iterate

Edit the `.gs` file, re-run the same `gsc.py --upload ... --slot 1` command
— it overwrites the slot. `GSCRIPTST,1` again to watch `lastStatus` change
as it runs. No firmware rebuild anywhere in this loop.

To stop it: `STENA,Script1,FALSE`. To check the slot/size budget before
writing something ambitious: `GSCRIPTLIMITS`.

## The other path: compile-time embedding

Not every use case wants runtime download. USBrepeater's shipped ADC demo
instead compiles once (`gsc.py` without `--scriptload`), pastes the result
into a C array in firmware source, and loads it by calling `vmInit()`
directly in `setup()`. That needs a full rebuild+reflash to change, but the
script survives a reboot automatically (it's in flash, not RAM).

A `SCRIPTLOAD`/`SCRIPTAPPEND`-loaded script is RAM-only as far as this
library is concerned — but USBrepeater adds its own `SCRIPTSAVE`/
`SCRIPTRESTORE` commands (plus automatic restore-on-boot) on top of
`ScriptRuntime`, using Teensy's `LittleFS_Program` to persist a slot's
exact buffer to on-chip flash. That's a project-level addition, not
something this library does for you generically (it would mean a Teensy
dependency in a library that's otherwise hardware-agnostic) — see
`USBrepeater/TODO.md` for the actual implementation, and this repo's own
`TODO.md` for why `ScriptSlot`'s public fields were left that way
specifically to make this possible without changes here.

## Current gaps

- **Per-project dependency freshness.** A project has to actually pull the
  `GAACE_Script` commit that has `cmd()`/floats/`--upload`/etc. for any of
  this to work — check its `platformio.ini` `lib_deps` (or force a refresh)
  if a feature described here doesn't seem to exist.
- **No GUI for any of this.** `--upload` replaces the manual-paste step
  with one command, but it's still a command line, not a control-panel
  button. See [TODO.md](TODO.md) for why a full host-application
  integration (edit/compile/download inside a larger GUI) is deliberately
  not being pursued yet.
