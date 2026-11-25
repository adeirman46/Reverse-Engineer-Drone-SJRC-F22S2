#!/usr/bin/env python3
"""
feature_test.py - test every app feature on the real drone from the PC, checking the drone's own
telemetry after each command (same messages the Pindad UGV - UAV app sends).

Phase A never starts the motors:
  gimbal up/down (9E gimbal), sport toggle (9C SportMode), GPS/attitude switch (9C IndoorMode,
  remote GPS button held 3.5 s), stick channels (9B sticks), motor beep, photo, record,
  waypoint upload acknowledgements (0x84; the mission is NOT started).
Phase B (only with --props-removed): take off, then land (8B Armed/Takeoff/AutoLand/LandFinish).
Every mode or setting changed is switched back.

Usage (from the repository root):
  python3 tools/feature_test.py <phone-ip>                   # phase A only
  python3 tools/feature_test.py <phone-ip> --props-removed   # A + B
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from link import Link, xor  # noqa: E402

RESULTS = []


def result(name, ok, detail):
    RESULTS.append((name, ok, detail))
    mark = {True: "PASS", False: "FAIL", None: "CHECK"}[ok]
    print(f"  [{mark}] {name}: {detail}", flush=True)


def wait_for(link, cond, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if link.telemetry.get("armed", 0) and not ALLOW_MOTORS:
            raise SystemExit("ABORT: the drone reports motors running during phase A")
        if cond(link.telemetry):
            return True
        time.sleep(0.05)
    return False


def watch_values(link, key, seconds):
    seen = []
    end = time.time() + seconds
    while time.time() < end:
        v = link.telemetry.get(key)
        if not seen or seen[-1] != v:
            seen.append(v)
        time.sleep(0.05)
    return seen


def camera(cmd, *args):
    return bytes([0xFF, 0x35, 0x19, 0x0A, cmd, *args])


def waypoint(index, lat, lon, alt_m, speed, hover):
    d = bytearray(12)
    d[0:4] = int(round(lon * 1e7)).to_bytes(4, "little", signed=True)
    d[4:8] = int(round(lat * 1e7)).to_bytes(4, "little", signed=True)
    v = (index & 0x1F) | (min(4095, int(alt_m * 10)) << 5) | ((speed & 0x7F) << 17) | ((hover & 0xFF) << 24)
    d[8:12] = v.to_bytes(4, "little")
    m = bytearray([0x68, 0x04, 0x0C]) + d
    return bytes(m + bytes([xor(m[1:15])]))


ALLOW_MOTORS = False


def phase_a(link):
    t = link.telemetry
    print("\nPHASE A (motors stay off)", flush=True)
    # gimbal
    g0 = t.get("gimbal")
    link.control["PTZ_V"] = 1
    up = watch_values(link, "gimbal", 1.5)
    link.control["PTZ_V"] = 0
    time.sleep(0.5)
    link.control["PTZ_V"] = 2
    down = watch_values(link, "gimbal", 1.5)
    link.control["PTZ_V"] = 0
    moved = len(set(up + down)) > 1
    result("gimbal up/down", moved if moved else None,
           f"9E gimbal value {g0} -> up {up[-1]} -> down {down[-1]}" + ("" if moved else
           " (no change in telemetry: did the camera tilt?)"))
    time.sleep(0.5)

    # sport mode toggle and back
    s0 = t.get("sport")
    link.pulse("SportMode", 1.0, extend=True)
    ok = wait_for(link, lambda x: x.get("sport") != s0, 3)
    result("sport mode toggle", ok, f"9C SportMode {s0} -> {t.get('sport')}")
    if ok:
        link.pulse("SportMode", 1.0, extend=True)
        back = wait_for(link, lambda x: x.get("sport") == s0, 3)
        result("sport mode back", back, f"SportMode now {t.get('sport')}")

    # GPS / attitude switch (remote GPS button held 3.5 s) and back
    i0 = t.get("indoor")
    link.pulse("gpsMode", 3.5)
    ok = wait_for(link, lambda x: x.get("indoor") != i0, 3)
    result("GPS/attitude switch", ok, f"9C IndoorMode {i0} -> {t.get('indoor')} (1 = attitude)")
    if ok:
        time.sleep(1.0)
        link.pulse("gpsMode", 3.5)
        back = wait_for(link, lambda x: x.get("indoor") == i0, 3)
        result("GPS/attitude switch back", back, f"IndoorMode now {t.get('indoor')}")

    # sticks: which rocker moves which channel the drone reports
    mapping = {}
    for r in ("rocker1", "rocker2", "rocker3", "rocker4"):
        before = dict(t.get("sticks") or {})
        link.control[r] = 170
        time.sleep(1.0)
        after = dict(t.get("sticks") or {})
        link.control[r] = 128
        time.sleep(0.6)
        moved_axes = [k for k in after if before.get(k) is not None and abs(after[k] - before[k]) > 10]
        mapping[r] = moved_axes
    accepted = any(mapping.values())
    result("virtual sticks reach the drone", accepted if accepted else None,
           ", ".join(f"{r}->{'/'.join(v) or 'nothing'}" for r, v in mapping.items()))

    # motor beep (find drone)
    link.pulse("MotorBeepCrtl", 1.0, extend=True)
    result("find-drone beep", None, "sent; did the motors beep?")

    # camera
    link.status6.clear()
    link.send_drone(camera(61, 1))
    time.sleep(1.5)
    link.send_drone(camera(34))
    time.sleep(3.0)
    link.send_drone(camera(35))
    time.sleep(1.0)
    replies = " ".join(f"{c:02X}" for c in link.status6) or "none"
    result("photo + 3 s recording", None, f"sent; status replies: {replies}; check the drone's SD card")

    # waypoint upload acknowledgements (mission NOT started)
    lat, lon = t.get("lat") or 0.0, t.get("lon") or 0.0
    acks = []
    for i in range(2):
        link.raw_messages.pop(0x84, None)
        msg = waypoint(i, lat + 0.0001 * (i + 1), lon, 20, 3, 1)
        end = time.time() + 3
        while time.time() < end and 0x84 not in link.raw_messages:
            link.send_drone(msg)
            time.sleep(0.2)
        acks.append(link.raw_messages.get(0x84, b"").hex(" ") or None)
    ok = all(acks)
    result("waypoint upload acks", ok if ok else None,
           f"0x84 replies {acks} (GPS fix: {bool(lat)}; start command NOT sent)")


def phase_b(link):
    t = link.telemetry
    print("\nPHASE B (motors; propellers must be off)", flush=True)
    if t.get("armed"):
        result("take off", False, "drone already armed; skipped")
        return
    link.pulse("autoTakeoff", 1.0)
    ok = wait_for(link, lambda x: x.get("armed"), 6)
    time.sleep(3)
    result("take off", ok, f"armed={t.get('armed')} takeoff={t.get('takeoff')} fly_mode={t.get('fly_mode')}")
    if not t.get("armed"):
        return
    link.pulse("autoLand", 1.0)
    landed = wait_for(link, lambda x: not x.get("armed"), 20)
    result("land", landed, f"armed={t.get('armed')} auto_land={t.get('auto_land')} land_finish={t.get('land_finish')}")
    if not landed:
        # The remote's ground stop: throttle fully down for 2 s (other sticks centred).
        link.control["rocker2"] = 20
        stopped = wait_for(link, lambda x: not x.get("armed"), 4)
        link.control["rocker2"] = 128
        result("stop with throttle down (rocker2)", stopped, f"armed={t.get('armed')}")
        if not stopped:
            print("  !!! motors still running: stop them with the remote (left stick down for 2 s)", flush=True)


def main():
    global ALLOW_MOTORS
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--props-removed", action="store_true")
    args = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    capture = os.path.join(root, "captures", time.strftime("%Y%m%d-%H%M%S") + "-feature-test.bin")
    link = Link(args.host, capture_path=capture)
    try:
        end = time.time() + 8
        while time.time() < end and "indoor" not in link.telemetry:
            time.sleep(0.1)
        t = link.telemetry
        if "armed" not in t or "indoor" not in t:
            sys.exit("No live telemetry: is the app connected to the remote and PC control on (Settings)? Nothing was sent.")
        if t.get("armed"):
            sys.exit("The drone reports motors running. Nothing was sent.")
        print(f"drone: battery {t.get('bat_pct')}% {t.get('bat_v9c')} V, remote {t.get('remote_pct')}%, "
              f"mode {'attitude' if t.get('indoor') else 'GPS'}, sport {t.get('sport')}, gps {t.get('gps_num')} sats", flush=True)
        link.status6.pop(0x17, None)
        link.send_drone(bytes([0xFF, 0x53, 0x54, 0x17, 0x01]))  # read-only: gimbal version
        end = time.time() + 2
        while 0x17 not in link.status6 and time.time() < end:
            time.sleep(0.05)
        if 0x17 not in link.status6:
            sys.exit("The gimbal did not answer a read-only question: turn on PC control (app Settings) "
                     "in the app's Settings. No flight command was sent.")
        print("PC command path OK (gimbal answered).", flush=True)
        print("startup handshake:", flush=True)
        link.handshake()
        link.start_control()
        time.sleep(1.0)
        phase_a(link)
        if args.props_removed:
            ALLOW_MOTORS = True
            phase_b(link)
        else:
            print("\nPHASE B skipped (add --props-removed to test take-off and landing)", flush=True)
    finally:
        link.control.update({"rocker1": 128, "rocker2": 128, "rocker3": 128, "rocker4": 128, "PTZ_V": 0, "gpsMode": 0})
        time.sleep(0.3)
        link.close()
        print("\nSUMMARY")
        for name, ok, detail in RESULTS:
            print(f"  {({True: 'PASS', False: 'FAIL', None: 'CHECK'})[ok]:5s} {name}: {detail}")
        print(f"capture: {capture}")


if __name__ == "__main__":
    main()
