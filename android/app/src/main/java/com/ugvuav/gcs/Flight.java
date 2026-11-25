package com.ugvuav.gcs;

import android.os.SystemClock;

import java.util.Locale;

/**
 * Flight commands, sent the way the official app sends them: after a startup handshake, a control
 * message and an extend message every 100 ms with the sticks centred; a button press is a flag
 * held at 1 for one second ("pulse"). The remote keeps flying the drone with its own sticks.
 *
 * Every command is checked against live telemetry before it is sent.
 */
final class Flight {
    private final Link link;
    private final Telemetry tel;

    private final int[] control = Protocol.controlDefaults();
    private final int[] extend = new int[Protocol.EXTEND_WIDTHS.length];
    private final long[] holdUntil = new long[Protocol.CONTROL_WIDTHS.length];
    private final int[] holdValue = new int[Protocol.CONTROL_WIDTHS.length];
    private final long[] xHoldUntil = new long[Protocol.EXTEND_WIDTHS.length];
    private Thread thread;

    volatile String handshake = "idle";

    // waypoint mission
    volatile String missionState = "idle";  // idle, uploading, running, failed, cancelled
    volatile String missionMessage = "";
    volatile int missionTotal, missionAcked;
    private double[][] missionPoints;
    private double missionAlt;
    private int missionSpeed, missionHover, missionStep;
    private long stepStart, lastSend;
    private boolean sawPointFlight;

    // camera
    volatile boolean recording;
    volatile long recordStart;
    private int photoSeq = 1;

    // Virtual sticks (Mode 2 from the interface): -1..1, up/right positive.
    // Axis -> rocker: yaw = rocker4 (the official app's "rotate"), throttle = rocker2 (its "weightless"),
    // roll = rocker1, pitch = rocker3. Roll/pitch order still to be confirmed with scripts/stick_test.py.
    static final int[] STICK_ROCKER = {Protocol.ROCKER4, Protocol.ROCKER2, Protocol.ROCKER1, Protocol.ROCKER3};
    /** Never deflect further than this: full-deflection stick combinations start the motors,
     *  calibrate the compass or factory-reset the drone. */
    static final float STICK_LIMIT = 0.7f;
    private final float[] sticks = new float[4];  // yaw, throttle, roll, pitch
    private volatile long sticksAt;
    volatile boolean sticksEnabled;
    volatile boolean stickPriority;  // also set ctrlPanel while the sticks are used (experimental)

    Flight(Link link, Telemetry tel) {
        this.link = link;
        this.tel = tel;
    }

    synchronized void start() {
        if (thread != null && thread.isAlive()) return;
        thread = new Thread(this::run, "flight");
        thread.setDaemon(true);
        thread.start();
    }

    private void run() {
        handshake();
        long next = SystemClock.uptimeMillis();
        while (link.isConnected()) {
            tick();
            next += 100;
            long wait = next - SystemClock.uptimeMillis();
            if (wait > 0) Link.sleep(wait);
            else next = SystemClock.uptimeMillis();
        }
        handshake = "idle";
        if ("uploading".equals(missionState)) failMission("The remote was disconnected.");
        recording = false;
        link.media.stopRecording();  // keep what was recorded so far
    }

    /** The official app's startup sequence for this drone type (WelcomeActivity, "FixHack"). */
    private void handshake() {
        handshake = "relay";
        for (int i = 0; i < 20 && link.isConnected() && tel.relayReply < 0; i++) {
            link.sendDrone(Protocol.FIX_HACK_FLY);
            Link.sleep(100);
        }
        handshake = "version";
        byte[] req = Protocol.versionRequest();
        for (int i = 0; i < 25 && link.isConnected() && !tel.gotVersion; i++) {
            link.sendDrone(req);
            Link.sleep(200);
        }
        handshake = tel.gotVersion ? "ok" : "no-reply";
        if (!tel.gotVersion && link.isConnected()) {
            link.log("The flight controller did not answer the startup handshake (drone off?).");
        }
    }

    private void tick() {
        long now = SystemClock.uptimeMillis();
        missionTick(now);
        takeoffTick(now);
        if (link.allowPcCommands) return;  // the PC is sending flight messages itself
        int[] c;
        int[] x;
        synchronized (this) {
            c = control.clone();
            x = extend.clone();
            for (int i = 0; i < c.length; i++) if (now < holdUntil[i]) c[i] = holdValue[i];
            for (int i = 0; i < x.length; i++) if (now < xHoldUntil[i]) x[i] = 1;
            if (recording) c[Protocol.VIDEO] = 1;
            // Sticks count only while the interface keeps sending them (it repeats every 200 ms);
            // if it stops (app frozen, finger lifted) they fall back to centre within 400 ms.
            // While the app steers, the official app sets BOTH ctrlPanel and VisionFollow (its image
            // follow turns the drone through rocker4 this way); with only ctrlPanel nothing moved.
            if (sticksEnabled && now - sticksAt < 400) {
                for (int i = 0; i < 4; i++) c[STICK_ROCKER[i]] = rocker(sticks[i]);
                c[Protocol.CTRL_PANEL] = 1;
                c[Protocol.VISION_FOLLOW] = 1;
            }
            if (now < yawHoldUntil) {  // gimbal left/right: turn the drone
                c[Protocol.ROCKER4] = yawHoldValue;
                c[Protocol.CTRL_PANEL] = 1;
                c[Protocol.VISION_FOLLOW] = 1;
            }
        }
        link.sendDrone(Protocol.control(c));
        link.sendDrone(Protocol.extend(x));
    }

    /** From the interface: each -1..1, up/right positive. */
    synchronized void setSticks(float yaw, float throttle, float roll, float pitch) {
        sticks[0] = clampStick(yaw);
        sticks[1] = clampStick(throttle);
        sticks[2] = clampStick(roll);
        sticks[3] = clampStick(pitch);
        sticksAt = SystemClock.uptimeMillis();
    }

    private static float clampStick(float v) {
        if (Float.isNaN(v)) return 0;
        return Math.max(-STICK_LIMIT, Math.min(STICK_LIMIT, v));
    }

    private static int rocker(float v) {
        return Math.max(1, Math.min(255, Math.round(128 + v * 127)));
    }

    private long takeoffAt, takeoffDeadline;  // two-step take-off in progress (0 = none)
    private int gimbalTarget;                 // last gimbal position sent
    private long gimbalSentAt;
    private int yawHoldValue = 128;
    private long yawHoldUntil;

    /** After "takeoff" from standstill: once the motors run, wait 1.5 s, then send take-off. */
    private synchronized void takeoffTick(long now) {
        if (takeoffDeadline == 0) return;
        if (tel.armed == 2) {
            takeoffDeadline = 0;
        } else if (now > takeoffDeadline) {
            takeoffDeadline = 0;
            link.log("Take off: the motors did not start (GPS mode needs a GPS fix).");
        } else if (tel.armed == 1) {
            if (takeoffAt == 0) {
                takeoffAt = now + 1500;
            } else if (now >= takeoffAt) {
                hold(Protocol.AUTO_TAKEOFF, 1, 1000);
                takeoffDeadline = 0;
                link.log("Take off: sent.");
            }
        }
    }

    private synchronized void hold(int field, int value, long ms) {
        holdValue[field] = value;
        holdUntil[field] = SystemClock.uptimeMillis() + ms;
    }

    private synchronized void release(int field) {
        holdUntil[field] = 0;
    }

    private synchronized void holdExtend(int field, long ms) {
        xHoldUntil[field] = SystemClock.uptimeMillis() + ms;
    }

    /** Runs a named command; returns null on success or a message for the pilot. */
    String command(String name) {
        if (!link.isConnected()) return "The remote is not connected.";
        boolean flightCmd = !name.startsWith("photo") && !name.startsWith("record");
        if (flightCmd && link.allowPcCommands) return "PC control is on (Settings). Turn it off to fly from the app.";
        if (flightCmd && !name.startsWith("gimbal") && !"cancel".equals(name) && !tel.fresh()) {
            return "No live data from the drone. Is it switched on?";
        }
        int armed = tel.armed;
        switch (name) {
            case "unlock":
                if (armed != 0) return "The motors are already running.";
                hold(Protocol.LOCK_UNLOCK, 1, 1000);
                return ok("Start motors");
            case "takeoff":
                if (armed == 2) return "Already flying.";
                if (armed == 0) {
                    // Tested 2026-11-25: the drone ignores take-off with the motors stopped; start them
                    // first (like the official app's slider), then take off once they run (see tick()).
                    hold(Protocol.LOCK_UNLOCK, 1, 1000);
                    synchronized (this) {
                        takeoffAt = 0;
                        takeoffDeadline = SystemClock.uptimeMillis() + 7000;
                    }
                    return ok("Take off: starting the motors first");
                }
                hold(Protocol.AUTO_TAKEOFF, 1, 1000);
                return ok("Take off");
            case "land":
                if (armed == 0) return "The motors are not running.";
                hold(Protocol.AUTO_LAND, 1, 1000);
                return ok("Land");
            case "rth":
                if (armed != 2) return "Return to home works in flight only.";
                if (tel.indoor == 1) return "Return to home needs GPS mode (the drone is in altitude mode).";
                if (tel.gpsFine == 0) return "Return to home needs a GPS fix.";
                synchronized (this) { control[Protocol.ROCKER4] = 128; }
                hold(Protocol.GO_HOME, 1, 1000);
                return ok("Return to home");
            case "cancel":
                hold(Protocol.CANCEL, 1, 1000);
                if ("uploading".equals(missionState) || "running".equals(missionState)) {
                    missionState = "cancelled";
                    missionMessage = "Mission cancelled.";
                }
                return ok("Cancel automatic flight");
            case "modeToggle":
                // The remote's GPS button held for 3 s switches GPS <-> attitude (altitude hold) mode.
                // The official app sends this button bit as its "cancel" (1 s press); held 3.5 s here.
                hold(Protocol.CANCEL, 1, 3500);
                return ok("Switch GPS / attitude mode (3.5 s button press)");
            case "sport":
                holdExtend(Protocol.X_SPORT, 1000);  // official app: 1 s pulse toggles sport mode (9C SportMode)
                return ok("Sport mode toggle");
            case "gimbalUp":
            case "gimbalDown": {
                // Official HiSi gimbal command: an absolute position, stepped by 9 per press.
                long now = SystemClock.uptimeMillis();
                int base = now - gimbalSentAt < 1500 ? gimbalTarget
                        : (tel.gimbalPos >= 0 ? tel.gimbalPos : gimbalTarget);
                int step = "gimbalUp".equals(name) ? -Protocol.GIMBAL_STEP : Protocol.GIMBAL_STEP;
                gimbalTarget = Math.max(0, Math.min(Protocol.GIMBAL_MAX, base + step));
                gimbalSentAt = now;
                link.sendDrone(Protocol.gimbalPosition(gimbalTarget));
                return null;
            }
            case "gimbalStop":
                return null;
            case "yawLeft":
            case "yawRight":
                if (armed != 2) return "Turning works in flight only.";
                synchronized (this) {
                    yawHoldValue = "yawLeft".equals(name) ? 128 - 45 : 128 + 45;
                    yawHoldUntil = SystemClock.uptimeMillis() + 450;  // the interface repeats while held
                }
                return null;
            case "beep":
                if (armed != 0) return "Only with the motors stopped.";
                holdExtend(Protocol.X_MOTOR_BEEP, 1000);
                return ok("Find drone (beep)");
            case "photo":
                link.sendDrone(Protocol.camera(Protocol.CAM_PHOTO, photoSeq & 0xFF));
                photoSeq = photoSeq >= 255 ? 1 : photoSeq + 1;
                return null;
            case "record":
                // Both: the drone records full quality to its SD card, the phone saves the live
                // stream into the gallery (Movies/Pindad UGV-UAV).
                if (recording) {
                    link.sendDrone(Protocol.camera(Protocol.CAM_RECORD_STOP));
                    recording = false;
                    link.media.stopRecording();
                    link.log("Recording stopped.");
                } else {
                    link.sendDrone(Protocol.camera(Protocol.CAM_RECORD_START));
                    recording = true;
                    recordStart = SystemClock.uptimeMillis();
                    link.media.startRecording();
                    link.log("Recording started: drone SD card and phone gallery.");
                }
                return null;
            default:
                return "Unknown command: " + name;
        }
    }

    private String ok(String what) {
        link.log(what + ": sent.");
        return null;
    }

    // ---------------- waypoint mission ----------------

    /** points[i] = {lat, lon}. Returns null when the upload started, else the reason it did not. */
    synchronized String startMission(double[][] points, double altM, int speed, int hover) {
        if (!link.isConnected()) return "The remote is not connected.";
        if (link.allowPcCommands) return "PC control is on (Settings).";
        if (!tel.fresh()) return "No live data from the drone.";
        if (tel.armed != 2) return "Take off first: waypoints are flown from the air.";
        if (tel.indoor == 1) return "Switch the drone to GPS mode first.";
        if (tel.gpsFine == 0 || !tel.hasFix) return "Waiting for a GPS fix.";
        if (points == null || points.length < 1) return "Add at least one waypoint.";
        if (points.length > 32) return "At most 32 waypoints.";
        if (altM < 5 || altM > 120) return "Altitude must be 5 to 120 m.";
        if (speed < 1 || speed > 10) return "Speed must be 1 to 10 m/s.";
        if (hover < 0 || hover > 60) return "Hover time must be 0 to 60 s.";
        if ("uploading".equals(missionState)) return "A mission is already uploading.";
        for (int i = 0; i < points.length; i++) {
            double d = distance(tel.lat, tel.lon, points[i][0], points[i][1]);
            if (Double.isNaN(d) || d > 1500) {
                return String.format(Locale.US, "Waypoint %d is %.0f m away (limit 1500 m).", i + 1, d);
            }
        }
        missionPoints = points;
        missionAlt = altM;
        missionSpeed = speed;
        missionHover = hover;
        missionStep = 0;
        missionTotal = points.length;
        missionAcked = 0;
        missionMessage = "Uploading waypoints...";
        tel.waypointAck = -1;
        stepStart = SystemClock.uptimeMillis();
        lastSend = 0;
        sawPointFlight = false;
        missionState = "uploading";
        link.log(String.format(Locale.US, "Mission: uploading %d waypoints at %.0f m, %d m/s.",
                points.length, altM, speed));
        return null;
    }

    private void missionTick(long now) {
        String state = missionState;
        if ("running".equals(state)) {
            if (tel.flyMode == 4) sawPointFlight = true;
            else if (sawPointFlight) {
                missionState = "idle";
                missionMessage = "Mission finished.";
                link.log("Mission finished (the drone left waypoint mode).");
            } else if (now - stepStart > 5000) {
                missionMessage = "Mission sent. Waiting for the drone to start it...";
            }
            return;
        }
        if (!"uploading".equals(state)) return;
        synchronized (this) {
            int ack = tel.waypointAck;
            if (ack >= missionStep && ack < missionTotal) {
                missionStep = ack + 1;
                missionAcked = missionStep;
                stepStart = now;
                lastSend = 0;
            }
            if (missionStep >= missionTotal) {
                missionState = "running";
                missionMessage = "All waypoints confirmed. Starting the mission.";
                stepStart = now;
                hold(Protocol.POINT_FLY, 1, 1000);
                link.log("Mission: all waypoints confirmed, start sent.");
                return;
            }
            if (now - stepStart > 6000) {
                failMission("The drone did not confirm waypoint " + (missionStep + 1) + ".");
                return;
            }
            if (now - lastSend >= 200) {
                double[] p = missionPoints[missionStep];
                link.sendDrone(Protocol.waypoint(missionStep, p[0], p[1], missionAlt, missionSpeed, missionHover));
                lastSend = now;
            }
        }
    }

    private void failMission(String why) {
        missionState = "failed";
        missionMessage = why;
        link.log("Mission: " + why);
    }

    synchronized void clearMission() {
        if (!"uploading".equals(missionState)) {
            missionState = "idle";
            missionMessage = "";
        }
    }

    void appendJson(StringBuilder sb) {
        sb.append("\"mission\":{");
        Telemetry.str(sb, "state", missionState);
        Telemetry.str(sb, "msg", missionMessage);
        Telemetry.num(sb, "total", missionTotal);
        Telemetry.num(sb, "acked", missionAcked);
        sb.setLength(sb.length() - 1);
        sb.append("},\"cam\":{");
        Telemetry.num(sb, "rec", recording ? 1 : 0);
        Telemetry.num(sb, "recS", recording ? (SystemClock.uptimeMillis() - recordStart) / 1000.0 : 0);
        sb.setLength(sb.length() - 1);
        sb.append("},");
        Telemetry.str(sb, "handshake", handshake);
    }

    static double distance(double lat1, double lon1, double lat2, double lon2) {
        double r = 6371000;
        double p1 = Math.toRadians(lat1), p2 = Math.toRadians(lat2);
        double dp = p2 - p1, dl = Math.toRadians(lon2 - lon1);
        double a = Math.sin(dp / 2) * Math.sin(dp / 2) + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
        return 2 * r * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }
}
