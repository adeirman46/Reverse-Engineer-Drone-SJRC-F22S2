package com.ugvuav.gcs;

import android.os.SystemClock;

import java.nio.charset.StandardCharsets;
import java.util.Locale;

/**
 * Latest drone state, decoded from the remote's frames. Written by the USB reader thread only;
 * read from any thread (single fields are volatile; a snapshot may mix two updates, which is fine
 * for display).
 *
 * Frame type 5 carries a stream of messages: 58 id len payload xor(id, len, payload).
 * Layouts were mapped bit by bit from the official app's native parsers (NOTES.md).
 */
final class Telemetry {
    private final Link link;

    Telemetry(Link link) { this.link = link; }

    volatile long lastAny, last8B, last9D;  // SystemClock.uptimeMillis()
    final long[] counts = new long[256];

    // 8B: attitude and status (~20 Hz)
    volatile double roll, pitch, yaw, batV8B;
    volatile int sensors, calibProgress, lowBat, tempOver, currOver, armed, flyMode, goHomeStatus,
            videoOn, takeoff, autoLand, landFinish, gpsNum, gpsFine, gpsQuality, modeGps, rcFast,
            forbidTakeoff, galeWarn;
    // 9B: sensors and the remote's sticks as the drone receives them
    volatile int stRoll = 127, stPitch = 127, stThr = 127, stYaw = 127, imuTemp;
    // 9C: battery, mode and warnings
    volatile int magInterference, batPct = -1, indoor = -1, remotePct = -1, magErr, magNeedCal,
            heightLimit, distLimit, oaWarn, oaDis, sport, autoMode, rtlStep, imuAccFlag, gpsQuality9C;
    volatile double batV;
    // 9D: position
    volatile double lat, lon, alt, hs, vs;
    volatile int dist;
    volatile boolean hasFix;
    // 9E: gimbal, terrain distance, automatic return-home countdowns
    volatile int gimbal, terrainDm = 65535, emergRth = 255, lowPowerRth = 255;
    // other messages
    volatile String fcVersion = "";
    volatile String fcLog = "";
    volatile int relayReply = -1;
    volatile boolean gotVersion;
    volatile int waypointAck = -1;
    volatile int gimbalPos = -1, rcSignal;
    volatile long gimbalAt;

    // derived
    volatile double homeLat, homeLon;
    volatile boolean hasHome;
    volatile long armedSince;   // uptime ms, 0 when not armed
    volatile long lastFlightMs;
    volatile double maxAlt, maxDist;

    private final byte[] stream = new byte[16384];
    private int streamLen;

    boolean fresh() {
        return last8B != 0 && SystemClock.uptimeMillis() - last8B < 1500;
    }

    long age() {
        return lastAny == 0 ? -1 : SystemClock.uptimeMillis() - lastAny;
    }

    void reset() {
        streamLen = 0;
        relayReply = -1;
        gotVersion = false;
    }

    /** Payload of one frame from the remote. */
    void onFrame(int type, byte[] b, int off, int len) {
        if (type == 5) {
            feedStream(b, off, len);
        } else if (type == 6 && len >= 4 && b[off] == (byte) 0xFF && b[off + 1] == 0x53 && b[off + 2] == 0x54) {
            int cmd = b[off + 3] & 0xFF;
            if (cmd == 0x24 && relayReply < 0) {
                relayReply = len > 4 ? b[off + 4] & 0xFF : 0;
                link.log("Relay answered the startup handshake.");
            } else if (cmd == 0x61 && len > 4) {
                gimbalPos = b[off + 4] & 0xFF;  // gimbal tilt position (official NOTIFY_TYPE_PTZ_ANGLE)
                gimbalAt = SystemClock.uptimeMillis();
            } else if (cmd == 0x52 && len > 4) {
                rcSignal = b[off + 4];          // remote link signal (official NOTIFY_WIFI_SIGNAL_RC)
            }
        }
    }

    private void feedStream(byte[] b, int off, int len) {
        if (streamLen + len > stream.length) streamLen = 0;  // resync
        System.arraycopy(b, off, stream, streamLen, len);
        streamLen += len;
        int j = 0;
        while (j + 4 <= streamLen) {
            if (stream[j] != 0x58) { j++; continue; }
            int id = stream[j + 1] & 0xFF;
            int n = stream[j + 2] & 0xFF;
            if (j + 4 + n > streamLen) break;
            byte x = 0;
            for (int i = j + 1; i < j + 3 + n; i++) x ^= stream[i];
            if (x == stream[j + 3 + n]) {
                byte[] p = new byte[n];
                System.arraycopy(stream, j + 3, p, 0, n);
                decode(id, p);
                j += 4 + n;
            } else {
                j++;  // not a real message start
            }
        }
        System.arraycopy(stream, j, stream, 0, streamLen - j);
        streamLen -= j;
    }

    private void decode(int id, byte[] p) {
        long now = SystemClock.uptimeMillis();
        counts[id]++;
        lastAny = now;
        switch (id) {
            case 0x8B: if (p.length >= 14) decode8B(p, now); break;
            case 0x9B: if (p.length >= 36) decode9B(p); break;
            case 0x9C: if (p.length >= 12) decode9C(p); break;
            case 0x9D: if (p.length >= 14) decode9D(p, now); break;
            case 0x9E: if (p.length >= 10) decode9E(p); break;
            case 0x84: if (p.length >= 1) waypointAck = p[0] & 0xFF; break;
            case 0xFE:
            case 0x9A:
                if (!gotVersion) {
                    gotVersion = true;
                    fcVersion = ascii(p, Math.min(p.length, 24));
                    link.log("Flight controller: " + fcVersion);
                }
                break;
            case 0xBE: fcLog = ascii(p, p.length); break;
            default: break;
        }
    }

    private void decode8B(byte[] p, long now) {
        roll = Protocol.s16(p, 0) / 100.0;
        pitch = Protocol.s16(p, 2) / 100.0;
        yaw = Protocol.s16(p, 4) / 100.0;
        sensors = p[6] & 0xFF;  // InsInitOk BaroInitOk MagInitOk GpsInitOk FlowInitOk InsCalib MagXYCalib MagZCalib
        calibProgress = Protocol.bits(p, 56, 7);
        batV8B = Protocol.bits(p, 63, 7) / 10.0;
        lowBat = Protocol.bits(p, 70, 2);
        tempOver = Protocol.bits(p, 72, 1);
        currOver = Protocol.bits(p, 73, 2);
        int a = Protocol.bits(p, 75, 2);
        flyMode = Protocol.bits(p, 77, 4);
        goHomeStatus = Protocol.bits(p, 81, 3);
        videoOn = Protocol.bits(p, 86, 1);
        takeoff = Protocol.bits(p, 87, 1);
        autoLand = Protocol.bits(p, 88, 1);
        landFinish = Protocol.bits(p, 89, 1);
        gpsNum = Protocol.bits(p, 90, 5);
        gpsFine = Protocol.bits(p, 95, 1);
        gpsQuality = Protocol.bits(p, 96, 7);
        modeGps = Protocol.bits(p, 103, 2);
        rcFast = Protocol.bits(p, 105, 2);
        forbidTakeoff = Protocol.bits(p, 109, 1);
        galeWarn = Protocol.bits(p, 110, 1);
        if (a != 0 && armed == 0) {
            armedSince = now;
            maxAlt = 0;
            maxDist = 0;
            if (hasFix && gpsFine == 1) setHome();
            else hasHome = false;
            link.log("Motors started.");
        } else if (a == 0 && armed != 0) {
            lastFlightMs = now - armedSince;
            armedSince = 0;
            link.log("Motors stopped.");
        }
        if (a != 0 && !hasHome && hasFix && gpsFine == 1) setHome();
        armed = a;
        last8B = now;
    }

    private void setHome() {
        homeLat = lat;
        homeLon = lon;
        hasHome = true;
    }

    private void decode9B(byte[] p) {
        imuTemp = p[22];
        stRoll = p[24] & 0xFF;
        stPitch = p[25] & 0xFF;
        stThr = p[26] & 0xFF;
        stYaw = p[27] & 0xFF;
    }

    private void decode9C(byte[] p) {
        magInterference = Protocol.bits(p, 0, 10);
        batV = Protocol.bits(p, 10, 12) / 100.0;
        gpsQuality9C = Protocol.bits(p, 22, 10);
        indoor = Protocol.bits(p, 32, 1);
        batPct = Protocol.bits(p, 33, 7);
        remotePct = Protocol.bits(p, 54, 7);
        imuAccFlag = Protocol.bits(p, 61, 1);
        magErr = Protocol.bits(p, 63, 1);
        heightLimit = Protocol.bits(p, 64, 1);
        distLimit = Protocol.bits(p, 65, 1);
        oaWarn = Protocol.bits(p, 67, 1);
        oaDis = Protocol.bits(p, 68, 5);
        magNeedCal = Protocol.bits(p, 73, 1);
        sport = Protocol.bits(p, 74, 1);
        autoMode = Protocol.bits(p, 78, 4);
        rtlStep = Protocol.bits(p, 82, 4);
    }

    private void decode9D(byte[] p, long now) {
        int lo = Protocol.s32(p, 0);
        int la = Protocol.s32(p, 4);
        alt = Protocol.s16(p, 8) / 10.0;
        dist = Protocol.s16(p, 10);
        hs = (p[12] & 0xFF) / 10.0;
        vs = p[13] / 10.0;
        hasFix = lo != 0 || la != 0;
        if (hasFix) {
            lon = lo / 1e7;
            lat = la / 1e7;
        }
        if (armedSince != 0) {
            maxAlt = Math.max(maxAlt, alt);
            maxDist = Math.max(maxDist, dist);
        }
        last9D = now;
    }

    private void decode9E(byte[] p) {
        gimbal = Protocol.s16(p, 0);
        terrainDm = Protocol.u16(p, 2);
        emergRth = p[5] & 0xFF;
        lowPowerRth = p[9] & 0xFF;
    }

    private static String ascii(byte[] p, int n) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < n; i++) {
            int c = p[i] & 0xFF;
            if (c >= 32 && c < 127) sb.append((char) c);
        }
        return sb.toString().trim();
    }

    /** JSON for the web interface. */
    void appendJson(StringBuilder sb) {
        long now = SystemClock.uptimeMillis();
        sb.append("\"d\":{");
        num(sb, "fresh", fresh() ? 1 : 0);
        num(sb, "age", age());
        num(sb, "roll", roll);
        num(sb, "pitch", pitch);
        num(sb, "yaw", yaw);
        num(sb, "armed", armed);
        num(sb, "flyMode", flyMode);
        num(sb, "goHome", goHomeStatus);
        num(sb, "modeGps", modeGps);
        num(sb, "indoor", indoor);
        num(sb, "gpsNum", gpsNum);
        num(sb, "gpsFine", gpsFine);
        num(sb, "hasFix", hasFix ? 1 : 0);
        num(sb, "lat", lat);
        num(sb, "lon", lon);
        num(sb, "alt", alt);
        num(sb, "dist", dist);
        num(sb, "hs", hs);
        num(sb, "vs", vs);
        num(sb, "batV", batV > 0 ? batV : batV8B);
        num(sb, "batPct", batPct);
        num(sb, "lowBat", lowBat);
        num(sb, "remotePct", remotePct);
        num(sb, "magInt", magInterference);
        num(sb, "magErr", magErr);
        num(sb, "magCal", magNeedCal);
        num(sb, "oaWarn", oaWarn);
        num(sb, "oaDis", oaDis);
        num(sb, "hLimit", heightLimit);
        num(sb, "dLimit", distLimit);
        num(sb, "sport", sport);
        num(sb, "autoMode", autoMode);
        num(sb, "rtl", rtlStep);
        num(sb, "takeoff", takeoff);
        num(sb, "autoLand", autoLand);
        num(sb, "landFinish", landFinish);
        num(sb, "forbid", forbidTakeoff);
        num(sb, "gale", galeWarn);
        num(sb, "tempOver", tempOver);
        num(sb, "currOver", currOver);
        num(sb, "emergRth", emergRth);
        num(sb, "lowRth", lowPowerRth);
        num(sb, "gimbal", gimbal);
        num(sb, "gimbalPos", gimbalPos);
        num(sb, "rcSig", rcSignal);
        num(sb, "terrain", terrainDm);
        num(sb, "sensors", sensors);
        num(sb, "calib", calibProgress);
        num(sb, "stRoll", stRoll);
        num(sb, "stPitch", stPitch);
        num(sb, "stThr", stThr);
        num(sb, "stYaw", stYaw);
        num(sb, "flightS", armedSince != 0 ? (now - armedSince) / 1000.0 : lastFlightMs / 1000.0);
        num(sb, "maxAlt", maxAlt);
        num(sb, "maxDist", maxDist);
        str(sb, "fc", fcVersion);
        str(sb, "fcLog", fcLog);
        sb.setLength(sb.length() - 1);
        sb.append("},\"home\":");
        if (hasHome) {
            sb.append(String.format(Locale.US, "{\"lat\":%.7f,\"lon\":%.7f}", homeLat, homeLon));
        } else {
            sb.append("null");
        }
        sb.append(',');
    }

    static void num(StringBuilder sb, String k, double v) {
        sb.append('"').append(k).append("\":");
        if (Double.isNaN(v) || Double.isInfinite(v)) sb.append("null");
        else if (v == Math.rint(v) && Math.abs(v) < 1e15) sb.append((long) v);
        else sb.append(String.format(Locale.US, "%.7f", v));
        sb.append(',');
    }

    static void str(StringBuilder sb, String k, String v) {
        sb.append('"').append(k).append("\":\"");
        if (v != null) {
            for (int i = 0; i < v.length(); i++) {
                char c = v.charAt(i);
                if (c == '"' || c == '\\') sb.append('\\').append(c);
                else if (c < 32) sb.append(' ');
                else sb.append(c);
            }
        }
        sb.append("\",");
    }

    static String text(byte[] b) {
        return new String(b, StandardCharsets.US_ASCII);
    }
}
