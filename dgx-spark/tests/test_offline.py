"""Offline tests of the ground station logic (no drone, no remote, about 15 s).

    cd dgx-spark && python3 tests/test_offline.py          (or: python3 -m pytest tests)

Known answers for the limits message come from the official app's native encoder (convertHy1BSetInfo).
"""
import os
import random
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pindad_gcs import protocol as P  # noqa: E402
from pindad_gcs.battery import BatteryEstimator  # noqa: E402
from pindad_gcs.link import Link  # noqa: E402


def test_flight_limits_message():
    assert P.flight_limits(10, 20, 20).hex() == "681b0a00000a001400140000001b"
    assert P.flight_limits(120, 1500, 30).hex() == "681b0a00007800dc051e000000ae"
    assert P.flight_limits(500, 2000, 120).hex() == "681b0a0000f401d007780000004b"


def test_settings_replies():
    ab = b"HF-SH-F22N7R406.024.0509" + struct.pack("<HHHH", 500, 2000, 40, 0)
    assert struct.unpack_from("<HHH", ab, 24) == (500, 2000, 40)
    nine_a = bytearray(28)
    v = 120 | 1500 << 10 | 30 << 22
    nine_a[24:28] = v.to_bytes(4, "little")
    assert (P.bits(nine_a, 192, 10), P.bits(nine_a, 202, 12), P.bits(nine_a, 214, 10)) == (120, 1500, 30)


def test_battery_steady_in_flight():
    """Simulated flight: true charge drains 3 %/min; the drone's own % = true - voltage sag (16 %, 24 % in
    fast flight) with noise. The shown % stays within 3 % of the truth and never rises in flight."""
    rnd = random.Random(3)
    e, t, soc, worst, last = BatteryEstimator(), 0, 76.0, 0.0, None

    def step(armed, sag):
        nonlocal t
        e.update(max(0, min(100, int(soc - sag + rnd.uniform(-1.2, 1.2)))), armed, 0, t)
        t += 100

    for _ in range(300):
        step(0, 0)
    for _ in range(20):
        step(1, 2)
    for i in range(2700):
        soc -= 3 / 600
        step(2, 16 + (8 if 900 <= i < 1200 else 0))
        shown = e.percent()
        if last is not None:
            assert shown <= last, "rose in flight"
        last = shown
        worst = max(worst, abs(shown - soc))
    assert worst <= 3.5, worst
    for i in range(200):  # landed: the voltage recovers, the number must not jump up
        step(0, max(0, 6 - i * 0.06))
        assert e.percent() <= last + 1
    assert abs(e.percent() - soc) <= 2.5


class _Backend:
    sends_heartbeats = False
    name = "test"

    def __init__(self):
        self.sent = []

    def start(self, link):
        pass

    def send_frame(self, ftype, payload, seq):
        self.sent.append((time.monotonic(), ftype, payload))
        return True


class _Stub:
    def feed(self, *a):
        pass

    def start_recording(self):
        pass

    def stop_recording(self):
        pass


def _pack(fields, n):
    v = 0
    for lo, width, val in fields:
        v |= (val & ((1 << width) - 1)) << lo
    return v.to_bytes(n, "little")


def _msg(mid, p):
    x = mid ^ len(p)
    for b in p:
        x ^= b
    return bytes([0x58, mid, len(p)]) + p + bytes([x])


def _frame(ftype, payload):
    return b"\xff\x56\x53\x00\x00" + struct.pack("<HH", len(payload), ftype) + b"\x00\x01" + payload


def test_flight_logic():
    st = dict(armed=0, lowBat=1, sensors=0xEF, gpsNum=20, gpsFine=1, forbid=0, magInt=212, magCal=1,
              batPct=39, batV=11.18, indoor=0)
    be = _Backend()
    link = Link(be, lambda m: None, _Stub(), _Stub())
    link.tel.relay_reply, link.tel.got_version = 2, True  # skip the startup handshake
    stop = threading.Event()

    def feed():
        while not stop.is_set():
            p8 = _pack([(48, 8, st["sensors"]), (70, 2, st["lowBat"]), (75, 2, st["armed"]), (90, 5, st["gpsNum"]),
                        (95, 1, st["gpsFine"]), (109, 1, st["forbid"])], 14)
            p9 = _pack([(0, 10, st["magInt"]), (10, 12, round(st["batV"] * 100)), (32, 1, st["indoor"]),
                        (33, 7, st["batPct"]), (73, 1, st["magCal"])], 12)
            link.on_bytes(_frame(5, _msg(0x8B, p8) + _msg(0x9C, p9)))
            time.sleep(0.1)

    threading.Thread(target=feed, daemon=True).start()
    try:
        link.start()
        link.on_connected(True)
        time.sleep(1.0)
        f = link.flight
        # 1. the drone does not start: the message gives its reasons, not GPS
        assert f.command("takeoff") is None
        time.sleep(7.6)
        a = f.alert["text"]
        assert "Compass disturbed here (interference 212" in a and "low battery (11.18 V)" in a
        assert "attitude mode" in a and "needs a GPS fix" not in a  # GPS is fine (20 satellites)
        # 2. motors start: take-off follows 1.5 s later
        st.update(magInt=60, magCal=0, lowBat=0)
        time.sleep(0.3)
        assert link.tel.blockers() == []
        assert f.command("takeoff") is None
        time.sleep(0.5)
        st["armed"] = 1
        t0 = time.monotonic()
        time.sleep(2.2)
        assert any(s[0] > t0 and s[2][:3] == b"\x68\x01\x0d" and P.bits(s[2][3:16], 51, 1) for s in be.sent)
        st["armed"] = 2
        # 3. flight limits: sent until the drone answers; its answer is reported
        assert f.set_limits(5000, 3000, 60) is None
        time.sleep(0.4)
        assert [s for s in be.sent if s[2] == P.flight_limits(5000, 3000, 60)]
        link.on_bytes(_frame(5, _msg(0xAB, b"HF-SH-F22N7R406.024.0509" + struct.pack("<HHHH", 500, 3000, 60, 0))))
        time.sleep(0.3)
        s = f.limits_state()
        assert s["state"] == "ok" and s["dAlt"] == 500 and "keeps its own maximum" in s["msg"]
        link.tel.d["alt"] = 50.0
        assert "set the max altitude above" in f.set_limits(30, 3000, 20)
        link.tel.d["alt"] = 0.0
        # 4. no-fly block left by the official app: one 1 s ForbidTakeOff pulse, then the result
        st.update(armed=0, forbid=1)
        time.sleep(0.3)
        t0 = time.monotonic()
        assert f.command("clearNoFly") is None
        time.sleep(1.3)
        pulse = [s for s in be.sent if s[0] > t0 and s[2][:3] == b"\x68\x0b\x08" and P.bits(s[2][3:11], 21, 1)]
        assert 8 <= len(pulse) <= 11
        st["forbid"] = 0
        time.sleep(3.2)
        assert f.alert["text"] == "No-fly block cleared."
    finally:
        stop.set()
        link.connected = False


def test_mission_distance():
    """Waypoints: at most 16; measured from home; inside the drone's confirmed distance limit (else the one set
    here, else 1500 m) by 30 m."""
    home = (-6.1758, 106.8263)
    north = lambda m: [home[0] + m / 111195.0, home[1]]  # noqa: E731
    be = _Backend()
    link = Link(be, lambda m: None, _Stub(), _Stub())
    link.tel.relay_reply, link.tel.got_version = 2, True
    stop = threading.Event()

    def feed():
        p8 = _pack([(48, 8, 0xEF), (75, 2, 2), (90, 5, 18), (95, 1, 1)], 14)
        p9c = _pack([(10, 12, 1150), (33, 7, 50)], 12)
        p9d = struct.pack("<iihhBb", round(home[1] * 1e7), round(home[0] * 1e7), 300, 0, 0, 0)
        while not stop.is_set():
            link.on_bytes(_frame(5, _msg(0x9D, p9d) + _msg(0x9C, p9c) + _msg(0x8B, p8)))
            time.sleep(0.1)

    threading.Thread(target=feed, daemon=True).start()
    try:
        link.start()
        link.on_connected(True)
        time.sleep(0.6)
        f = link.flight
        assert link.tel.home is not None
        assert "At most 16" in f.start_mission([north(100)] * 17, 30, 4, 2)
        r = f.start_mission([north(100), north(1600)], 30, 4, 2)  # limit unknown: 1500 m
        assert r.startswith("Waypoint 2 is 1.60 km from home") and "limit is 1.50 km" in r, r
        f.saved_limits = [120, 6000, 30]
        link.on_bytes(_frame(5, _msg(0xAB, b"HF-SH-F22N7R406.024.0509" + struct.pack("<HHHH", 120, 500, 30, 0))))
        time.sleep(0.2)
        r = f.start_mission([north(900)], 30, 4, 2)  # the drone kept 500 m: its answer wins
        assert r.startswith("Waypoint 1 is 900 m from home") and "limit is 500 m" in r, r
        link.on_bytes(_frame(5, _msg(0xAB, b"HF-SH-F22N7R406.024.0509" + struct.pack("<HHHH", 120, 6000, 30, 0))))
        time.sleep(0.2)
        assert "limit is 6.00 km" in f.start_mission([north(5980)], 30, 10, 2)  # within 30 m of the limit
        assert f.start_mission([north(2000), north(5900)], 30, 10, 2) is None
        assert f.mission["state"] == "uploading" and f.mission["total"] == 2
        assert f.start_mission([north(100)], 30, 4, 2) == "A mission is already uploading."
    finally:
        stop.set()
        link.connected = False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
    print("all passed")
