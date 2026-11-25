"""Flight commands, sent the way the official app sends them (port of Flight.java).

After a startup handshake: a control and an extend message every 100 ms with the sticks centred; a
button press is a flag held at 1 for one second. Every command is checked against live telemetry.
Results of the drone tests (props off): start motors, take off (only with the motors running), land,
waypoint upload all work; GPS/attitude and sport are the remote's buttons only.
"""
import math
import threading
import time

from . import protocol as P

STICK_LIMIT = 0.7  # never reach the full-deflection stick combinations (motor start, compass cal, reset)


def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class Flight:
    def __init__(self, link, tel, log):
        self.link, self.tel, self.log = link, tel, log
        self.lock = threading.RLock()
        self.holds = {}    # control field -> (value, until)
        self.xholds = {}   # extend field -> until
        self.sticks = [0.0, 0.0, 0.0, 0.0]  # yaw, throttle, roll, pitch (-1..1)
        self.sticks_at = 0.0
        self.sticks_enabled = True
        self.yaw_hold = (128, 0.0)
        self.enabled = True  # send flight messages (off when another device is the pilot)
        self.handshake = "idle"
        self.mission = {"state": "idle", "msg": "", "total": 0, "acked": 0}
        self._mission = None
        self.recording = False
        self.record_start = 0.0
        self.photo_seq = 1
        self.gimbal_target, self.gimbal_sent_at = 0, 0.0
        self.takeoff_deadline = self.takeoff_at = 0.0
        self.thread = None

    # ---------- loop ----------
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name="flight", daemon=True)
        self.thread.start()

    def _run(self):
        self._handshake()
        nxt = time.monotonic()
        while self.link.connected:
            try:
                self._tick()
            except Exception as e:  # keep the loop alive
                self.log(f"flight loop: {e}")
            nxt += 0.1
            delay = nxt - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.monotonic()
        self.handshake = "idle"
        if self.mission["state"] == "uploading":
            self._fail("The remote was disconnected.")
        if self.recording:
            self.recording = False
            self.link.media.stop_recording()

    def _handshake(self):
        self.handshake = "relay"
        for _ in range(20):
            if not self.link.connected or self.tel.relay_reply is not None:
                break
            self.link.send_drone(P.FIX_HACK_FLY)
            time.sleep(0.1)
        self.handshake = "version"
        req = P.version_request()
        for _ in range(25):
            if not self.link.connected or self.tel.got_version:
                break
            self.link.send_drone(req)
            time.sleep(0.2)
        self.handshake = "ok" if self.tel.got_version else "no-reply"

    def _tick(self):
        now = time.monotonic()
        self._mission_tick(now)
        self._takeoff_tick(now)
        if not self.enabled:
            return
        with self.lock:
            c = dict(P.CONTROL_DEFAULTS)
            for f, (v, until) in self.holds.items():
                if now < until:
                    c[f] = v
            x = {f: 1 for f, until in self.xholds.items() if now < until}
            if self.recording:
                c["video"] = 1
            # While the app steers, set both ctrlPanel and VisionFollow like the official app's follow mode.
            if self.sticks_enabled and now - self.sticks_at < 0.4:
                for name, v in zip(("rocker4", "rocker2", "rocker1", "rocker3"), self.sticks):
                    c[name] = max(1, min(255, round(128 + v * 127)))
                c["ctrlPanel"] = c["VisionFollow"] = 1
            if now < self.yaw_hold[1]:
                c["rocker4"] = self.yaw_hold[0]
                c["ctrlPanel"] = c["VisionFollow"] = 1
        self.link.send_drone(P.control(c))
        self.link.send_drone(P.extend(x))

    def _hold(self, field, value, seconds):
        with self.lock:
            self.holds[field] = (value, time.monotonic() + seconds)

    # ---------- commands ----------
    def set_sticks(self, yaw, thr, roll, pitch):
        def clamp(v):
            v = float(v)
            return 0.0 if math.isnan(v) else max(-STICK_LIMIT, min(STICK_LIMIT, v))
        with self.lock:
            self.sticks = [clamp(yaw), clamp(thr), clamp(roll), clamp(pitch)]
            self.sticks_at = time.monotonic()

    def command(self, name):
        """Runs a named command; returns None on success or a message for the pilot."""
        if not self.link.connected:
            return "The remote is not connected."
        flight_cmd = not name.startswith(("photo", "record"))
        if flight_cmd and not self.enabled:
            return "Flight control from this computer is off."
        if flight_cmd and not name.startswith(("gimbal", "yaw")) and name != "cancel" and not self.tel.fresh():
            return "No live data from the drone. Is it switched on?"
        d = self.tel.d
        armed = d["armed"]
        if name == "unlock":
            if armed:
                return "The motors are already running."
            self._hold("lockUnlock", 1, 1.0)
        elif name == "takeoff":
            if armed == 2:
                return "Already flying."
            if armed == 0:  # take-off is ignored with the motors stopped: start them first
                self._hold("lockUnlock", 1, 1.0)
                with self.lock:
                    self.takeoff_at, self.takeoff_deadline = 0.0, time.monotonic() + 7
                self.log("Take off: starting the motors first.")
                return None
            self._hold("autoTakeoff", 1, 1.0)
        elif name == "land":
            if armed == 0:
                return "The motors are not running."
            self._hold("autoLand", 1, 1.0)
        elif name == "rth":
            if armed != 2:
                return "Return to home works in flight only."
            if d["indoor"] == 1:
                return "Return to home needs GPS mode (the drone is in attitude mode)."
            if not d["gpsFine"]:
                return "Return to home needs a GPS fix."
            self._hold("goHome", 1, 1.0)
        elif name == "cancel":
            self._hold("gpsMode", 1, 1.0)
            if self.mission["state"] in ("uploading", "running"):
                self.mission.update(state="cancelled", msg="Mission cancelled.")
        elif name in ("gimbalUp", "gimbalDown"):
            now = time.monotonic()
            base = self.gimbal_target if now - self.gimbal_sent_at < 1.5 else (
                d["gimbalPos"] if d["gimbalPos"] >= 0 else self.gimbal_target)
            step = -P.GIMBAL_STEP if name == "gimbalUp" else P.GIMBAL_STEP
            self.gimbal_target = max(0, min(P.GIMBAL_MAX, base + step))
            self.gimbal_sent_at = now
            self.link.send_drone(P.gimbal_position(self.gimbal_target))
            return None
        elif name == "gimbalStop":
            return None
        elif name in ("yawLeft", "yawRight"):
            if armed != 2:
                return "Turning works in flight only."
            with self.lock:
                self.yaw_hold = (128 - 45 if name == "yawLeft" else 128 + 45, time.monotonic() + 0.45)
            return None
        elif name == "beep":
            if armed:
                return "Only with the motors stopped."
            with self.lock:
                self.xholds["MotorBeepCrtl"] = time.monotonic() + 1.0
        elif name == "photo":
            self.link.send_drone(P.camera(P.CAM_PHOTO, self.photo_seq & 0xFF))
            self.photo_seq = 1 if self.photo_seq >= 255 else self.photo_seq + 1
            return None
        elif name == "record":
            if self.recording:
                self.link.send_drone(P.camera(P.CAM_RECORD_STOP))
                self.recording = False
                self.link.media.stop_recording()
            else:
                self.link.send_drone(P.camera(P.CAM_RECORD_START))
                self.recording = True
                self.record_start = time.monotonic()
                self.link.media.start_recording()
            return None
        else:
            return f"Unknown command: {name}"
        self.log(f"{name}: sent.")
        return None

    def _takeoff_tick(self, now):
        with self.lock:
            if not self.takeoff_deadline:
                return
            armed = self.tel.d["armed"]
            if armed == 2:
                self.takeoff_deadline = 0.0
            elif now > self.takeoff_deadline:
                self.takeoff_deadline = 0.0
                self.log("Take off: the motors did not start (GPS mode needs a GPS fix).")
            elif armed == 1:
                if not self.takeoff_at:
                    self.takeoff_at = now + 1.5
                elif now >= self.takeoff_at:
                    self._hold("autoTakeoff", 1, 1.0)
                    self.takeoff_deadline = 0.0
                    self.log("Take off: sent.")

    # ---------- waypoint mission ----------
    def start_mission(self, points, alt, speed, hover):
        d = self.tel.d
        if not self.link.connected:
            return "The remote is not connected."
        if not self.enabled:
            return "Flight control from this computer is off."
        if not self.tel.fresh():
            return "No live data from the drone."
        if d["armed"] != 2:
            return "Take off first: waypoints are flown from the air."
        if d["indoor"] == 1:
            return "Switch the drone to GPS mode first."
        if not d["gpsFine"] or not d["hasFix"]:
            return "Waiting for a GPS fix."
        if not 1 <= len(points) <= 32:
            return "1 to 32 waypoints."
        if not 5 <= alt <= 120:
            return "Altitude must be 5 to 120 m."
        if not 1 <= speed <= 10:
            return "Speed must be 1 to 10 m/s."
        if not 0 <= hover <= 60:
            return "Hover time must be 0 to 60 s."
        for i, (lat, lon) in enumerate(points):
            dist = distance_m(d["lat"], d["lon"], lat, lon)
            if dist > 1500:
                return f"Waypoint {i + 1} is {dist:.0f} m away (limit 1500 m)."
        with self.lock:
            self._mission = {"points": points, "alt": alt, "speed": int(speed), "hover": int(hover),
                             "step": 0, "step_start": time.monotonic(), "last_send": 0.0, "saw_point": False}
            self.tel.waypoint_ack = -1
            self.mission = {"state": "uploading", "msg": "Uploading waypoints...", "total": len(points), "acked": 0}
        self.log(f"Mission: uploading {len(points)} waypoints at {alt:.0f} m, {speed} m/s.")
        return None

    def clear_mission(self):
        if self.mission["state"] != "uploading":
            self.mission = {"state": "idle", "msg": "", "total": 0, "acked": 0}

    def _fail(self, why):
        self.mission.update(state="failed", msg=why)
        self.log("Mission: " + why)

    def _mission_tick(self, now):
        m, ms = self._mission, self.mission
        if m is None:
            return
        if ms["state"] == "running":
            if self.tel.d["flyMode"] == 4:
                m["saw_point"] = True
            elif m["saw_point"]:
                self.mission = {"state": "idle", "msg": "Mission finished.", "total": ms["total"], "acked": ms["total"]}
                self._mission = None
            return
        if ms["state"] != "uploading":
            return
        ack = self.tel.waypoint_ack
        if m["step"] <= ack < ms["total"]:
            m["step"] = ack + 1
            ms["acked"] = m["step"]
            m["step_start"], m["last_send"] = now, 0.0
        if m["step"] >= ms["total"]:
            ms.update(state="running", msg="All waypoints confirmed. Starting the mission.")
            self._hold("popublicFly", 1, 1.0)
            self.log("Mission: all waypoints confirmed, start sent.")
            return
        if now - m["step_start"] > 6:
            self._fail(f"The drone did not confirm waypoint {m['step'] + 1}.")
            return
        if now - m["last_send"] >= 0.2:
            lat, lon = m["points"][m["step"]]
            self.link.send_drone(P.waypoint(m["step"], lat, lon, m["alt"], m["speed"], m["hover"]))
            m["last_send"] = now
