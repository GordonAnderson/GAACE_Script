#!/usr/bin/env python3
"""Send command lines to a GAACE control port and print the replies.

For the hardware checklist (TESTING_CHECKLIST.md), when the MIPS app is not
connected. Requires pyserial (pip install pyserial).

    python3 send.py PORT "GSCRIPTST,1" "GSYSCALLS" ...

ACK (0x06) and NAK (0x15) bytes are shown as [ACK] / [NAK].
"""
import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("error: this needs pyserial -- pip install pyserial")
if not hasattr(serial, "Serial"):
    sys.exit("error: the 'serial' module found here is not pyserial -- "
             "pip uninstall serial; pip install pyserial")


def transact(port, command, first_wait=2.0, idle=0.4):
    """Sends one line; returns the reply bytes. Waits up to `first_wait`
    seconds for the first byte, then until the line has been quiet for `idle`."""
    port.reset_input_buffer()
    port.write((command + "\n").encode("ascii"))
    data = b""
    start = last = time.time()
    while True:
        chunk = port.read(256)
        now = time.time()
        if chunk:
            data += chunk
            last = now
        elif data and now - last >= idle:
            return data
        elif not data and now - start >= first_wait:
            return data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("port", help="serial port of the control port, e.g. /dev/cu.usbmodemXXXX")
    ap.add_argument("commands", nargs="+", help="command lines to send, one argument each")
    ap.add_argument("--baud", type=int, default=115200)
    args = ap.parse_args(argv)

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.1)
    except serial.SerialException as e:
        sys.exit(f"error: could not open {args.port}: {e}")
    with port:
        time.sleep(0.2)  # let the connection settle
        for command in args.commands:
            reply = transact(port, command)
            text = reply.decode("ascii", "replace").replace("\x06", "[ACK]").replace("\x15", "[NAK]")
            print(f"> {command}")
            print(text.rstrip() if text.strip() else "(no reply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
