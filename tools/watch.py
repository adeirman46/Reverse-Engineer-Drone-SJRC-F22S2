#!/usr/bin/env python3
"""
watch.py - listen only: print every change in armed state, motors, sticks and flight flags.

Usage (from the repository root): python3 tools/watch.py <phone-ip> [--seconds 180]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from link import Link  # noqa: E402

KEYS = ("armed", "fly_mode", "takeoff", "auto_land", "land_finish", "go_home", "gps_num",
        "forbid_takeoff", "low_bat", "sticks", "motors")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--seconds", type=float, default=180)
    args = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    capture = os.path.join(root, "captures", time.strftime("%Y%m%d-%H%M%S") + "-watch.bin")
    link = Link(args.host, capture_path=capture)
    start = time.time()
    last = {}
    seen6 = set()
    try:
        while time.time() - start < args.seconds and link.running:
            t = link.telemetry
            changed = {k: t.get(k) for k in KEYS if t.get(k) != last.get(k)}
            if changed:
                print(f"[{time.time() - start:6.1f}s] " + "  ".join(f"{k}={v}" for k, v in changed.items()),
                      flush=True)
                last.update(changed)
            for cmd in set(link.status6) - seen6:
                print(f"[{time.time() - start:6.1f}s] new status FF 53 54 {cmd:02X} {link.status6[cmd].hex(' ')}",
                      flush=True)
                seen6.add(cmd)
            for mid in set(link.raw_messages) - seen6:
                print(f"[{time.time() - start:6.1f}s] new message {mid:02X}: {link.raw_messages[mid].hex(' ')}",
                      flush=True)
                seen6.add(mid)
            time.sleep(0.1)
    finally:
        link.close()
        print(f"done; capture saved to {capture}", flush=True)


if __name__ == "__main__":
    main()
