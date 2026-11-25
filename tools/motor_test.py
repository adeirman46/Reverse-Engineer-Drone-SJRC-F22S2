#!/usr/bin/env python3
"""
motor_test.py - start the motors from the PC, let them idle, then stop them. PROPELLERS MUST BE OFF.

Same buttons as the official app: control + extend messages every 100 ms with sticks centred and a
1 s "lockUnlock" pulse to start the motors. To stop: a second lockUnlock pulse (the remote's stick
combo also toggles start/stop), then an "autoLand" pulse if they are still running. It never sends
take-off and never moves the sticks. Every stop command is sent only while telemetry says the motors
are running, so a stop can never become a start.

Indoors the drone must be in altitude mode (hold the remote's GPS button 3 s): GPS mode refuses to
start the motors without satellites.

Usage (from the repository root):
    python3 tools/motor_test.py <phone-ip> --props-removed [--spin 5] [--no-handshake]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from link import Link  # noqa: E402

T0 = time.time()


def show(link, label):
    t = link.telemetry
    print(f"  [{time.time() - T0:5.1f}s] {label:9s} armed={t.get('armed')} motors={t.get('motors')} "
          f"remote sticks={t.get('sticks')} mode={t.get('fly_mode')} battery={t.get('batt_v')} V",
          flush=True)


def fresh(link):
    return time.time() - link.telemetry_time < 0.8


def armed(link):
    return bool(link.telemetry.get("armed"))


def wait_fresh(link, since, timeout):
    """Wait until telemetry newer than `since` has arrived (Wi-Fi can stall for a few seconds)."""
    end = time.time() + timeout
    while time.time() < end:
        if link.telemetry_time > since and "armed" in link.telemetry:
            return True
        time.sleep(0.05)
    return False


def watch(link, seconds, label):
    end = time.time() + seconds
    while time.time() < end:
        show(link, label)
        time.sleep(0.5)


def pulse_until(link, field, want_armed, timeout=4.0):
    """1 s pulse of `field`; return seconds until armed == want_armed, or None on timeout."""
    sent = time.time()
    last_show = 0.0
    link.control[field] = 1
    try:
        while time.time() - sent < timeout:
            if time.time() - sent >= 1.0:
                link.control[field] = 0
            if armed(link) == want_armed:
                return time.time() - sent
            if time.time() - last_show >= 0.5:
                show(link, field)
                last_show = time.time()
            time.sleep(0.05)
        return None
    finally:
        link.control[field] = 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--props-removed", action="store_true", required=True)
    ap.add_argument("--spin", type=float, default=5, help="seconds to let the motors idle")
    ap.add_argument("--no-handshake", action="store_true")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    capture = os.path.join(root, "captures", time.strftime("%Y%m%d-%H%M%S") + "-motor-test.bin")
    link = Link(args.host, capture_path=capture)
    try:
        if not wait_fresh(link, time.time(), 8):
            sys.exit("No live telemetry: is the bridge connected to the remote? Nothing was sent.")
        show(link, "before")
        t = link.telemetry
        if armed(link):
            sys.exit("Drone already reports the motors running. Nothing was sent.")
        if (t.get("batt_v") or 0) < 10.5:
            sys.exit(f"Battery {t.get('batt_v')} V is low. Charge it first. Nothing was sent.")

        # Read-only check that PC commands get through: ask the gimbal its version.
        link.status6.pop(0x17, None)
        link.send_drone(bytes([0xFF, 0x53, 0x54, 0x17, 0x01]))
        deadline = time.time() + 1.5
        while 0x17 not in link.status6 and time.time() < deadline:
            time.sleep(0.05)
        if 0x17 not in link.status6:
            sys.exit("The gimbal did not answer a read-only question: turn on PC control (app Settings) "
                     "in the app's Settings. No flight command was sent.")
        print("PC command path OK (gimbal answered).", flush=True)

        if not args.no_handshake:
            print("0. Startup handshake, same as the official app:", flush=True)
            link.handshake()
        print("1. Neutral control messages (sticks centred):", flush=True)
        link.start_control()
        watch(link, 2, "neutral")

        print("2. START motors (1 s lockUnlock pulse):", flush=True)
        took = pulse_until(link, "lockUnlock", want_armed=True)
        if took is None:
            show(link, "after")
            print("RESULT: the start command was ignored (motors did not start).", flush=True)
        else:
            print(f"RESULT: MOTORS STARTED {took:.1f} s after the PC command.", flush=True)
            watch(link, args.spin, "spinning")
    except KeyboardInterrupt:
        print("Interrupted.")
    finally:
        if link.running and not wait_fresh(link, time.time(), 5):
            print("WARNING: no fresh telemetry - check the motors and stop them with the remote "
                  "if they are running.", flush=True)
        if link.running and armed(link):
            print("3. STOP motors:", flush=True)
            link.start_control()
            for field in ("lockUnlock", "autoLand"):
                if not armed(link):
                    break
                print(f"   {field} pulse", flush=True)
                took = pulse_until(link, field, want_armed=False)
                if took is not None:
                    print(f"RESULT: MOTORS STOPPED {took:.1f} s after the PC sent {field}.", flush=True)
            if armed(link):
                print("RESULT: motors STILL RUNNING. Stop them with the remote "
                      "(left stick down for 2 s).", flush=True)
        if link.running:
            time.sleep(0.5)
            show(link, "final")
        link.close()
        print(f"Capture saved to {capture}")


if __name__ == "__main__":
    main()
