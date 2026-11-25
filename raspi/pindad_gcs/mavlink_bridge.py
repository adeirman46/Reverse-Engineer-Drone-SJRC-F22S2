"""Optional MAVLink output (pymavlink), so QGroundControl or the UGV's computer can see the drone.

Sends HEARTBEAT, SYS_STATUS, GPS_RAW_INT, GLOBAL_POSITION_INT, ATTITUDE and VFR_HUD, and accepts
COMMAND_LONG: NAV_TAKEOFF, NAV_LAND, NAV_RETURN_TO_LAUNCH, DO_PAUSE_CONTINUE (pause = stop the
automatic flight) and COMPONENT_ARM_DISARM (arm = start motors; disarm on the ground needs the remote).
Example: --mavlink udpout:192.168.1.50:14550   (QGroundControl listens on UDP 14550)
"""
import math
import os
import threading
import time

os.environ.setdefault("MAVLINK20", "1")

MAV_TYPE_QUADROTOR, MAV_AUTOPILOT_GENERIC = 2, 0
MAV_MODE_FLAG_SAFETY_ARMED, MAV_MODE_FLAG_CUSTOM_MODE_ENABLED = 128, 1
MAV_STATE_STANDBY, MAV_STATE_ACTIVE = 3, 4
ACCEPTED, DENIED, UNSUPPORTED, FAILED = 0, 2, 3, 4
COMMANDS = {22: "takeoff", 21: "land", 20: "rth"}


class MavlinkBridge:
    def __init__(self, url, link, log):
        from pymavlink import mavutil  # optional dependency
        self.conn = mavutil.mavlink_connection(url, source_system=1, source_component=1)
        self.link, self.log = link, log
        self.t0 = time.monotonic()
        threading.Thread(target=self._run, name="mavlink", daemon=True).start()
        log(f"MAVLink: sending to {url}")

    def _ms(self):
        return int((time.monotonic() - self.t0) * 1000)

    def _run(self):
        n = 0
        while True:
            try:
                self._send(n)
                self._receive()
            except Exception as e:  # keep going on network hiccups
                self.log(f"MAVLink: {e}")
                time.sleep(1)
            n += 1
            time.sleep(0.1)

    def _send(self, n):
        m, d, link = self.conn.mav, self.link.tel.snapshot(), self.link
        armed = d["armed"] != 0
        if n % 10 == 0:
            base = MAV_MODE_FLAG_CUSTOM_MODE_ENABLED | (MAV_MODE_FLAG_SAFETY_ARMED if armed else 0)
            m.heartbeat_send(MAV_TYPE_QUADROTOR, MAV_AUTOPILOT_GENERIC, base, d["flyMode"],
                             MAV_STATE_ACTIVE if armed else MAV_STATE_STANDBY)
            m.sys_status_send(0, 0, 0, 0, int(d["batV"] * 1000), -1, d["batPct"] if d["batPct"] >= 0 else -1,
                              0, 0, 0, 0, 0, 0)
        if not link.connected or not d["fresh"]:
            return
        yaw = math.radians(d["yaw"])
        m.attitude_send(self._ms(), math.radians(d["roll"]), math.radians(d["pitch"]), yaw, 0, 0, 0)
        if n % 2 == 0:
            lat, lon = int(d["lat"] * 1e7), int(d["lon"] * 1e7)
            alt_mm = int(d["alt"] * 1000)
            hdg = int((d["yaw"] % 360) * 100)
            vx, vy = d["hs"] * math.cos(yaw) * 100, d["hs"] * math.sin(yaw) * 100
            m.gps_raw_int_send(int(time.time() * 1e6), 3 if d["gpsFine"] else (1 if not d["hasFix"] else 2),
                               lat, lon, alt_mm, 65535, 65535, int(d["hs"] * 100), hdg, d["gpsNum"])
            m.global_position_int_send(self._ms(), lat, lon, alt_mm, alt_mm, int(vx), int(vy),
                                       int(-d["vs"] * 100), hdg)
            m.vfr_hud_send(d["hs"], d["hs"], int(d["yaw"] % 360), 0, d["alt"], d["vs"])

    def _receive(self):
        while True:
            msg = self.conn.recv_match(blocking=False)
            if msg is None:
                return
            t = msg.get_type()
            if t == "COMMAND_LONG":
                self._command(msg)
            elif t == "PARAM_REQUEST_LIST":  # QGroundControl waits for parameters: give it one
                self.conn.mav.param_value_send(b"SYSID_THISMAV", 1.0, 9, 1, 0)
            elif t == "MISSION_REQUEST_LIST":
                self.conn.mav.mission_count_send(msg.get_srcSystem(), msg.get_srcComponent(), 0)

    def _command(self, msg):
        cmd, flight = msg.command, self.link.flight
        name = COMMANDS.get(cmd)
        if cmd == 400:  # COMPONENT_ARM_DISARM
            name = "unlock" if msg.param1 >= 0.5 else None
            if name is None:
                self._ack(cmd, DENIED, "Stopping the motors on the ground needs the remote (throttle down).")
                return
        elif cmd == 193:  # DO_PAUSE_CONTINUE: pause = stop the automatic flight
            name = "cancel" if msg.param1 < 0.5 else None
        if name is None:
            self._ack(cmd, UNSUPPORTED)
            return
        err = flight.command(name)
        self._ack(cmd, FAILED if err else ACCEPTED, err)

    def _ack(self, cmd, result, text=None):
        self.conn.mav.command_ack_send(cmd, result)
        if text:
            self.conn.mav.statustext_send(4, text[:50].encode())
