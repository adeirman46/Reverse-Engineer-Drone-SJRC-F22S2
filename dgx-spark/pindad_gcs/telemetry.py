"""Decodes the drone's telemetry (port of Telemetry.java; same field names as the app's interface).

Frame type 5 carries a stream of messages: 58 id len payload xor(id, len, payload).
Frame type 6 carries status messages from the remote's camera/relay board: FF 53 54 cmd ...
"""
import struct
import threading
import time

from .battery import BatteryEstimator
from .protocol import bits, xor


def _ms():
    return int(time.monotonic() * 1000)


def _ascii(b):
    return "".join(chr(c) for c in b if 32 <= c < 127).strip()


class Telemetry:
    def __init__(self, log):
        self.log = log
        self.lock = threading.Lock()
        self.stream = bytearray()
        self.d = {  # same keys as the Android app's state "d"
            "armed": 0, "flyMode": 0, "goHome": 0, "modeGps": 0, "indoor": -1, "gpsNum": 0, "gpsFine": 0,
            "hasFix": 0, "lat": 0.0, "lon": 0.0, "alt": 0.0, "dist": 0, "hs": 0.0, "vs": 0.0, "roll": 0.0,
            "pitch": 0.0, "yaw": 0.0, "batV": 0.0, "batPct": -1, "lowBat": 0, "remotePct": -1, "magInt": 0,
            "magErr": 0, "magCal": 0, "oaWarn": 0, "oaDis": 0, "hLimit": 0, "dLimit": 0, "sport": 0,
            "autoMode": 0, "rtl": 0, "takeoff": 0, "autoLand": 0, "landFinish": 0, "forbid": 0, "gale": 0,
            "tempOver": 0, "currOver": 0, "emergRth": 255, "lowRth": 255, "gimbal": 0, "gimbalPos": -1,
            "rcSig": 0, "terrain": 65535, "sensors": 0, "calib": 0, "stRoll": 127, "stPitch": 127,
            "stThr": 127, "stYaw": 127, "maxAlt": 0.0, "maxDist": 0, "fc": "", "fcLog": "",
        }
        self.bat_v_8b = 0.0
        self.last_any = self.last_8b = 0.0
        self.relay_reply = None
        self.got_version = False
        self.waypoint_ack = -1
        self.home = None
        self.armed_since = 0.0
        self.last_flight_s = 0.0
        self.battery = BatteryEstimator()  # steady percentage (the drone's own number sags in flight)
        self.lim = None  # flight limits the drone confirmed (AB / 9A): {"alt", "dist", "rth", "at"}

    # ---------- state helpers ----------
    def fresh(self):
        return self.last_8b > 0 and time.monotonic() - self.last_8b < 1.5

    def reset(self):
        with self.lock:
            self.stream.clear()
            self.relay_reply = None
            self.got_version = False
            self.lim = None  # the drone answers its limits again after each connection (the app re-sends them)
        if self.battery.stale_for(_ms(), 60000):  # keep the estimate over a short reconnect
            self.battery.reset()

    def snapshot(self):
        with self.lock:
            d = dict(self.d)
        now = time.monotonic()
        d["fresh"] = 1 if self.fresh() else 0
        d["age"] = int((now - self.last_any) * 1000) if self.last_any else -1
        d["flightS"] = (now - self.armed_since) if self.armed_since else self.last_flight_s
        if d["batV"] <= 0:
            d["batV"] = self.bat_v_8b
        d["batPct"] = self.battery.percent()
        d["batRaw"] = self.battery.raw()
        d["batSag"] = self.battery.sag_pct()
        return d

    def blockers(self):
        """Why the drone may refuse to start its motors, from its live status (same texts as the app)."""
        r = []
        if not self.fresh():
            return r
        d = self.d
        sensors = d["sensors"]
        if d["forbid"] == 1:
            r.append("The drone has a no-fly block (set by the official app). Clear it in Settings › Flight limits.")
        if not sensors & 0x01:
            r.append("Gyro not ready: keep the drone still on level ground.")
        if not sensors & 0x02:
            r.append("Barometer not ready.")
        if not sensors & 0x04:
            r.append("Compass not detected: restart the drone.")
        # 8B byte 6 bits 5-7 are 1 when calibrated; 0 while a calibration started with the sticks runs
        if sensors & 0xC0 != 0xC0:
            r.append("Compass calibration is running: finish it or restart the drone.")
        if not sensors & 0x20:
            r.append("Level calibration is running: keep the drone still on level ground.")
        if d["magErr"] == 1:
            r.append("Compass error: restart the drone, then calibrate the compass.")
        elif d["magCal"] == 1 or d["magInt"] > 150:
            # The check is the drone's own; no command switches it off. Attitude mode does not hold position with
            # GPS + compass (the manual's way out when GPS mode will not start); not yet confirmed for the compass.
            s = (f"Compass disturbed here (interference {d['magInt']}, normal below 150): carry"
                 " the drone a few metres away from metal, cars and reinforced concrete")
            s += (", or switch to attitude mode with the remote's GPS button (3 s) and steer it yourself (it drifts"
                  " with the wind)." if d["indoor"] == 0 else ".")
            r.append(s + " If it stays, calibrate the compass in an open place.")
        if d["indoor"] == 0 and d["gpsFine"] == 0:
            r.append(f"GPS mode needs a GPS fix ({d['gpsNum']} satellites): wait in the open, or switch to"
                     " attitude mode with the remote's GPS button (3 s).")
        if d["lowBat"] >= 1:
            v = d["batV"] if d["batV"] > 0 else self.bat_v_8b
            r.append(f"The drone reports low battery ({v:.2f} V): charge or swap the battery.")
        if abs(d["roll"]) > 25 or abs(d["pitch"]) > 25:
            r.append("The drone is not level.")
        if d["tempOver"] == 1:
            r.append("The drone is overheating.")
        return r

    # ---------- input ----------
    def on_frame(self, ftype, payload):
        if ftype == 5:
            self._feed_stream(payload)
        elif ftype == 6 and len(payload) >= 4 and payload[:3] == b"\xff\x53\x54":
            cmd = payload[3]
            if cmd == 0x24 and self.relay_reply is None:
                self.relay_reply = payload[4] if len(payload) > 4 else 0
                self.log("Relay answered the startup handshake.")
            elif cmd == 0x61 and len(payload) > 4:
                self.d["gimbalPos"] = payload[4]  # gimbal tilt position
            elif cmd == 0x52 and len(payload) > 4:
                self.d["rcSig"] = struct.unpack("b", payload[4:5])[0]  # remote link signal

    def _feed_stream(self, payload):
        s = self.stream
        s += payload
        if len(s) > 65536:
            del s[:-4096]
        j = 0
        while j + 4 <= len(s):
            if s[j] != 0x58:
                j += 1
                continue
            mid, n = s[j + 1], s[j + 2]
            if j + 4 + n > len(s):
                break
            if xor(s[j + 1:j + 3 + n]) == s[j + 3 + n]:
                self._decode(mid, bytes(s[j + 3:j + 3 + n]))
                j += 4 + n
            else:
                j += 1
        del s[:j]

    def _decode(self, mid, p):
        now = time.monotonic()
        self.last_any = now
        with self.lock:
            d = self.d
            if mid == 0x8B and len(p) >= 14:
                roll, pitch, yaw = struct.unpack_from("<hhh", p, 0)
                armed = bits(p, 75, 2)
                d.update(roll=roll / 100, pitch=pitch / 100, yaw=yaw / 100, sensors=p[6], calib=bits(p, 56, 7),
                         lowBat=bits(p, 70, 2), tempOver=bits(p, 72, 1), currOver=bits(p, 73, 2),
                         flyMode=bits(p, 77, 4), goHome=bits(p, 81, 3), takeoff=bits(p, 87, 1),
                         autoLand=bits(p, 88, 1), landFinish=bits(p, 89, 1), gpsNum=bits(p, 90, 5),
                         gpsFine=bits(p, 95, 1), modeGps=bits(p, 103, 2), forbid=bits(p, 109, 1),
                         gale=bits(p, 110, 1))
                self.bat_v_8b = bits(p, 63, 7) / 10
                if armed and not d["armed"]:
                    self.armed_since = now
                    d["maxAlt"], d["maxDist"] = 0.0, 0
                    self.home = (d["lat"], d["lon"]) if d["hasFix"] and d["gpsFine"] else None
                    self.log("Motors started.")
                elif not armed and d["armed"]:
                    self.last_flight_s = now - self.armed_since
                    self.armed_since = 0.0
                    self.log("Motors stopped.")
                if armed and self.home is None and d["hasFix"] and d["gpsFine"]:
                    self.home = (d["lat"], d["lon"])
                d["armed"] = armed
                self.last_8b = now
            elif mid == 0x9B and len(p) >= 36:
                d.update(stRoll=p[24], stPitch=p[25], stThr=p[26], stYaw=p[27])
            elif mid == 0x9C and len(p) >= 12:
                d.update(magInt=bits(p, 0, 10), batV=bits(p, 10, 12) / 100, indoor=bits(p, 32, 1),
                         remotePct=bits(p, 54, 7), magErr=bits(p, 63, 1),
                         hLimit=bits(p, 64, 1), dLimit=bits(p, 65, 1), oaWarn=bits(p, 67, 1),
                         oaDis=bits(p, 68, 5), magCal=bits(p, 73, 1), sport=bits(p, 74, 1),
                         autoMode=bits(p, 78, 4), rtl=bits(p, 82, 4))
                self.battery.update(bits(p, 33, 7), d["armed"], d["lowBat"], int(now * 1000))
            elif mid == 0x9D and len(p) >= 14:
                lon, lat, alt, dist = struct.unpack_from("<iihh", p, 0)
                fix = lon != 0 or lat != 0
                d.update(alt=alt / 10, dist=dist, hs=p[12] / 10, vs=struct.unpack_from("b", p, 13)[0] / 10,
                         hasFix=1 if fix else 0)
                if fix:
                    d.update(lat=lat / 1e7, lon=lon / 1e7)
                if self.armed_since:
                    d["maxAlt"] = max(d["maxAlt"], d["alt"])
                    d["maxDist"] = max(d["maxDist"], dist)
            elif mid == 0x9E and len(p) >= 10:
                gimbal, terrain = struct.unpack_from("<hH", p, 0)
                d.update(gimbal=gimbal, terrain=terrain, emergRth=p[5], lowRth=p[9])
            elif mid == 0x84 and len(p) >= 1:
                self.waypoint_ack = p[0]
            elif mid == 0xAB and len(p) >= 30:  # settings reply: name, version, then u16 metres
                alt, dist, rth = struct.unpack_from("<HHH", p, 24)
                self.lim = {"alt": alt, "dist": dist, "rth": rth, "at": now}
            elif mid in (0xFE, 0x9A):
                if not self.got_version:
                    self.got_version = True
                    d["fc"] = _ascii(p[:24])
                    self.log("Flight controller: " + d["fc"])
                if mid == 0x9A and len(p) >= 28:  # older settings reply: 10/12/10-bit fields
                    self.lim = {"alt": bits(p, 192, 10), "dist": bits(p, 202, 12), "rth": bits(p, 214, 10), "at": now}
            elif mid == 0xBE:
                d["fcLog"] = _ascii(p)
