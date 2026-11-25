# SJRC F22 S2 PRO+: phone ↔ remote ↔ drone protocol

Decoded by observing the USB traffic of the official SJ F PRO app (v6.5.2) and studying how it
builds and parses its messages, including running its native encoder/decoder library in an
emulator to map every bit. Everything below was confirmed on a real drone unless marked otherwise.

## 1. The link

```
 phone (USB device) ──USB──► remote (USB host) ──2.4 GHz control / 5.8 GHz video──► drone
```

- The remote is the USB **host**. The phone works as an **Android Open Accessory** (AOA):
  manufacturer `vison`, model `android.usbaoa`, version `0.1`. After the AOA switch the phone shows
  up as USB `18d1:2d00` with one vendor interface and a bulk IN + bulk OUT endpoint.
- A PC cannot plug in directly (two hosts). Linux can pose as the accessory with a USB gadget (see
  `raspi/`).
- The official app treats this drone as `DRONE_TYPE 18`, protocol **HACK_FLY** ("HY" flight
  controller), camera **HiSi**.

## 2. USB frames

Both directions use the same frame:

```
FF 56 53 | seq LE16 | length LE16 | type LE16 | XOR(payload) | 00 | payload
```

| Type | Direction | Meaning |
| --- | --- | --- |
| 1 | → remote | heartbeat, payload `00 01 … 09`, every 1 s |
| 3 | → remote | video heartbeat `00…09 28 28` every 1 s: ask for video |
| 3 | ← remote | video data (section 7) |
| 4 | → remote | photo |
| 5 | → remote | heartbeat `00…09 25 25`, every 1 s |
| 5 | ← remote | telemetry stream (section 5) |
| 6 | ↔ | drone messages and board commands/status ("UDP" in the official app) |
| 7, 9, 10, 11–16, 17 | → remote | relay status, fixed frequency, video heartbeat, media, RC status |
| **18, 20** | → remote | **power and relay firmware upgrade: never send** |

## 3. Startup (official app, every connection)

1. `FF 53 54 24 02` ("fixedHackFly") every 100 ms, up to 20 times, until the relay answers `FF 53 54 24 xx`.
2. `68 7E 1E "HF-XX-XXXXXX" "000.000.0000" 00×6 XOR` every 200 ms until the flight controller answers
   message **FE** or **9A** (name and firmware, e.g. `HF-SH-F22N7R406.024.0509`).

Not required for the commands below to work, but the official app always does it.

## 4. Messages to the drone (frame type 6)

`68 id len data XOR(id, len, data)`. Bit fields are packed **least significant bit first**.

### 4.1 Control `68 01 0D` (13 data bytes), every 100 ms

| Bits | Field | Notes |
| --- | --- | --- |
| 0–7, 8–15, 16–23, 24–31 | rocker1, rocker2, rocker3, rocker4 | 128 = centre. rocker4 = yaw ("rotate"), rocker2 = throttle ("weightless") in the official app |
| 32–37, 38–43 | leftRightTrim, upDownTrim | 32 = centre |
| 44 | speed | |
| 45 | noHead | headless |
| 46 | roll | |
| 47 | gpsMode | the remote's GPS button; the app's **cancel** = 1 s press |
| 48, 49 | levelCor, magCor | calibrations: do not send |
| 50 | **lockUnlock** | start motors (1 s pulse) |
| 51 | **autoTakeoff** | take off (1 s pulse, motors must be running) |
| 52 | **autoLand** | land (1 s pulse) |
| 53 | **goHome** | return to home (1 s pulse, GPS mode) |
| 54 | stop | unused by the official app |
| 55, 56, 57 | followMe, circleFly, popublicFly | follow, orbit, **start waypoint mission** (1 s pulse) |
| 58, 59 | photo, video | video = 1 while recording |
| 60–61, 62–63 | PTZ_H, PTZ_V | not used for this drone's gimbal (see 4.5) |
| 64 | ctrlPanel | app steering, set together with VisionFollow |
| 65, 66 | display, OpticalFlowOn | |
| 67 | VisionFollow | app steering (image follow) |
| 68 | Dis_valid | |
| 69–80, 81–92, 93–103 | Distance, Box_x, Box_y | vision tracking target |

### 4.2 Extend `68 0B 08` (8 data bytes), every 100 ms

IndoorMode 0, CloseTx 1, CloseTxCnt 2–4, SportMode 5, HYVisualOA 6, HYVisualWarn 7, Reserve0 8–9,
SkyFly 10, FarFly 11, SpiralFly 12, CruiseHorFlag 13, CruiseHorPec 14–16, CruiseVerFlag 17,
CruiseVerPec 18–20, ForbidTakeOff 21, EmergGoHomeAborted 22, EmergGoHomeStart 23,
LowPower1GoHomeAborted 24, LowPower1GoHomestart 25, ArmServerCrtl 26, MotorBeepCrtl 27 (find-drone
beep, 1 s pulse), ArmLightCrtl 28, GSMModeCrtl 29, GSMModuleSta 30, BottomTOFShutdown 31, Reserve2 32–63.

### 4.3 Waypoints `68 04 0C`

| Bytes / bits | Field |
| --- | --- |
| bytes 0–3 | longitude × 10⁷, int32 |
| bytes 4–7 | latitude × 10⁷, int32 |
| bits 64–68 | point number 0–31 |
| bits 69–80 | altitude in decimetres |
| bits 81–87 | speed (official app: 4) |
| bits 88–95 | hover time (official app: 2) |

Send point *n* every 200 ms until the flight controller answers message **0x84** with payload[0] = *n*,
then the next point. After the last one, a 1 s **popublicFly** pulse starts the mission (FlyMode 4).

### 4.4 Other drone messages

- `68 B4 08` cruise trims: CruiseThr (128), CruiseRol, CruisePit, CruiseYaw: only in cruise mode.
- `68 1D 08` gimbal speed / yaw rate limits (settings).

### 4.5 Camera and gimbal (HiSi board)

| Command | Meaning |
| --- | --- |
| `FF 35 19 0A 3D seq` | take photo (drone SD card) |
| `FF 35 19 0A 22` / `… 23` | start / stop recording (drone SD card) |
| `FF 53 54 20 01 pos` | gimbal tilt position; the official buttons step it by 9, 0 = level |
| `FF 53 54 17 01` | gimbal version (read only; good link test) |
| `FF 53 54 23 01`, `FF 53 54 25 01` | gimbal status, relay version |
| `FF 53 54 69 pan tilt` | pan/tilt: exists in the app, never used (no pan axis on this drone) |
| `FF 53 54 11 00`, `FF 53 54 0E 00` | accelerometer / gimbal calibration: **do not send** casually |

## 5. Telemetry (frame type 5)

A stream of `58 id len payload XOR(id, len, payload)` messages.

| Id | Rate | Content |
| --- | --- | --- |
| 8B | 20 Hz | attitude and status (5.1) |
| 9B | 10 Hz | sensors, the sticks the drone receives (5.2) |
| 9C | 10 Hz | battery, mode, warnings (5.3) |
| 9D | 10 Hz | position (5.4) |
| 9E | 10 Hz | gimbal, terrain, return-home countdowns (5.5) |
| 84 | on demand | waypoint acknowledgement (payload[0] = point number) |
| FE, 9A | on demand | flight controller name and firmware (ASCII) |
| BE | occasional | flight controller log text |
| BD, 8A, 8F, A8–AF, BF, C0–C7 | | others (8A drone type, AA ids, AF parameters, C1 battery cells, C4 cruise) |

### 5.1 8B (14 bytes)
bytes 0–5 roll, pitch, yaw int16 (÷100 °); byte 6 sensor flags (INS, baro, mag, GPS, optical flow,
calibrations); bits 56–62 calibration progress; 63–69 battery (÷10 V); 70–71 low battery;
72 over-temperature; 73–74 over-current; **75–76 Armed (0 off, 1 motors on the ground, 2 flying)**;
77–80 FlyMode (0 manual, 1 return home, 2 follow, 3 orbit, 4 waypoints, 9 cruise, 11 comet);
81–83 go-home status; 84 photo; 85 camera; 86 video on; 87 take-off; 88 auto-land; 89 land finished;
90–94 satellites; 95 GPS fix; 96–102 GPS quality; 103–104 GPS mode; 105–106 RC fast mode;
107–108 gimbal adjust; 109 take-off forbidden; 110 strong wind.

### 5.2 9B (36 bytes)
accelerometer, gyro, magnetometer int16×3 at bytes 0, 6, 12; baro int32 at 18; IMU temperature 22;
baro temperature 23; **sticks the drone receives** roll 24, pitch 25, throttle 26, yaw 27 (0–254,
127 centre); bytes 28–35 "motor" values (0 in our tests).

### 5.3 9C (12 bytes)
bits 0–9 magnetic interference; 10–21 battery (÷100 V); 22–31 GPS quality; **32 IndoorMode
(1 = attitude/altitude-hold)**; 33–39 battery %; 40 long-distance; 41–53 distance; 54–60 **remote
battery %**; 61 IMU accel flag; 62 yaw rotate; 63 compass error; 64 height limit; 65 distance limit;
66 WiFi test; 67 obstacle warning; 68–72 obstacle distance; 73 compass needs calibration; **74 sport
mode**; 75 obstacle sensor; 76–77 zoom; 78–81 auto mode; 82–85 return-home step; 86 cruise H;
87 cruise V; 89–90 sky-fly; 91–92 far-fly; 93–94 spiral; 95 battery legal.

### 5.4 9D (14 bytes)
longitude, latitude int32 ×10⁷ (0 = no fix); altitude int16 (÷10 m); distance from home int16 (m);
horizontal speed uint8 (÷10 m/s); vertical speed int8 (÷10 m/s).

### 5.5 9E (12 bytes)
gimbal angle command int16; terrain distance uint16 (dm, 65535 = none); obstacle distance; emergency
return-home countdown (255 = none); yaw rate setting uint16; gimbal velocity (128 idle); low-battery
return-home countdown (255 = none).

## 6. Board status (frame type 6, `FF 53 54 cmd …`)

| cmd | Meaning |
| --- | --- |
| 24 xx | answer to the startup handshake |
| 61 pos xx | gimbal tilt position |
| 52 xx | remote link signal |
| 08 xx | another signal level |
| 17 …, 23 … | gimbal version, gimbal status |
| 39, 40, 41, 50 | remote id / state (meaning not decoded) |

## 7. Video (frame type 3)

Frames of H.265 (H.264 on some models), 1280×720, ~30 fps, keyframe every ~1 s, ~430 KB/s.
USB chunks are at most 10240 bytes; each video frame starts with a 44-byte header:

| Offset | Field |
| --- | --- |
| 0 | `00 00 01 A0` (`A1` = keyframe) |
| 10 | frame counter LE16 |
| 16 | payload length LE32 |
| 24, 26 | height, width LE16 |
| 36 | timestamp in µs LE32 |
| 44 | Annex-B NAL units (keyframes start with VPS/SPS/PPS) |

## 8. Behaviour

- Buttons are 1 s pulses of a control bit. **Take off is ignored while the motors are stopped**: start
  them (lockUnlock), wait until Armed = 1, then autoTakeoff. **Land** from flight stops the motors.
- On the ground the official app has no "stop motors": the remote does it (throttle down 2 s).
- **GPS mode refuses to start the motors without a GPS fix.** Indoors switch to attitude mode: hold the
  remote's GPS button 3 s. GPS/attitude and sport mode are the remote's buttons only (the app's
  commands had no effect).
- Remote stick combos: start/stop = both sticks down and outward; both up and outward = compass
  calibration; throttle down-left + right stick up-right = factory reset.
- App steering: the official image-follow mode turns the drone through rocker4 while ctrlPanel = 1
  **and** VisionFollow = 1. With only ctrlPanel nothing moved. The apps set both while their
  virtual sticks are used, limited to 70% deflection so they can never form a stick combo.

## 9. Test results (props off, attitude mode, indoors)

| Feature | Result |
| --- | --- |
| Telemetry, gimbal version query | works |
| Start motors | works (Armed 0 → 1) |
| Take off | works after start motors (Armed 1 → 2, take-off flag) |
| Land | works (Armed 2 → 0) |
| Waypoint upload | works (0x84 acknowledgements) |
| Gimbal (`FF 53 54 20 01 pos`), virtual sticks with both flags, photos/videos | confirmed by the user in the Android app |
| Sport, GPS/attitude from the app | no effect (remote buttons only) |
| Return home, missions in flight | protocol from the official app; need GPS outdoors |

## 10. Safety notes

- Remove the propellers for any test that can start the motors.
- Never send frame types 18 or 20, or calibration commands you do not mean to run.
- On the tested Xiaomi phone (MediaTek), closing or killing an app while its USB accessory read is
  pending crashed the phone (kernel panic in the accessory driver). The apps here never close the link
  while reading; unplug the remote before closing or updating the app.
