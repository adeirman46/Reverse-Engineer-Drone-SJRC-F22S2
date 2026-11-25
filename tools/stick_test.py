#!/usr/bin/env python3
"""
stick_test.py - do the app's stick values reach the flight controller, and which rocker is which axis?

Motors stay OFF. One rocker at a time is moved from 128 to 170 (about a third of full travel) for
1.2 s while control + extend messages go out every 100 ms like the official app; the drone's own
report of the sticks it receives (message 9B: roll, pitch, thr, yaw) is recorded. Then the same
with ctrlPanel=1 (the official app sets it when the app steers the drone).
A single half-deflected stick cannot form any of the remote's stick combinations (motor start,
compass calibration, reset). The test aborts if the drone ever reports armed.

Usage (from the repository root): python3 tools/stick_test.py <phone-ip>
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from link import Link  # noqa: E402


def sticks(link):
    s = link.telemetry.get("sticks") or {}
    return (s.get("roll"), s.get("pitch"), s.get("thr"), s.get("yaw"))


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    capture = os.path.join(root, "captures", time.strftime("%Y%m%d-%H%M%S") + "-stick-test.bin")
    link = Link(sys.argv[1], capture_path=capture)
    try:
        end = time.time() + 8
        while time.time() < end and not (link.telemetry.get("armed") is not None and link.telemetry.get("sticks")):
            time.sleep(0.1)
        t = link.telemetry
        if t.get("armed") is None or not t.get("sticks"):
            sys.exit("No live telemetry from the drone. Nothing was sent.")
        if t.get("armed"):
            sys.exit("The drone reports motors running. Nothing was sent.")
        print(f"idle sticks (roll, pitch, thr, yaw) = {sticks(link)}", flush=True)
        link.start_control()
        time.sleep(1.0)
        print(f"with app neutral       = {sticks(link)}", flush=True)
        results = {}
        for panel in (0, 1):
            for r in ("rocker1", "rocker2", "rocker3", "rocker4"):
                link.control["ctrlPanel"] = panel
                link.control[r] = 170
                seen = set()
                t_end = time.time() + 1.2
                while time.time() < t_end:
                    if link.telemetry.get("armed"):
                        raise SystemExit("ABORT: drone reports armed")
                    seen.add(sticks(link))
                    time.sleep(0.05)
                link.control[r] = 128
                results[(panel, r)] = seen
                print(f"ctrlPanel={panel} {r}=170 -> sticks seen {sorted(seen, key=str)}", flush=True)
                time.sleep(0.8)
            link.control["ctrlPanel"] = 0
        time.sleep(0.5)
        print(f"back to neutral        = {sticks(link)}  armed={link.telemetry.get('armed')}", flush=True)
    finally:
        link.control.update({"rocker1": 128, "rocker2": 128, "rocker3": 128, "rocker4": 128, "ctrlPanel": 0})
        time.sleep(0.3)
        link.close()
        print(f"capture: {capture}")


if __name__ == "__main__":
    main()
