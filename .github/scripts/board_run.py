#!/usr/bin/env python3
"""Flash the all-ops firmware, run it, and judge its serial output.

Opens the KitProg3 USB-UART bridge (115200-8-N-1) first, flashes the
images with pyOCD, then restarts the device by pulsing the reset line
(XRES) at probe level only, without any target/debug session: after a
pyocd load the CM0+ core boots and runs, but CM7_0 never starts - the
CM0+ hand-off only clears CPU_WAIT, and any debug connection to the
device blocks CM7_0 again (observed even when a full pyocd hardware
reset was used: its post-reset reconnect kept CM7_0 down). Only a bare
XRES with the debugger staying away gives the power-on style boot the
manual flow ("reset the board") relies on.

The capture may therefore contain a partial pre-reset run; a fresh boot
is recognised by the first model's "Test_exec" line and discards
anything seen before it. After the reset the CM0+ core prints its result
table, releases CM7 core 0, which prints a second table on the same
port. Each core ends with a line

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
import shutil
import subprocess
import sys
import termios
import time

SUMMARY_RE = re.compile(r"Test_result: SUMMARY (\d+)/(\d+) (PASS|FAIL)")
FAIL_RE = re.compile(r"Test_result: .* FAIL")
# First entry of g_embedded_models[] on both cores (embedded_models.cpp):
# each core's run starts with this line, so it marks a (re)boot.
BOOT_RE = re.compile(r"Test_exec: adaptive_avg_pool2d\b")


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


def pyocd_python():
    """Find an interpreter that can import pyocd: the shebang of the pyocd
    launcher (venv/pipx installs), then the common interpreters."""
    candidates = []
    exe = shutil.which("pyocd")
    if exe:
        try:
            with open(exe, "rb") as f:
                first = f.readline().decode("utf-8", "replace").strip()
            print(f"pyocd launcher: {exe} ({first[:80]})", flush=True)
            if first.startswith("#!"):
                parts = first[2:].split()
                candidates.append(parts[1] if parts[0].endswith("/env")
                                  else parts[0])
        except OSError:
            pass
    candidates += [sys.executable, "python3"]
    for cand in candidates:
        try:
            ok = subprocess.run([cand, "-c", "import pyocd"], timeout=30,
                                capture_output=True).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if ok:
            return cand
    return None


XRES_PULSE = """
import sys, time
from pyocd.probe.aggregator import DebugProbeAggregator
uid = sys.argv[1]
probe = next((p for p in DebugProbeAggregator.get_all_connected_probes()
              if uid in (p.unique_id or "")), None)
if probe is None:
    sys.exit("probe %s not found" % uid)
probe.open()
try:
    probe.connect()
except Exception:
    pass  # pin control usually works without a wire protocol selected
probe.assert_reset(True)
time.sleep(0.25)
probe.assert_reset(False)
try:
    probe.disconnect()
except Exception:
    pass
probe.close()
print("XRES pulsed")
"""


def hw_reset(uid, cbuild_run):
    """Restart the device. Preferred: pulse the reset line at probe level
    only - no target/debug session, because any debug connection to this
    device blocks the CM7_0 startup again. Fallback: pyocd reset -m hw as
    an unawaited background process; the caller must kill it the moment
    the reboot shows up on serial, before it can reconnect or fire a
    second reset."""
    interp = pyocd_python()
    if interp:
        cmd = [interp, "-c", XRES_PULSE, uid]
        print("+ <probe-level XRES pulse via pyocd API>", flush=True)
        try:
            r = subprocess.run(cmd, timeout=60, capture_output=True,
                               text=True)
            print(r.stdout, end="", flush=True)
            if r.returncode == 0:
                return None
            print(r.stderr, end="", flush=True)
        except subprocess.TimeoutExpired:
            print("probe-level XRES pulse timed out", flush=True)
    else:
        print("no interpreter with the pyocd package found", flush=True)
    cmd = ["pyocd", "reset", "-m", "hw", "--uid", uid,
           "--cbuild-run", cbuild_run]
    print("+", " ".join(cmd), "(background, killed at first reboot)",
          flush=True)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.STDOUT)


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

    # Open the port before flashing so nothing of the run is lost.
    fd = open_port(port)
    flash_target(args.uid, args.cbuild_run)
    termios.tcflush(fd, termios.TCIFLUSH)  # drop any pre-flash leftovers
    reset_proc = hw_reset(args.uid, args.cbuild_run)

    deadline = time.monotonic() + args.timeout_minutes * 60
    last_data = time.monotonic()
    summaries = []
    fails = []
    pending = b""
    stop_reason = None
    # Restart detection: output seen / summary seen since the last boot
    # marker. A marker arriving mid-run (output but no summary yet) means
    # the reset kicked in - discard the partial pre-reset capture. A marker
    # right after a summary is CM7_0 starting its own run - keep counting.
    seg_output = False
    seg_summary = False
    last_summary = 0.0

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
                if BOOT_RE.search(line):
                    # The chip rebooted: the background reset (if any) has
                    # done its one job. Kill it NOW, before it reconnects
                    # or fires a second reset - any debug connection
                    # blocks the CM7_0 startup.
                    if reset_proc is not None and reset_proc.poll() is None:
                        reset_proc.kill()
                        reset_proc = None
                        print("[monitor] reboot seen - killed background "
                              "pyocd reset", flush=True)
                    # CM7_0 starts its run within a couple of seconds of
                    # the CM0+ summary; a boot marker at any other point
                    # is the hardware reset kicking in - discard whatever
                    # the pre-reset boot produced.
                    cm7_handoff = (seg_summary and
                                   time.monotonic() - last_summary < 3.0)
                    if (seg_output or summaries) and not cm7_handoff:
                        print("[monitor] reboot detected, discarding "
                              "pre-reset capture", flush=True)
                        summaries = []
                        fails = []
                    seg_output = False
                    seg_summary = False
                if not line.startswith("Test_"):
                    continue
                seg_output = True
                m = SUMMARY_RE.search(line)
                if m:
                    summaries.append((int(m.group(1)), int(m.group(2)),
                                      m.group(3)))
                    seg_summary = True
                    last_summary = time.monotonic()
                elif FAIL_RE.search(line):
                    fails.append(line)
    os.close(fd)
    if reset_proc is not None and reset_proc.poll() is None:
        print("[monitor] killing still-running background pyocd reset",
              flush=True)
        reset_proc.kill()

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
