"""Message formats of the SJRC F22 S2 PRO+ (see docs/PROTOCOL.md). Port of Protocol.java.

All bit fields are packed least significant bit first. Drone messages travel to the remote inside
USB frames of type 6: FF 56 53 | seq LE16 | len LE16 | type LE16 | XOR(payload) | 00 | payload.
"""
import struct

# ---------- control message 68 01 0D (13 data bytes), sent every 100 ms ----------
CONTROL_FIELDS = [
    ("rocker1", 8), ("rocker2", 8), ("rocker3", 8), ("rocker4", 8),
    ("leftRightTrim", 6), ("upDownTrim", 6), ("speed", 1), ("noHead", 1), ("roll", 1),
    ("gpsMode", 1),  # the remote's GPS button; the official app uses a 1 s press as "cancel"
    ("levelCor", 1), ("magCor", 1), ("lockUnlock", 1), ("autoTakeoff", 1), ("autoLand", 1),
    ("goHome", 1), ("stop", 1), ("followMe", 1), ("circleFly", 1), ("popublicFly", 1),
    ("photo", 1), ("video", 1), ("PTZ_H", 2), ("PTZ_V", 2), ("ctrlPanel", 1), ("display", 1),
    ("OpticalFlowOn", 1), ("VisionFollow", 1), ("Dis_valid", 1), ("Distance", 12), ("Box_x", 12),
    ("Box_y", 11),
]
CONTROL_DEFAULTS = {"rocker1": 128, "rocker2": 128, "rocker3": 128, "rocker4": 128,
                    "leftRightTrim": 32, "upDownTrim": 32}

# ---------- extend message 68 0B 08 (8 data bytes), sent every 100 ms ----------
EXTEND_FIELDS = [
    ("IndoorMode", 1), ("CloseTx", 1), ("CloseTxCnt", 3), ("SportMode", 1), ("HYVisualOA", 1),
    ("HYVisualWarn", 1), ("Reserve0", 2), ("SkyFly", 1), ("FarFly", 1), ("SpiralFly", 1),
    ("CruiseHorFlag", 1), ("CruiseHorPec", 3), ("CruiseVerFlag", 1), ("CruiseVerPec", 3),
    ("ForbidTakeOff", 1), ("EmergGoHomeAborted", 1), ("EmergGoHomeStart", 1),
    ("LowPower1GoHomeAborted", 1), ("LowPower1GoHomestart", 1), ("ArmServerCrtl", 1),
    ("MotorBeepCrtl", 1), ("ArmLightCrtl", 1), ("GSMModeCrtl", 1), ("GSMModuleSta", 1),
    ("BottomTOFShutdown", 1), ("Reserve2", 32),
]

# USB heartbeats, sent every second (UsbWriteCommands in the official app)
HB_TYPE1 = bytes(range(10))
HB_TYPE5 = bytes(range(10)) + b"\x25\x25"
HB_VIDEO = bytes(range(10)) + b"\x28\x28"  # frame type 3: asks the remote for video

BLOCKED_FRAME_TYPES = (18, 20)  # power and relay firmware upgrade: never sent


def xor(data):
    x = 0
    for b in data:
        x ^= b
    return x


def pack_bits(fields, values, nbytes):
    v, pos = 0, 0
    for name, width in fields:
        v |= (int(values.get(name, 0)) & ((1 << width) - 1)) << pos
        pos += width
    return v.to_bytes(nbytes, "little")


def bits(data, lo, n):
    return (int.from_bytes(data, "little") >> lo) & ((1 << n) - 1)


def message(msg_id, data):
    """68 id len data xor(id, len, data)."""
    m = bytes([0x68, msg_id, len(data)]) + bytes(data)
    return m + bytes([xor(m[1:])])


def control(values):
    return message(0x01, pack_bits(CONTROL_FIELDS, {**CONTROL_DEFAULTS, **values}, 13))


def extend(values):
    return message(0x0B, pack_bits(EXTEND_FIELDS, values, 8))


def waypoint(index, lat, lon, alt_m, speed, hover):
    """68 04 0C: lon, lat x1e7 (int32), point number (5 bits), altitude dm (12), speed (7), hover s (8).
    The flight controller confirms each point with message 0x84 (payload[0] = point number)."""
    alt = max(0, min(4095, round(alt_m * 10)))
    tail = (index & 0x1F) | alt << 5 | (speed & 0x7F) << 17 | (hover & 0xFF) << 24
    return message(0x04, struct.pack("<iiI", round(lon * 1e7), round(lat * 1e7), tail))


FIX_HACK_FLY = bytes([0xFF, 0x53, 0x54, 0x24, 0x02])  # startup handshake step 1 (relay)


def version_request():
    """Startup handshake step 2: the flight controller answers message 9A or FE."""
    return message(0x7E, b"HF-XX-XXXXXX" + b"000.000.0000" + bytes(6))


def camera(cmd, *args):
    """HiSi camera command FF 35 19 0A cmd [args]: photo 0x3D seq, record start 0x22, stop 0x23."""
    return bytes([0xFF, 0x35, 0x19, 0x0A, cmd, *args])


CAM_PHOTO, CAM_RECORD_START, CAM_RECORD_STOP = 0x3D, 0x22, 0x23
GIMBAL_STEP, GIMBAL_MAX = 9, 90


def gimbal_position(pos):
    """Gimbal tilt (official setPTZData, HiSi branch): FF 53 54 20 01 pos; 0 = level."""
    return bytes([0xFF, 0x53, 0x54, 0x20, 0x01, pos & 0xFF])


def usb_frame(frame_type, payload, seq):
    """Outgoing USB frame: FF 56 53 | seq | len | type | XOR(payload) | 00 | payload."""
    if frame_type in BLOCKED_FRAME_TYPES:
        raise ValueError(f"frame type {frame_type} is blocked")
    return (b"\xff\x56\x53" + struct.pack("<HHH", seq & 0xFFFF, len(payload), frame_type)
            + bytes([xor(payload), 0]) + bytes(payload))
