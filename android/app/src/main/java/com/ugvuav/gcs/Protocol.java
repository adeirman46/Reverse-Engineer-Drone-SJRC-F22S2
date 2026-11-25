package com.ugvuav.gcs;

import java.nio.charset.StandardCharsets;

/**
 * Message formats of the SJRC F22 S2 PRO+ flight controller (the "HACK_FLY"/HY protocol of the
 * official SJ F PRO app; see NOTES.md). Bit fields are packed least significant bit first.
 * Every message here travels to the drone inside a USB frame of type 6.
 */
final class Protocol {
    private Protocol() {}

    // ---------- control message 68 01 0D, 13 data bytes, sent every 100 ms ----------
    static final int ROCKER1 = 0, ROCKER2 = 1, ROCKER3 = 2, ROCKER4 = 3, LR_TRIM = 4, UD_TRIM = 5,
            SPEED = 6, NO_HEAD = 7, ROLL = 8, CANCEL = 9 /* "gpsMode" bit: cancels auto modes */,
            LEVEL_COR = 10, MAG_COR = 11, LOCK_UNLOCK = 12, AUTO_TAKEOFF = 13, AUTO_LAND = 14,
            GO_HOME = 15, STOP = 16, FOLLOW_ME = 17, CIRCLE_FLY = 18, POINT_FLY = 19, PHOTO = 20,
            VIDEO = 21, PTZ_H = 22, PTZ_V = 23, CTRL_PANEL = 24, DISPLAY = 25, OPTICAL_FLOW_ON = 26,
            VISION_FOLLOW = 27, DIS_VALID = 28, DISTANCE = 29, BOX_X = 30, BOX_Y = 31;
    static final int[] CONTROL_WIDTHS = {8, 8, 8, 8, 6, 6, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
            1, 1, 2, 2, 1, 1, 1, 1, 1, 12, 12, 11};

    // ---------- extend message 68 0B 08, 8 data bytes, sent every 100 ms ----------
    static final int X_INDOOR = 0, X_CLOSE_TX = 1, X_CLOSE_TX_CNT = 2, X_SPORT = 3, X_VISUAL_OA = 4,
            X_VISUAL_WARN = 5, X_RESERVE0 = 6, X_SKY_FLY = 7, X_FAR_FLY = 8, X_SPIRAL_FLY = 9,
            X_CRUISE_HOR = 10, X_CRUISE_HOR_PEC = 11, X_CRUISE_VER = 12, X_CRUISE_VER_PEC = 13,
            X_FORBID_TAKEOFF = 14, X_EMERG_RTH_ABORT = 15, X_EMERG_RTH_START = 16,
            X_LOWPOWER_RTH_ABORT = 17, X_LOWPOWER_RTH_START = 18, X_ARM_SERVO = 19,
            X_MOTOR_BEEP = 20, X_ARM_LIGHT = 21, X_GSM_MODE = 22, X_GSM_STA = 23,
            X_BOTTOM_TOF_OFF = 24, X_RESERVE2 = 25;
    static final int[] EXTEND_WIDTHS = {1, 1, 3, 1, 1, 1, 2, 1, 1, 1, 1, 3, 1, 3, 1, 1, 1, 1, 1, 1,
            1, 1, 1, 1, 1, 32};

    static int[] controlDefaults() {
        int[] v = new int[CONTROL_WIDTHS.length];
        v[ROCKER1] = v[ROCKER2] = v[ROCKER3] = v[ROCKER4] = 128;
        v[LR_TRIM] = v[UD_TRIM] = 32;
        return v;
    }

    static byte[] pack(int[] widths, int[] values, int nbytes) {
        byte[] out = new byte[nbytes];
        int pos = 0;
        for (int i = 0; i < widths.length; i++) {
            long v = values[i] & ((1L << widths[i]) - 1);
            for (int b = 0; b < widths[i]; b++, pos++) {
                if ((v >> b & 1) != 0) out[pos >> 3] |= (byte) (1 << (pos & 7));
            }
        }
        return out;
    }

    /** 68 id len data xor, where xor covers id, len and data. */
    static byte[] message(int id, byte[] data) {
        byte[] m = new byte[data.length + 4];
        m[0] = 0x68;
        m[1] = (byte) id;
        m[2] = (byte) data.length;
        System.arraycopy(data, 0, m, 3, data.length);
        byte x = 0;
        for (int i = 1; i < m.length - 1; i++) x ^= m[i];
        m[m.length - 1] = x;
        return m;
    }

    static byte[] control(int[] values) {
        return message(0x01, pack(CONTROL_WIDTHS, values, 13));
    }

    static byte[] extend(int[] values) {
        return message(0x0B, pack(EXTEND_WIDTHS, values, 8));
    }

    /**
     * Waypoint 68 04 0C (WaypointManager + LGDataUtils.convertFlyLine): longitude and latitude
     * x1e7 as int32, then point number (5 bits), altitude in dm (12), speed (7), hover time (8).
     * The drone answers each point with message 0x84 whose first byte is the point number.
     */
    static byte[] waypoint(int index, double lat, double lon, double altM, int speed, int hover) {
        byte[] d = new byte[12];
        putInt(d, 0, (int) Math.round(lon * 1e7));
        putInt(d, 4, (int) Math.round(lat * 1e7));
        long alt = Math.max(0, Math.min(4095, Math.round(altM * 10)));
        long v = (index & 0x1FL) | alt << 5 | (speed & 0x7FL) << 17 | (hover & 0xFFL) << 24;
        putInt(d, 8, (int) v);
        return message(0x04, d);
    }

    /** Startup handshake step 1 ("fixedHackFly"); the relay answers FF 53 54 24 xx. */
    static final byte[] FIX_HACK_FLY = {(byte) 0xFF, 0x53, 0x54, 0x24, 0x02};

    /** Startup handshake step 2: version request; the flight controller answers 9A or FE. */
    static byte[] versionRequest() {
        byte[] d = new byte[30];
        byte[] name = "HF-XX-XXXXXX".getBytes(StandardCharsets.US_ASCII);
        byte[] ver = "000.000.0000".getBytes(StandardCharsets.US_ASCII);
        System.arraycopy(name, 0, d, 0, 12);
        System.arraycopy(ver, 0, d, 12, 12);
        return message(0x7E, d);
    }

    /** HiSi camera commands (HisiCommandHelper): FF 35 19 0A cmd [args]. */
    static byte[] camera(int cmd, int... args) {
        byte[] m = new byte[5 + args.length];
        m[0] = (byte) 0xFF;
        m[1] = 0x35;
        m[2] = 0x19;
        m[3] = 0x0A;
        m[4] = (byte) cmd;
        for (int i = 0; i < args.length; i++) m[5 + i] = (byte) args[i];
        return m;
    }

    static final int CAM_RECORD_START = 34, CAM_RECORD_STOP = 35, CAM_PHOTO = 61;

    /** Gimbal tilt position (SJBaseApplication.setPTZData, HiSi branch): FF 53 54 20 01 pos.
     *  The official buttons step it by 9; 0 = level. The board reports it back as FF 53 54 61 pos xx. */
    static byte[] gimbalPosition(int pos) {
        return new byte[]{(byte) 0xFF, 0x53, 0x54, 0x20, 0x01, (byte) pos};
    }

    static final int GIMBAL_STEP = 9, GIMBAL_MAX = 90;

    // ---------- helpers ----------
    static void putInt(byte[] b, int off, int v) {
        b[off] = (byte) v;
        b[off + 1] = (byte) (v >> 8);
        b[off + 2] = (byte) (v >> 16);
        b[off + 3] = (byte) (v >> 24);
    }

    static int s16(byte[] b, int off) {
        return (short) ((b[off] & 0xFF) | (b[off + 1] & 0xFF) << 8);
    }

    static int u16(byte[] b, int off) {
        return (b[off] & 0xFF) | (b[off + 1] & 0xFF) << 8;
    }

    static int s32(byte[] b, int off) {
        return (b[off] & 0xFF) | (b[off + 1] & 0xFF) << 8 | (b[off + 2] & 0xFF) << 16 | b[off + 3] << 24;
    }

    /** n bits starting at bit lo (LSB-first across bytes). */
    static int bits(byte[] b, int lo, int n) {
        int v = 0;
        for (int i = 0; i < n; i++) {
            int k = lo + i;
            if ((b[k >> 3] >> (k & 7) & 1) != 0) v |= 1 << i;
        }
        return v;
    }
}
