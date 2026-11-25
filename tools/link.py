#!/usr/bin/env python3
"""
link.py - talk to the SJRC F22 S2 PRO+ through the Pindad UGV - UAV app on the phone (TCP port 9000).

Formats (see NOTES.md):
  PC -> phone:   [frame type LE16][length LE16][payload]; the phone wraps it in an FF 56 53 frame.
  drone msgs:    frame type 6, e.g. control 68 01 0D <13 bytes> <xor>, extend 68 0B 08 <8 bytes> <xor>,
                 settings FF 53 54 <cmd> <arg>.
  phone -> PC:   raw FF 56 53 frames from the remote; type 5 = telemetry stream of
                 58 <id> <len> <payload> <xor>, type 6 = FF 53 54 status messages.
"""
import collections
import socket
import struct
import threading
import time

# Bit-packed payloads, least significant bit first (decoded from LGDataUtils in the emulator).
CONTROL_FIELDS = [
    ("rocker1", 8), ("rocker2", 8), ("rocker3", 8), ("rocker4", 8),
    ("leftRightTrim", 6), ("upDownTrim", 6), ("speed", 1), ("noHead", 1), ("roll", 1), ("gpsMode", 1),
    ("levelCor", 1), ("magCor", 1), ("lockUnlock", 1), ("autoTakeoff", 1), ("autoLand", 1), ("goHome", 1),
    ("stop", 1), ("followMe", 1), ("circleFly", 1), ("popublicFly", 1), ("photo", 1), ("video", 1),
    ("PTZ_H", 2), ("PTZ_V", 2), ("ctrlPanel", 1), ("display", 1), ("OpticalFlowOn", 1),
    ("VisionFollow", 1), ("Dis_valid", 1), ("Distance", 12), ("Box_x", 12), ("Box_y", 11),
]
EXTEND_FIELDS = [
    ("IndoorMode", 1), ("CloseTx", 1), ("CloseTxCnt", 3), ("SportMode", 1), ("HYVisualOA", 1),
    ("HYVisualWarn", 1), ("Reserve0", 2), ("SkyFly", 1), ("FarFly", 1), ("SpiralFly", 1),
    ("CruiseHorFlag", 1), ("CruiseHorPec", 3), ("CruiseVerFlag", 1), ("CruiseVerPec", 3),
    ("ForbidTakeOff", 1), ("EmergGoHomeAborted", 1), ("EmergGoHomeStart", 1),
    ("LowPower1GoHomeAborted", 1), ("LowPower1GoHomestart", 1), ("ArmServerCrtl", 1),
    ("MotorBeepCrtl", 1), ("ArmLightCrtl", 1), ("GSMModeCrtl", 1), ("GSMModuleSta", 1),
    ("BottomTOFShutdown", 1), ("Reserve2", 32),
]
CONTROL_DEFAULTS = {"rocker1": 128, "rocker2": 128, "rocker3": 128, "rocker4": 128,
                    "leftRightTrim": 32, "upDownTrim": 32}


def pack_bits(fields, values, nbytes):
    v, pos = 0, 0
    for name, width in fields:
        v |= (int(values.get(name, 0)) & ((1 << width) - 1)) << pos
        pos += width
    return v.to_bytes(nbytes, "little")


def xor(data):
    x = 0
    for b in data:
        x ^= b
    return x


def control_message(values):
    """17-byte control message, as SendHyControlThread.controlData() builds it."""
    m = bytearray([0x68, 0x01, 0x0D]) + pack_bits(CONTROL_FIELDS, {**CONTROL_DEFAULTS, **values}, 13)
    return bytes(m + bytes([xor(m[1:16])]))


def extend_message(values):
    """12-byte extend message, as SendHyControlThread.extendData() builds it."""
    m = bytearray([0x68, 0x0B, 0x08]) + pack_bits(EXTEND_FIELDS, values, 8)
    return bytes(m + bytes([xor(m[1:11])]))


def bits(data, lo, n):
    return (int.from_bytes(data, "little") >> lo) & ((1 << n) - 1)


def connect(host, port, timeout=5):
    """TCP connect, trying each network interface in turn if the default route fails.
    Needed when the PC has two links on the same subnet (e.g. cable + Wi-Fi) and only one
    of them reaches the phone."""
    try:
        return socket.create_connection((host, port), timeout=2)
    except OSError as first:
        error = first
    for _, name in socket.if_nameindex():
        if name == "lo" or name.startswith(("docker", "br-", "veth")):
            continue
        s = socket.socket()
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, name.encode())
            s.settimeout(timeout)
            s.connect((host, port))
            return s
        except OSError as e:
            error = e
            s.close()
    raise error


class Link:
    """TCP client for the phone bridge: telemetry in, drone messages out."""

    def __init__(self, host, port=9000, capture_path=None):
        self.sock = connect(host, port)
        self.sock.settimeout(1.0)
        self.send_lock = threading.Lock()
        self.running = True
        self.telemetry = {}
        self.telemetry_time = 0.0
        self.status6 = {}
        self.new_status6 = []
        self.raw_messages = {}
        self.frames = collections.Counter()
        self.raw = open(capture_path, "wb") if capture_path else None
        self.control = dict(CONTROL_DEFAULTS)
        self.extend = {}
        self._control_thread = None
        threading.Thread(target=self._reader, daemon=True).start()

    # ---------- sending ----------
    def send(self, frame_type, payload):
        if frame_type in (18, 20):
            raise ValueError(f"frame type {frame_type} is blocked")
        with self.send_lock:
            self.sock.sendall(struct.pack("<HH", frame_type, len(payload)) + bytes(payload))

    def send_drone(self, payload):
        self.send(6, payload)

    def start_control(self):
        """Send control + extend every 100 ms, like the official app."""
        if self._control_thread:
            return
        self._control_thread = threading.Thread(target=self._control_loop, daemon=True)
        self._control_thread.start()

    def stop_control(self):
        t, self._control_thread = self._control_thread, None
        if t:
            t.join(timeout=1)

    def _control_loop(self):
        me = threading.current_thread()
        while self.running and self._control_thread is me:
            self.send_drone(control_message(self.control))
            self.send_drone(extend_message(self.extend))
            time.sleep(0.1)

    def pulse(self, field, seconds=1.0, extend=False):
        """Set a flag to 1 for `seconds`, then back to 0 (how the official app presses buttons)."""
        target = self.extend if extend else self.control
        target[field] = 1
        time.sleep(seconds)
        target[field] = 0

    # ---------- receiving ----------
    def _reader(self):
        buf, stream = bytearray(), bytearray()
        while self.running:
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            if self.raw:
                self.raw.write(data)
            buf += data
            while True:
                i = buf.find(b"\xff\x56\x53")
                if i < 0:
                    del buf[:-2]
                    break
                del buf[:i]
                if len(buf) < 11:
                    break
                n = buf[5] | buf[6] << 8
                ftype = buf[7] | buf[8] << 8
                if len(buf) < 11 + n:
                    break
                payload = bytes(buf[11:11 + n])
                del buf[:11 + n]
                self.frames[ftype] += 1
                if ftype == 5:
                    stream += payload
                    self._parse_stream(stream)
                elif ftype == 6 and payload[:3] == b"\xff\x53\x54" and len(payload) >= 4:
                    cmd = payload[3]
                    if cmd not in self.status6:
                        self.new_status6.append((time.time(), cmd, payload[4:]))
                    self.status6[cmd] = payload[4:]
        self.running = False

    def _parse_stream(self, s):
        j = 0
        while j + 4 <= len(s):
            if s[j] != 0x58:
                j += 1
                continue
            mid, ln = s[j + 1], s[j + 2]
            if j + 4 + ln > len(s):
                break
            body = bytes(s[j + 3:j + 3 + ln])
            if xor(s[j + 1:j + 3 + ln]) == s[j + 3 + ln]:
                self._decode(mid, body)
            j += 4 + ln
        del s[:j]

    def handshake(self, log=print):
        """Startup handshake the official app runs for HACK_FLY drones (WelcomeActivity):
        FF 53 54 24 02 ("fixedHackFly") until the relay answers FF 53 54 24 xx, then the 68 7E
        version request until the flight controller answers with message 9A or FE."""
        self.status6.pop(0x24, None)
        for _ in range(20):
            self.send_drone(bytes([0xFF, 0x53, 0x54, 0x24, 0x02]))
            time.sleep(0.1)
            if 0x24 in self.status6:
                log(f"   relay answered: FF 53 54 24 {self.status6[0x24].hex(' ')}")
                break
        else:
            log("   relay did not answer FF 53 54 24 02")
        req = bytearray(34)
        req[0:3] = b"\x68\x7e\x1e"
        req[3:15] = b"HF-XX-XXXXXX"
        req[15:27] = b"000.000.0000"
        req[33] = xor(req[1:33])
        for _ in range(25):
            self.send_drone(bytes(req))
            time.sleep(0.2)
            got = [m for m in (0x9A, 0xFE) if m in self.raw_messages]
            if got:
                for m in got:
                    body = self.raw_messages[m]
                    text = "".join(chr(c) if 32 <= c < 127 else "." for c in body)
                    log(f"   flight controller answered {m:02X}: {body.hex(' ')}  ({text})")
                return True
        log("   flight controller did not answer the version request")
        return False

    def _decode(self, mid, b):
        if mid not in (0x8B, 0x9B, 0x9C, 0x9D, 0x9E, 0xBD):
            self.raw_messages[mid] = b  # rarer messages, kept raw for inspection
        t = self.telemetry
        if mid == 0x8B and len(b) >= 14:
            roll, pitch, yaw = struct.unpack_from("<hhh", b, 0)
            t.update(roll=roll / 100, pitch=pitch / 100, yaw=yaw / 100, batt_v=bits(b, 63, 7) / 10,
                     low_bat=bits(b, 70, 2), armed=bits(b, 75, 2), fly_mode=bits(b, 77, 4),
                     go_home=bits(b, 81, 3), takeoff=bits(b, 87, 1), auto_land=bits(b, 88, 1),
                     land_finish=bits(b, 89, 1), gps_num=bits(b, 90, 5),
                     forbid_takeoff=bits(b, 109, 1))
        elif mid == 0x9B and len(b) >= 36:
            t.update(acc=struct.unpack_from("<hhh", b, 0), imu_temp=b[22],
                     sticks={"roll": b[24], "pitch": b[25], "thr": b[26], "yaw": b[27]},
                     motors=struct.unpack_from("<HHHH", b, 28))
        elif mid == 0x9C and len(b) >= 12:
            t.update(bat_v9c=bits(b, 10, 12) / 100, indoor=bits(b, 32, 1), bat_pct=bits(b, 33, 7),
                     remote_pct=bits(b, 54, 7), mag_need_cal=bits(b, 73, 1), sport=bits(b, 74, 1),
                     auto_mode=bits(b, 78, 4), rtl_step=bits(b, 82, 4))
        elif mid == 0x9D and len(b) >= 14:
            lon, lat, alt, dist = struct.unpack_from("<iihh", b, 0)
            t.update(lat=lat / 1e7, lon=lon / 1e7, alt=alt / 10, dist=dist, hs=b[12] / 10,
                     vs=struct.unpack_from("<b", b, 13)[0] / 10)
        elif mid == 0x9E and len(b) >= 10:
            gimbal, terrain = struct.unpack_from("<hH", b, 0)
            t.update(gimbal=gimbal, terrain_dm=terrain, emerg_rth=b[5], lowpower_rth=b[9])
        self.telemetry_time = time.time()

    def close(self):
        self.running = False
        self.stop_control()
        try:
            self.sock.close()
        except OSError:
            pass
        if self.raw:
            self.raw.close()
