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

## 2. Compile it to the wire format

```
python3 tools/gsc.py drive_limit.gs --scriptload --format hex
```

This prints one hex string: the code and string-constant pool combined
with the `[codeLen][code][pool]` length prefix `SCRIPTLOAD` expects. Sanity
check it first with `--disasm` if you want to see the actual opcodes before
sending anything to a real device.

## 3. Send it to the controller

Open a serial connection to the control port (e.g. `SerialUSB1` at 115200
baud) with whatever terminal you already use for GAACE commands, pick a
free slot, and send:

```
SCRIPTLOAD,1,<the hex string>
```

You get back `ACK` or `NAK`, same as any other command. Nothing here
requires a firmware rebuild or reflash.

## 4. Confirm it loaded

```
GSCRIPTST,1
```

→ `1,0` (loaded=1, lastStatus=0/`VM_OK` — hasn't run yet).

## 5. Turn it on

Loading doesn't start it running — a fresh load shouldn't immediately start
firing before you're ready. Enable and set its rate with GAACE_Core's
existing thread commands, not anything script-specific:

```
STENA,Script1,TRUE
STINT,Script1,1000
```

## 6. Iterate

Edit the `.gs` file, recompile, send a new `SCRIPTLOAD,1,<hex>` — it
overwrites the slot. `GSCRIPTST,1` again to watch `lastStatus` change as it
runs. No firmware rebuild anywhere in this loop.

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

- **No upload tool yet.** Step 3 above means manually pasting a command
  into a serial terminal. Teaching `gsc.py` (or a small sibling script) to
  open the port and send the line itself is the natural next step — see
  the repo's recent design discussion for why that's preferred over
  integrating compile+download into a larger host application right now.
- **Per-project dependency freshness.** A project has to actually pull the
  `GAACE_Script` commit that has `cmd()`/floats/etc. for any of this to
  work — check its `platformio.ini` `lib_deps` (or force a refresh) if a
  feature described here doesn't seem to exist.
