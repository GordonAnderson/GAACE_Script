# Writing and downloading a script

This is the practical, step-by-step version of what's designed in
[README.md](README.md) and [tools/README.md](tools/README.md) — how you
actually get a script running on a controller, without reflashing firmware.

Assumes the project already has `GAACEScript::ScriptRuntime` wired up (see
README.md's "Standard runtime" section) and its command port reachable over
a serial terminal.

## 1. Write the script

A plain `.gs` file. Either declare your own syscalls, or call straight into
whatever the project's command processor already exposes with `cmd()` — no
firmware change needed either way. Example (`drive_limit.gs`):

```
syscall read_power() : float = 0;
syscall max_drive() : float = 1;
syscall set_max_drive(v) = 2;

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

Compiles, opens that serial port itself, sends `SCRIPTLOAD,1,<hex>` (the
code and string-constant pool combined with the `[codeLen][code][pool]`
length prefix `SCRIPTLOAD` expects), and prints the result:

```
OK: script loaded into slot 1 (12 bytes)
```

or, on failure:

```
NAK: device rejected the script (bad slot, malformed hex, or over
the configured size limit -- see GSCRIPTLIMITS)
```

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
script survives a reboot automatically (it's in flash, not RAM) — whereas
today, a `SCRIPTLOAD`-loaded script does **not** survive a reboot (see
`USBrepeater/TODO.md`'s persistence item).

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
