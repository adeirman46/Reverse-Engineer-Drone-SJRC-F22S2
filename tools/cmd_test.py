#!/usr/bin/env python3
"""
cmd_test.py - prove that commands reach the drone, with nothing moving.

Sends read-only questions the official app also sends (gimbal version, gimbal status, relay
version) and prints any reply that was not already arriving on its own. Sends no control messages.

Usage (from the repository root): python3 tools/cmd_test.py <phone-ip>
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from link import Link  # noqa: E402

QUERIES = [
    ("gimbal version", bytes([0xFF, 0x53, 0x54, 0x17, 0x01])),
    ("gimbal status", bytes([0xFF, 0x53, 0x54, 0x23, 0x01])),
    ("relay version", bytes([0xFF, 0x53, 0x54, 0x25, 0x01])),
]


def main():
    link = Link(sys.argv[1])
    time.sleep(3)
    t = link.telemetry
    print(f"telemetry: armed={t.get('armed')} battery={t.get('batt_v')} V gps={t.get('gps_num')} "
          f"heading={t.get('yaw')} | status messages already arriving: "
          + " ".join(f"{c:02X}" for c in sorted(link.status6)))
    link.new_status6.clear()
    for name, q in QUERIES:
        print(f"asking {name}: {q.hex(' ')}")
        link.send_drone(q)
        time.sleep(1.5)
    time.sleep(2)
    if link.new_status6:
        for _, cmd, p in link.new_status6:
            print(f"NEW reply: FF 53 54 {cmd:02X} {p.hex(' ')}   text: {p.decode('ascii', 'replace')!r}")
    else:
        print("no new replies (is PC control on in the app's Settings?)")
    link.close()


if __name__ == "__main__":
    main()
