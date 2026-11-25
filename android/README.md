# Pindad UGV - UAV

Android ground-control app for the SJRC F22 S2 PRO+ drone (and, later, the UGV). The phone
plugs into the drone's remote by USB; the app shows the live video, telemetry and map, and
sends flight commands in the same format as the official SJ F PRO app.

## Open and build in Android Studio

1. **File > Open** and pick this folder (`pindad-ugv-uav`). Android Studio downloads Gradle 8.9
   and the Android Gradle Plugin 8.7.3 on the first sync. If it asks about the Gradle wrapper,
   accept its offer to create one.
2. Install **Android SDK Platform 34** when prompted.
3. **Build > Build App Bundle(s) / APK(s) > Build APK(s)**, or press Run with the phone connected.

The project has no library dependencies: plain Android SDK (Java 8 language level), minSdk 26,
targetSdk 34. The release build is signed with the debug key so it installs directly; use your own
key before giving the app to others.

## Project layout

| Path | What it is |
| --- | --- |
| `app/src/main/java/com/ugvuav/gcs/Link.java` | USB link to the remote (Android Open Accessory), heartbeats, frame parser, PC/UGV bridge on TCP 9000 |
| `.../Protocol.java` | Message formats: control, extend, waypoint, camera, handshake |
| `.../Telemetry.java` | Decodes the drone's messages (attitude, GPS, battery, modes, warnings) |
| `.../Flight.java` | 10 Hz control loop, commands with safety checks, virtual sticks, waypoint upload |
| `.../VideoDecoder.java` | Hardware H.264 / H.265 decoding of the live video |
| `.../MainActivity.java` | Full-screen cockpit: video surface + web interface, JavaScript bridge, phone GPS |
| `app/src/main/assets/ui/` | The interface (HTML/CSS/JS, Leaflet map). Open `index.html` in a desktop browser for a demo |
| `tools/ui_screenshots.sh` | Renders every screen of the interface into `screenshots/phone` and `screenshots/tablet` |

## Using it

- Open the app first, then plug in the remote, then tap **Connect** (for safety the app only opens a
  remote that was plugged in while it was running).
- **Unplug the remote before closing the app** or installing an update. On this phone, closing the
  link while a USB read is pending crashes the phone (kernel panic).
- Disable the official SJ F PRO app while using this one, so the two never share the remote.

### Screens

- **Fly** (default): live video, flight display (horizon, heading), telemetry, two virtual sticks.
  Tap the small window at the bottom to swap video and map. On the map, tap a point to *Fly here*.
- **Plan**: tap the map to add waypoints, drag to move them, set altitude, speed and hover time,
  then *Upload & fly mission* (the drone must be flying in GPS mode).
- **Flight mode** (tap the status pill): GPS / attitude, normal / sport, Home, Stop, Land.
- **Settings**: sticks (mode 1/2, sensitivity, what the drone receives), video, map style,
  UGV/PC link, find-my-drone beep, event log.

### Virtual sticks

Mode 2 by default, like a normal remote: left = climb/descend and turn, right = forward/back and
sideways. Both spring back to the centre, where the drone holds its altitude. Deflection is
limited to 70% so the sticks can never form the full-deflection combinations (motor start,
compass calibration, factory reset).

## Status of each feature (tested on the real drone, propellers off)

See `../docs/PROTOCOL.md` for the protocol and the test log. Commands are sent the way the official app
sends them; features marked *to verify* use the protocol but were not yet confirmed on the drone.

| Feature | Status |
| --- | --- |
| USB link, telemetry, live video (H.265) | verified |
| Start motors, take off (motors first), land | verified (props off) |
| Waypoint upload | verified: the drone confirms every point |
| Gimbal tilt, virtual sticks, photos/videos to the Gallery | verified in v1.2 |
| Return home, Stop, Fly here, missions in flight | protocol from the official app; test outdoors with GPS |
| GPS / attitude, normal / sport | shown in the app; switched with the remote's buttons |

## First real flight (outdoors, by the pilot)

1. Open area, at least 10 GPS satellites, drone in GPS mode, remote in your hands.
2. Take off from the app; let it hover at about 1.5 m for 30 s. Your thumbs stay on the remote.
3. Small virtual-stick inputs, then *Land* from the app.
4. Then *Return home* from about 20 m away, then a two-waypoint mission close by.
5. If anything looks wrong, take over with the remote or tap **Stop**.

## Building without Android Studio (ARM64 Linux)

`../tools/build_apk.sh` builds the same sources with javac, d8, a small manifest converter and a
minimal resources.arsc writer, because aapt2 has no ARM64 Linux build.
