#!/usr/bin/env python3
"""Flash the all-ops firmware, run it, and judge its serial output.

Opens the KitProg3 USB-UART bridge (115200-8-N-1) first, then flashes the
images with pyOCD; the target boots and runs as soon as programming ends,
so no output is lost and no separate reset is needed. (Resetting via the
debug port after flashing proved unreliable on this device: the DFP
software reset does not restart a running target, and a hardware reset
leaves pyOCD hanging while the chip goes through its boot ROM.)

The CM0+ core prints its result table, releases CM7 core 0, which prints
a second table on the same port. Each core ends with a line

    Test_result: SUMMARY <passed>/<total> PASS

so the run is complete once two SUMMARY lines were captured.

Exit code 0 only if both summaries report <passed> == <total> and no
per-op "Test_result: <op> FAIL" line was seen; 1 otherwise (including
overall or idle timeout).

Uses only the Python standard library (termios) so the self-hosted Linux
runner does not need pyserial.
"""

import argparse
import glob
import os
import re
import select
import subprocess
import sys
import termios
import time

SUMMARY_RE = re.compile(r"Test_result: SUMMARY (\d+)/(\d+) (PASS|FAIL)")
FAIL_RE = re.compile(r"Test_result: .* FAIL")


def find_port(uid):
    """KitProg3 exposes the probe UID in its USB serial string."""
    matches = sorted(glob.glob(f"/dev/serial/by-id/*{uid}*"))
    return matches[0] if matches else None


def open_port(port):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[0] = termios.IGNBRK                                 # iflag: raw
    attrs[1] = 0                                              # oflag: raw
    attrs[2] = termios.CREAD | termios.CLOCAL | termios.CS8   # cflag: 8-N-1
    attrs[3] = 0                                              # lflag: raw
    attrs[4] = termios.B115200                                # ispeed
    attrs[5] = termios.B115200                                # ospeed
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIFLUSH)
    return fd


def flash_target(uid, cbuild_run):
    cmd = ["pyocd", "load", "--uid", uid, "--cbuild-run", cbuild_run]
    print("+", " ".join(cmd), flush=True)
    try:
        if subprocess.run(cmd, timeout=600).returncode != 0:
            sys.exit("error: pyocd load failed")
    except subprocess.TimeoutExpired:
        sys.exit("error: pyocd load timed out after 600 s")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--uid", required=True, help="debug probe unique ID")
    ap.add_argument("--cbuild-run", required=True)
    ap.add_argument("--port",
                    help="serial device (default: match probe UID under "
                         "/dev/serial/by-id)")
    ap.add_argument("--log", default="serial.log")
    ap.add_argument("--timeout-minutes", type=float, default=40)
    ap.add_argument("--idle-timeout", type=float, default=300,
                    help="seconds without serial output before giving up")
    args = ap.parse_args()

    port = args.port or find_port(args.uid)
    if not port:
        sys.exit(f"error: no serial port matching {args.uid} under "
                 "/dev/serial/by-id; pass --port explicitly")
    print(f"Serial port: {port}", flush=True)

    # Open the port before flashing: the target starts running the moment
    # programming finishes, so this captures the run from the first byte.
    fd = open_port(port)
    flash_target(args.uid, args.cbuild_run)
    termios.tcflush(fd, termios.TCIFLUSH)  # drop any pre-flash leftovers

    deadline = time.monotonic() + args.timeout_minutes * 60
    last_data = time.monotonic()
    summaries = []
    fails = []
    pending = b""
    stop_reason = None

    with open(args.log, "wb") as log:
        while len(summaries) < 2:
            now = time.monotonic()
            if now > deadline:
                stop_reason = f"overall timeout ({args.timeout_minutes:g} min)"
                break
            if now - last_data > args.idle_timeout:
                stop_reason = (f"no serial output for "
                               f"{args.idle_timeout:.0f} s")
                break
            ready, _, _ = select.select([fd], [], [], 1.0)
            if not ready:
                continue
            data = os.read(fd, 4096)
            if not data:
                continue
            last_data = time.monotonic()
            log.write(data)
            log.flush()
            pending += data
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").rstrip("\r")
                print(line, flush=True)
                m = SUMMARY_RE.search(line)
                if m:
                    summaries.append((int(m.group(1)), int(m.group(2)),
                                      m.group(3)))
                elif FAIL_RE.search(line):
                    fails.append(line)
    os.close(fd)

    print()
    if stop_reason:
        print(f"Stopped: {stop_reason}")
    for passed, total, verdict in summaries:
        print(f"Core summary: {passed}/{total} {verdict}")
    if len(summaries) < 2:
        print(f"Expected 2 'Test_result: SUMMARY' lines "
              f"(CM0+ and CM7_0), got {len(summaries)}")
    if fails:
        print(f"{len(fails)} per-op failure(s):")
        for line in fails:
            print(f"  {line}")

    ok = (len(summaries) == 2 and not fails
          and all(v == "PASS" and p == t and t > 0
                  for p, t, v in summaries))
    print("Board test PASSED" if ok else "Board test FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
