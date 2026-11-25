# Raspberry Pi 5 ground station (Python + Qt)

The Android app, ported to Python for a Raspberry Pi 5 (or any Linux computer). It shows the same
cockpit (the interface is shared with the Android app), and it can run without a screen as a
**video and telemetry relay**: the drone's live video goes out as **RTSP** over the Pi's Ethernet
(for example to a Starlink router), telemetry and basic commands as **MAVLink** for QGroundControl.

```
 SJRC remote ──USB──► Raspberry Pi 5 ──Ethernet──► Starlink router ──► internet (VPN) ──► viewers / QGroundControl
                       (poses as the phone)          rtsp://PI:8554/drone   +  MAVLink UDP 14550
```

## Connecting the remote

The remote is a USB **host** that expects an Android phone in "Android Open Accessory" mode. A
computer is normally a host too, so it cannot talk to the remote directly. Two ways:

| | How | Status |
| --- | --- | --- |
| **A. Pi as the phone** (`--link gadget`) | The Pi 5's USB-C port works as a USB *device*: `gadget/setup_gadget.sh` presents the Pi as an Android accessory (USB ID 18d1:2d00) and `pindad_gcs/gadget.py` answers the remote like the phone did. | Written to the USB and AOA specifications, **not yet tested with the remote** |
| **B. Through a phone** (`--link tcp://PHONE_IP:9000`) | The remote stays on a phone running the Pindad UGV - UAV app; the Pi reads it over the network. For flight commands turn on **PC control** in the app's Settings. | Works (same link the PC tools use) |

### Option A: wiring and setup

1. Add to `/boot/firmware/config.txt` (under `[all]`) and reboot:
   ```
   dtoverlay=dwc2,dr_mode=peripheral
   ```
   The USB-C port then becomes a USB device port (it is also the power input).
2. **Power:** the remote cannot power a Pi 5. Power the Pi from a 5 V / 5 A supply through a
   **USB-C power + data splitter made for Pi gadget mode**, or through the GPIO 5 V pins. Do not
   connect the remote's 5 V line to the Pi's 5 V supply (use a splitter or a data-only adapter), so
   the two supplies never push against each other.
3. Cable: the remote's USB port to the Pi's USB-C data side.
4. `sudo raspi/gadget/setup_gadget.sh $USER` (after every boot; the systemd unit below does it).
5. `python3 -m pindad_gcs --link gadget`: the app binds the gadget and waits for the remote.
   The log shows `Remote asked for accessory mode: {...}` and `Connected to the remote`.

If it does not connect, run with the log visible and send it in an issue: the remote's requests
are logged.

## Install (Raspberry Pi OS Bookworm, 64-bit)

```bash
sudo apt install python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-rtsp-server-1.0 \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav \
    python3-pyqt5 python3-pyqt5.qtwebengine
pip install pymavlink        # optional: MAVLink for QGroundControl
```
PySide6 works too (`pip install PySide6`; on some systems also `sudo apt install libsnappy1v5 libminizip1`).

## Run

From the `raspi` folder:

```bash
python3 -m pindad_gcs --link gadget --fullscreen                 # remote on the Pi, with the cockpit window
python3 -m pindad_gcs --link tcp://192.168.1.23:9000             # remote on a phone (option B)
python3 -m pindad_gcs --link gadget --headless \
        --mavlink udpout:GROUND_STATION_IP:14550                 # relay only: RTSP + MAVLink, no screen
python3 -m pindad_gcs --link replay:../captures/some-capture.bin # test with a recorded capture
```

| Option | Default | |
| --- | --- | --- |
| `--rtsp-port`, `--rtsp-path` | 8554, `/drone` | RTSP address: `rtsp://PI_IP:8554/drone` |
| `--no-rtsp` | | no RTSP server |
| `--mavlink URL` | off | e.g. `udpout:IP:14550` (send to a ground station) or `udpin:0.0.0.0:14550` |
| `--media-dir` | `~/Pindad UGV-UAV` | photos (`Pictures/`) and recordings (`Videos/`, MP4) |
| `--headless` | | no window |
| `--fullscreen` | | full-screen window (touch screens) |

## Watching the video

- **RTSP:** `rtsp://PI_IP:8554/drone`: VLC, `ffplay -fflags nobuffer rtsp://…`, or QGroundControl
  (Application Settings › Video › RTSP Video Stream). The drone's H.265 720p30 stream (about
  3.5 Mbit/s) is passed through without re-encoding, so the Pi's CPU stays free and there is no quality loss.
- **QGroundControl:** start with `--mavlink udpout:QGC_IP:14550`. QGroundControl shows the drone on
  its map with attitude, altitude, speed, battery and GPS, and its Takeoff / Land / RTL / Pause
  buttons work (see `pindad_gcs/mavlink_bridge.py`).

## Starlink

- Connect the Pi to the Starlink router's LAN port (Gen 2/3 routers need the Starlink Ethernet adapter).
- Starlink uses **CGNAT**: nothing on the internet can open a connection *to* the Pi. To watch from
  anywhere, install a VPN overlay such as **Tailscale** or **ZeroTier** on the Pi and on the viewing
  computer, then use the Pi's VPN address: `rtsp://100.x.y.z:8554/drone`, MAVLink to the viewer's VPN address.
- Upload needed: about 3.5 Mbit/s per RTSP viewer, plus a few kbit/s for MAVLink.

## Start at boot (headless relay)

```bash
sudo cp systemd/pindad-gcs.service /etc/systemd/system/
sudo nano /etc/systemd/system/pindad-gcs.service   # set User=, the path and the --mavlink address
sudo systemctl enable --now pindad-gcs
journalctl -u pindad-gcs -f
```

## What is tested

| Part | Status |
| --- | --- |
| Message encoders (control, extend, waypoint, camera, gimbal) | identical to the encoders verified against the official app (2000 random messages) |
| Telemetry, video assembly, RTSP | recorded drone video replayed into the app and watched over RTSP: H.265 1280x720, ~31 fps, keyframes and parameter sets intact |
| Cockpit window (PySide6) | runs with recorded drone data (telemetry, warnings, map, sticks) |
| Option B (phone link) | same link the PC test tools use on the real drone |
| Option A (USB gadget) | **not yet tested with the remote** |
| On-screen video decoding, MAVLink with QGroundControl | not yet tested on a Pi |

## Files

| File | |
| --- | --- |
| `pindad_gcs/protocol.py` | message formats (port of `Protocol.java`) |
| `pindad_gcs/telemetry.py` | decodes the drone's messages (port of `Telemetry.java`) |
| `pindad_gcs/flight.py` | 10 Hz control loop, commands with safety checks, take-off sequence, waypoint upload (port of `Flight.java`) |
| `pindad_gcs/link.py` | frame parser, backends: gadget, tcp, replay |
| `pindad_gcs/gadget.py` | USB gadget: the Pi as an Android accessory (FunctionFS) |
| `pindad_gcs/video.py` | frame assembly, RTSP server, preview, photo and MP4 recording (GStreamer) |
| `pindad_gcs/mavlink_bridge.py` | MAVLink telemetry and commands (pymavlink) |
| `pindad_gcs/ui.py`, `qt_shim.js` | Qt window running the shared interface from `android/app/src/main/assets/ui` |
| `gadget/setup_gadget.sh` | creates the USB gadget (run as root) |
| `systemd/pindad-gcs.service` | start at boot |
