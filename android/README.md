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
| `.../Protocol.java` | Message formats: control, extend, waypoint, flight limits, camera, handshake |
| `.../Telemetry.java` | Decodes the drone's messages (attitude, GPS, battery, modes, warnings, limits) and lists why it may refuse to start |
| `.../BatteryEstimator.java` | Steady battery percentage: compensates the voltage sag in flight (plain Java, testable on a PC) |
| `.../Flight.java` | 10 Hz control loop, commands with safety checks, virtual sticks, waypoint upload, flight limits |
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
- **Plan**: tap the map to add waypoints (up to 16), drag to move them, set altitude, speed and hover
  time, then *Upload & fly mission* (the drone must be flying in GPS mode). The line below shows the
  whole trip with the way home, its time, and how far out it goes against the drone's distance limit.
- **Flight mode** (tap the status pill): GPS / attitude, normal / sport, Home, Stop, Land.
- **Settings**: sticks (mode 1/2, sensitivity, what the drone receives), video, map style,
  UGV/PC link, flight limits and no-fly block, find-my-drone beep, event log.

### Battery percentage

The drone's own percentage is its live voltage on a straight line (10.4 V = 0 %, 12.4 V = 100 %), so it
drops at take-off and moves with the throttle. The app shows the resting value at take-off, adds back
the sag it measures 5 to 15 s after take-off, smooths it over 20 s and never lets it rise in flight
(at most 6 % per minute down, faster when the drone itself reports low voltage). The drone's low-battery
levels still drive the warnings and its own return home and landing.

### When the motors do not start

The drone gives no reason itself. The app reads its status flags and says what to fix: compass disturbed
(typical after landing next to metal, cars or reinforced concrete: carry it a few metres away), no GPS
fix, low battery, a calibration running, or a no-fly block left by the official app. The same list
appears in the take-off confirmation.

The compass check is inside the drone: no app can switch it off, and in GPS mode it protects against
fly-aways (a wrong compass makes the GPS position hold circle and drift away). Landing anywhere is fine;
only the next start is refused. Where moving the drone is not possible, switch to attitude mode (hold
the remote's GPS button 3 s): it holds no position, so steer it yourself; return home does not work in
this mode. Climb away from the metal, then hold the GPS button again for GPS mode once the app no longer
shows the compass warning. If it circles in GPS mode, go back to attitude mode and fly it by hand.
Not yet confirmed: whether this drone also starts in attitude mode with a disturbed compass.

### Flight limits

Settings › Flight limits sends max altitude (10–5000 m), max distance (20–10000 m) and return-home
altitude (20–120 m) with the official app's message, shows the values the drone confirms, and re-sends
them after every connection (the official app does the same with its own values). The official app
allows 120 m (500 m in a hidden mode) and 6000 m; if the drone keeps a lower maximum, its answer shows it.

### Long missions

- **Distance:** every waypoint must be at least 30 m inside the drone's max distance, measured from home
  (the take-off point). The app uses the limit the drone confirmed, else the one set in Settings, else
  1500 m. The drone flies home by itself when it reaches that limit (manual), so raise it first.
- **Radio:** the remote reaches about 6 km in open country (manual). When the drone loses the remote it
  flies home on its own, which ends the mission.
- **Battery:** the planner adds the way home to the trip time and warns when it is longer than the rough
  flight time left (about 25 min on a full battery, keeping 20 % for the drone's own low-battery return).
- **Speed:** the official app always flies missions at 4 m/s; other speeds (1–10 m/s here) are not yet
  tested on the drone. At 4 m/s a 5 km leg takes 21 minutes.
- **Height:** at most 409 m: the waypoint message carries the altitude in 12 bits of decimetres.
- The official app only allows 16 waypoints within 300 m of the pilot, so long missions are new ground
  for this drone: try a short one first, then go further step by step.

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
| Take off, land, sticks in real flights | verified by the pilot with v1.2 |
| Steady battery %, reasons when the motors do not start | v1.3; logic tested on recorded and simulated data |
| Flight limits, clearing a no-fly block | v1.3; messages match the official native code; to verify on the drone |
| Missions up to 10 km, faster than 4 m/s | v1.4; the waypoint message has no distance limit; to verify on the drone, step by step |
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
