# Ground station for the NVIDIA DGX Spark (Python + Qt)

The Android app, ported to Python for the **DGX Spark** (any Linux computer works). It shows the same
cockpit (the interface is shared with the Android app) and relays the drone to the outside world:
live video as **RTSP** (passed through, no re-encoding) and telemetry plus basic commands as
**MAVLink** for QGroundControl, over the DGX Spark's Ethernet, for example to a Starlink router.

```
 SJRC remote ──USB──► bridge ──Ethernet / Wi-Fi──► DGX Spark ──Ethernet──► Starlink router ──► viewers, QGroundControl
                  (poses as the phone)  TCP 9000     cockpit window          rtsp://DGX:8554/drone + MAVLink UDP
```

## Why a bridge between the remote and the DGX Spark

The remote is a USB **host** that expects an Android phone. The DGX Spark's USB ports are host-only as
well: all its USB controllers are standard xHCI host controllers and the firmware exposes no USB
device controller (`/sys/class/udc` does not exist). Two hosts cannot talk, so something has to play
the phone:

| Bridge | Status |
| --- | --- |
| **USB bridge board** (Raspberry Pi 4/5 with Ethernet, see [`../bridge`](../bridge/)) | network side tested; USB side not yet tested with the remote |
| **The Android app** on a phone (PC control on in its Settings) | works |

## Install (DGX OS / Ubuntu 24.04)

The tested combination on the DGX Spark:

```bash
sudo apt install python3-gi gir1.2-gst-rtsp-server-1.0 gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-libav libsnappy1v5 libminizip1t64
python3 -m venv --system-site-packages ~/venv-gcs          # system site packages: GStreamer bindings
~/venv-gcs/bin/pip install PySide6==6.8.1 pymavlink        # Qt window, MAVLink (optional)
```

(`python3-pyqt5 python3-pyqt5.qtwebengine` from apt work instead of PySide6.)

## Run

From this folder (`dgx-spark`):

```bash
~/venv-gcs/bin/python -m pindad_gcs                               # finds the bridge on the network
~/venv-gcs/bin/python -m pindad_gcs --link tcp://192.168.1.23:9000   # bridge or phone at a known address
~/venv-gcs/bin/python -m pindad_gcs --headless \
        --mavlink udpout:GROUND_STATION_IP:14550                  # relay only: RTSP + MAVLink, no window
~/venv-gcs/bin/python -m pindad_gcs --link replay:../captures/some-capture.bin   # test with a recording
```

| Option | Default | |
| --- | --- | --- |
| `--link` | `tcp://auto` | `tcp://auto` (search), `tcp://HOST[:9000]`, `gadget`, `replay:FILE` |
| `--rtsp-port`, `--rtsp-path` | 8554, `/drone` | RTSP address: `rtsp://DGX_IP:8554/drone` |
| `--no-rtsp` | | no RTSP server |
| `--mavlink URL` | off | e.g. `udpout:IP:14550` (send to a ground station) or `udpin:0.0.0.0:14550` |
| `--media-dir` | `~/Pindad UGV-UAV` | photos (`Pictures/`) and recordings (`Videos/`, MP4) |
| `--headless`, `--fullscreen` | | no window / full-screen window |
| `--limits ALT,DIST,RTH` | | drone flight limits in metres, e.g. `500,2000,40`: saved in `~/.config/pindad-gcs/settings.json` and sent after every connection (also set from the window's Settings › Flight limits) |

The link tries every network interface, so it also works when the DGX Spark has a cable and Wi-Fi on
the same network but only one of them reaches the bridge.

## Watching the video

- **RTSP:** `rtsp://DGX_IP:8554/drone` in VLC, `ffplay -fflags nobuffer`, or QGroundControl
  (Application Settings › Video › RTSP Video Stream). The drone's H.265 720p30 stream (about
  3.5 Mbit/s) is passed through unchanged.
- **The cockpit window** decodes the video with GStreamer (`gstreamer1.0-libav`, or the GPU decoder
  of the `nvcodec` plugin when it is available).
- **QGroundControl:** start with `--mavlink udpout:QGC_IP:14550`: the drone appears on the map with
  attitude, altitude, speed, battery and GPS; Takeoff, Land, RTL and Pause work.

## Starlink

- Connect the DGX Spark's Ethernet to the Starlink router (Gen 2/3 routers need the Starlink Ethernet adapter).
- Starlink uses **CGNAT**: nothing on the internet can open a connection *to* the DGX Spark. To watch
  from anywhere, install **Tailscale** (or ZeroTier) on the DGX Spark and on the viewing computer and
  use the DGX Spark's Tailscale address: `rtsp://100.x.y.z:8554/drone`.
- Upload needed: about 3.5 Mbit/s per RTSP viewer, plus a few kbit/s for MAVLink.

## Start at boot

```bash
sudo cp systemd/pindad-gcs.service /etc/systemd/system/    # edit user, paths and --mavlink first
sudo systemctl enable --now pindad-gcs
journalctl -u pindad-gcs -f
```

## What is tested (on a DGX Spark)

| Part | Status |
| --- | --- |
| Message encoders | identical to the encoders verified against the official app (2000 random messages) |
| Bridge ⇄ ground station | recorded drone data through `pindad_gcs.bridge`: H.265 1280x720 at 30 fps and telemetry down; heartbeats, handshake, 10 Hz control and a photo command up |
| RTSP | recorded drone video served and received by an RTSP viewer, keyframes and parameter sets intact |
| Cockpit window (PySide6) | runs with recorded drone data (telemetry, warnings, map, sticks) |
| Phone app as the bridge | same link the PC test tools use on the real drone |
| Battery estimate, take-off reasons, flight limits, no-fly clear, long missions | same logic as the Android app 1.4; offline tests with recorded and simulated data |
| USB bridge board with the remote | **not yet tested** |
| On-screen video decoding, MAVLink with QGroundControl | not yet tested |

## Files

| File | |
| --- | --- |
| `pindad_gcs/app.py` | entry point and options |
| `pindad_gcs/link.py` | frame parser, backends (tcp with auto-discovery, gadget, replay) |
| `pindad_gcs/protocol.py`, `telemetry.py`, `flight.py`, `battery.py` | ports of the Android app's `Protocol`, `Telemetry`, `Flight`, `BatteryEstimator` |
| `pindad_gcs/video.py` | frame assembly, RTSP server, preview, photo and MP4 recording (GStreamer) |
| `pindad_gcs/mavlink_bridge.py` | MAVLink telemetry and commands (pymavlink) |
| `pindad_gcs/ui.py`, `qt_shim.js` | Qt window running the shared interface from `android/app/src/main/assets/ui` |
| `pindad_gcs/bridge.py`, `gadget.py` | the USB bridge program (runs on the bridge board, see `../bridge`) |
| `systemd/pindad-gcs.service` | start at boot |
| `tests/test_offline.py` | offline tests (limits message, battery estimate, flight logic and mission distance checks with made-up telemetry): `python3 tests/test_offline.py` |
