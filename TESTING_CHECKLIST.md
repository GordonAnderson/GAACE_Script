# Hardware test checklist

What has **not** yet run on real hardware: the firmware's `GSYSCALLS` / hash /
float-argument code, and the whole Device Scripts feature in the MIPS app
(`dev` branch). Everything below was passing against fakes; this is the real
test. Tick the boxes as you go, and write down anything that surprises you.

## What you need

- **A USBrepeater (Teensy 4.1) by itself is enough.** The scripting stack
  (compiler, upload, hash check, `cmd()`, floats, syscalls, slots) does not need
  a device plugged into it. Only three things do, and they are marked
  *(needs a downstream device)* and optional: the `link_ready` and `scale_send`
  syscalls, and slot 0's built-in ADC demo forwarding values.
- A Mac with the repos checked out (`GAACE_Script`, `GAACE_Core`, `USBrepeater`,
  `MIPS_QT6` on `dev`), PlatformIO, and Qt 6.
- **Python with pyserial** for the command-line steps. The system `python3` here
  has a package called `serial` that is *not* pyserial, and fails. Use PlatformIO's:
  `PY=~/.platformio/penv/bin/python` (pyserial 3.5), or make a venv and
  `pip install pyserial`.

### The control port

The USBrepeater shows up as **three** USB serial ports. Only the second one
(`SerialUSB1`) is the control port; the other two are raw passthroughs and do not
answer commands. Find it by trying each one:

```
ls /dev/cu.usbmodem*
$PY examples/hwtest/send.py /dev/cu.usbmodemXXXX GVER      # the control port replies [ACK]...Version
```

Below, `PORT` is that port. **Only one program can have it open at a time**: close
the MIPS app, a terminal, or `pio device monitor` before using the command-line
tools, and vice versa.

### Cautions

- **Use slots 1-3.** Slot 0 holds the compiled-in ADC demo.
- T2 changes a live setting (`ADCFILTER`). Read it first and put it back; it is
  not saved to flash unless you send `SAVE`.
- Do not use `dio_write` / `dio_set_dir` in a test while anything is wired to the
  DIO pin. The test scripts only *read* it.

---

> **Status (2026-11-09):** ticked boxes below passed on real hardware. See the results table at the end.

## A. Firmware

- [x] **A1. Flash current firmware.** From `USBrepeater`: `pio run -t upload`. It
  pulls the library's branch head (`GAACE_Script` `main`), so it includes the syscall
  table, the hash check and typed `cmd()` arguments. (If the build seems stale,
  check the "Dependency Graph" in the build output shows the latest commit.)
- [x] **A2. It boots and answers.** `$PY examples/hwtest/send.py PORT GVER GNAME`
  -> `[ACK]` and a version / name.
- [x] **A3. Script limits.** `send.py PORT GSCRIPTLIMITS` -> `[ACK]4,2048,32,16,32`
  (slots, bytes per slot, stack, variables, max syscalls; these are the expected
  values from the defaults, and the 2048 comes from `GAACE_SCRIPT_MAX_CODE_LEN` in
  `platformio.ini`).
- [x] **A4. Syscall table.** `send.py PORT GSYSCALLS` -> `[ACK]6,6501A214` then
  `0,link_ready,0,int`, `1,read_adc,0,int`, `2,scale_send,1,int`,
  `3,dio_set_dir,1,int`, `4,dio_write,1,int`, `5,dio_read,0,int`.
  **If the hash differs** the table or the hash code disagrees with `gsc.py`
  (`python3 tools/gsc.py --list-syscalls PORT` recomputes it): stop and tell me.
- [x] **A5. Command list.** `send.py PORT GCMDS` -> `[ACK]` and a long list of
  `NAME,help` lines that includes `SCRIPTLOAD`, `SCRIPTAPPEND`, `SCRIPTCOMMIT`,
  `GSCRIPTST`, `GSYSCALLS`, `ADCFILTER`-family names (`GADCFILTER`, `SADCFILTER`).
- [x] **A6. Slot status.** `send.py PORT GSCRIPTST,1` -> `[ACK]FALSE,0` (empty).

## B. The command-line compiler, no Qt app

Close the app. From `GAACE_Script`:

- [x] **B1. Read the table and commands.**
  `$PY tools/gsc.py --list-syscalls PORT --save-syscalls /tmp/ub.json` prints
  6 syscalls and hash `6501A214`;
  `$PY tools/gsc.py --list-commands PORT --save-commands /tmp/ubcmds.json` prints the
  command list. Both files are written.
- [x] **B2. Compile offline against the saved table.**
  `$PY tools/gsc.py examples/hwtest/t3_syscalls.gs --syscalls /tmp/ub.json --disasm`
  shows `CALL` instructions and no errors.
- [x] **B3. T1: integer reply.**
  `$PY tools/gsc.py examples/hwtest/t1_int_reply.gs --upload PORT --slot 1` -> `OK:
  script loaded into slot 1 (... single SCRIPTLOAD)`. Then run it and read the result:
  ```
  $PY examples/hwtest/send.py PORT GSCRIPTST,1 STINT,Script1,500 STENA,Script1,TRUE
  $PY examples/hwtest/send.py PORT GSCRIPTVAR,1,0 GBAUD
  ```
  `GSCRIPTST,1` shows `TRUE,...` (loaded). `GSCRIPTVAR,1,0` shows the baud rate as the
  first number, matching `GBAUD`. Stop it: `send.py PORT STENA,Script1,FALSE`.
- [x] **B4. T2: a float argument and a float reply.** First note the current value:
  `send.py PORT GADCFILTER` (original = ______). Upload `t2_float.gs` to slot 1 the
  same way, start it (`STENA,Script1,TRUE`), then
  `send.py PORT GADCFILTER GSCRIPTVAR,1,0` -> `GADCFILTER` now reads **12.5** (this is
  the point of the test: before the fix it would have been a huge integer), and
  variable 0 shows 12.5 as its float reading. **Stop the slot and restore:**
  `send.py PORT STENA,Script1,FALSE SADCFILTER,<original>`.
- [x] **B5. T3: syscalls.** `$PY tools/gsc.py examples/hwtest/t3_syscalls.gs --upload PORT
  --slot 1` (it asks the device for the table; the load is stamped with the hash).
  Start it; `GSCRIPTVAR,1,0` is a plausible raw ADC reading (it changes if the AIN input
  changes), `GSCRIPTVAR,1,1` is 0 or 1. Stop it.
- [x] **B6. T4: chunked upload.** Upload `t4_chunked.gs` to slot 1 -> `OK: ... 3
  SCRIPTAPPEND chunks + SCRIPTCOMMIT`. `GSCRIPTST,1` -> loaded. (This sends ~400
  character lines over real serial, which the fakes could not test.)
- [x] **B7. A stale hash is refused.** Using the tiny script `x = 1;`:
  `send.py PORT SCRIPTLOAD,1,08000101000000050000,DEADBEEF` -> `[NAK]?`, and
  `GSCRIPTST,1` afterwards shows `FALSE,...`: the slot is **not** left loaded with a rejected script.
  Then without the hash: `send.py PORT SCRIPTLOAD,1,08000101000000050000` -> `[ACK]`.
- [x] **B8. A bad command name is only a warning.** Compile
  `printf 'cmd("SDRW", 1);' | $PY tools/gsc.py - --commands /tmp/ubcmds.json --json`
  -> `ok: true` with one warning and "did you mean ...?" if a close name exists.
- [x] **B9. Optional: survives a reboot.** With a script in slot 1: `send.py PORT
  SCRIPTSAVE,1`, power-cycle the board, then `GSCRIPTST,1` -> loaded (auto-restored).
  Clean up: `send.py PORT SCRIPTERASE,1`.

## C. The MIPS app (`dev` branch)

Build and run: open `MIPS.pro` in Qt Creator (Qt 6.7.1 or newer), or in a scratch
folder `qmake /path/to/MIPS_QT6/MIPS.pro && make`. Optional first:
`tools/python/fetch_python.sh` to build in the bundled Python (without it the app
uses a `python3` from your PATH).

- [x] **C1. It runs and sees the device.** Connect as you normally do
  (Connect or Search). The USBrepeater should appear as a system. The app finds systems by
  asking `GVER` and looking for "Version" in the reply: **if the USBrepeater
  does not appear, tell me what `GVER` returns.**
- [ ] **C2. About.** The About menu -> About shows "Device Scripts compiler: gsc 1.2.1, Python
  x.y.z" (and the right Python if you ran the fetch script: 3.14.10).
- [x] **C3. Open Tools -> Device Scripts.** The system, "4 script slots, 2048 bytes
  per slot", the 6 syscalls and hash `6501A214` are shown, and the log's first line
  names the Python and `gsc.py` being used.
- [x] **C4. Check and Send, a command script.** Open `examples/hwtest/t1_int_reply.gs`
  (Open...), Check -> "Compiled: 15 bytes". Select slot 1, Send -> "Sent 15 bytes to
  slot 1". Start slot, then confirm with `GSCRIPTVAR,1,0` in the app's Terminal tab. Stop slot.
- [x] **C5. Float.** `t2_float.gs` the same way; confirm `GADCFILTER` reads 12.5; restore it.
- [x] **C6. Syscalls and the hash.** `t3_syscalls.gs`: the log says "(checked against
  table hash 6501A214)". A script with no syscalls (T1) does not mention a hash.
- [x] **C7. Chunked upload.** `t4_chunked.gs`: Send works, the progress bar moves, and
  the slot shows loaded (Slot status).
- [x] **C8. Errors are located.** Type `y = ;` on line 3 of a script: Check puts the cursor
  and a red mark on line 3, lists "Error: line 3, column 5: ...", and Send does nothing.
  Click the entry to jump to it; editing clears it.
- [x] **C9. Warnings.** `cmd("SDRW", 1);` -> "Warning: ... the device has no command
  'SDRW'" with a suggestion; Send still works.
- [x] **C10. Replace prompt.** Send to a slot that already has a script: it asks first;
  "No" leaves it alone.
- [x] **C11. Too big.** A script over 2048 bytes (`for i in $(seq 400); do echo 'x = cmd("GBAUD");'; done > big.gs`)
  is refused with "a slot holds 2048" before anything is sent.
- [x] **C12. Save / Open / unsaved changes.** Save As a `.gs`, reopen it; close the dialog
  with unsaved edits -> it asks.
- [x] **C13. Unplug mid-use.** Unplug the USB while the dialog is open, press Refresh /
  Send: a clear "not connected" message, no crash or hang. Plug back in, search again.
- [x] **C14. Other app features still work** while the dialog is open (control panels,
  terminal): the new `Comms::Transact()` shares the serial port with them.
  (2026-11-09: an RFgen control panel ran alongside Device Scripts and a running
  script; both worked.)
- [ ] **C15. Hex path.** Tick "Send a precompiled .hex file instead", pick a `--scriptload
  --format hex` file, Send.

## D. Packaging (macOS only here)

- [ ] **D1.** `tools/python/fetch_python.sh`, rebuild: `MIPS.app/Contents/Resources/python`
  and `gsc.py` exist, and C2 shows Python 3.14.10.
- [ ] **D2.** Sign the way the release workflow does: `tools/python/sign_macos_python.sh
  MIPS.app` then `codesign --deep --force --sign - MIPS.app` and `codesign --verify --deep
  --strict MIPS.app` -> no errors; Device Scripts still compiles from the signed bundle.
- [ ] **D3.** Move the app to a different folder (or Mac) and run it from there: it still
  finds its Python.

## E. Needs a downstream device (optional)

- [x] **E1. `device()` / `ready()` / `dcmd()`.** With an instrument named `RFgen` on the
  repeater: `rf = device("RFgen"); if (ready(rf)) { ok = dcmd(rf, "SDRV", 10); }`
  sets RFgen's drive to 10 and `ok` reads 1.
- [x] **E2. Full system test: ADC -> RFgen drive.** `examples/hwtest/rfgen_drive.gs`
  reads the ADC (`read_adc()`), converts to volts with the repeater's own calibration
  (`cmdf("GADCM")`, `cmdf("GADCB")`), maps 0..10 V to 0..100% (clamped), and sends it
  with `dcmd(rf, "SDRV", drv)`. Check: -5 V in -> 0%; the drive follows the input; the
  RFgen control panel keeps working while the script runs.
- [x] **E3. `link_ready()` / `scale_send()`.** Both use the repeater's ADC target and
  command prefix, not a name in the script. Stop anything else sending the same command
  (other slots, `SADCEN,FALSE`), note `GADCDEV` / `GADCCMD`, then `SADCDEV,RFgen` and
  `SADCCMD,SDRV`. Load `examples/hwtest/t5_link_send.gs` into slot 1 and start it:
  variable 0 (`linked`) = 1, variable 2 (`sent`) = 1, and RFgen's drive equals the input
  in volts (`scale_send` applies only ADCM/ADCB, so 5 V sends `SDRV,5.000`; there is no
  clamp). A negative input sends a negative drive, which RFgen rejects (drive shows 0).
  Optional: `SADCDEV,NoSuchName` -> `linked` = 0; slot 0's built-in demo
  (`STENA,Script0,TRUE`) does the same on its own. Restore `SADCDEV` / `SADCCMD` afterwards.

---

## If something fails

Send me:

1. The exact step, and what you saw versus what is expected above.
2. The Device Scripts **log** text (select all in the log pane) or the terminal output.
3. `send.py PORT GVER GSYSCALLS GSCRIPTLIMITS` output, and the About line (versions).

Quick triage: **A-steps fail** -> firmware. **A passes, B fails** -> firmware/protocol
or the compiler. **A and B pass, C fails** -> the Qt app.

## Results

| Date | Tester | Firmware commit | App commit (`dev`) | Sections passed | Notes |
| --- | --- | --- | --- | --- | --- |
| 2026-11-08 | Gordon Anderson (A-steps run from the command line with Claude) | USBrepeater `95bb2f5` + GAACE_Script `467964d`, GAACEgateway 1.1 | MIPS_QT6 `dev` `aa24d15` | A1-A6, C1, C3-C13 | Two bugs found and fixed: GSCRIPTST replies TRUE/FALSE (not 1/0), and the replace prompt was skipped when the status could not be read. Not yet run: B1-B9 (command-line compiler against the device), C2, C14, C15, D, E. First flash attempt failed with "error writing to Teensy" (documented), second worked. |
| 2026-11-09 | Gordon Anderson (with Claude) | USBrepeater `b3f7c60` + GAACE_Script `5892914` | MIPS_QT6 `dev` `aa24d15` | B1-B9, C14, E1, E2, E3 | Device scripts drove a downstream instrument (`RFgen`) for the first time, with its control panel open. First version of `rfgen_drive.gs` divided raw ADC counts by 24 (-5 V gave 47.58%); fixed by converting counts to volts with GADCM/GADCB first. E3 (`t5_link_send.gs`): `link_ready`/`scale_send` worked against RFgen; a negative input sends a negative SDRV, which RFgen rejects (drive shows 0). B1-B9 run from the command line by Claude (control port `/dev/cu.usbmodem211425603`): hash `6501A214`, 91 commands; T1 baud 115200; T2 12.5 (ADCFILTER restored to 0.00); T3 ADC 2441 counts, DIO 0 (input not varied); T4 3 chunks + commit; DEADBEEF hash NAKed and slot left unloaded; SDRW warning only (no suggestion: no close name on the repeater). Firmware help text for GSCRIPTST still says `loaded(0|1)` though it replies TRUE/FALSE. B9: `x = 1;` saved to slot 1, power-cycled, slot 1 reloaded by itself (`TRUE,1`), then SCRIPTERASE,1. Still not run: C2, C15, D. |
