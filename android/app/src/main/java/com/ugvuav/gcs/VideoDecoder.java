package com.ugvuav.gcs;

import android.media.MediaCodec;
import android.media.MediaFormat;
import android.os.Build;
import android.os.SystemClock;
import android.view.Surface;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * Live video from the remote (USB frame type 3), decoded in hardware with MediaCodec.
 *
 * The remote wraps each video frame in a 44-byte header: 00 00 01 A0 (A1 = keyframe), frame
 * counter (LE16 at 10), payload length (LE32 at 16), height (LE16 at 24), width (LE16 at 26),
 * timestamp in microseconds (LE32 at 36). The payload is Annex-B NAL units. The codec, H.264 or
 * H.265, is detected from the parameter sets in each keyframe, so either stream works and a
 * switch between them is followed automatically.
 */
final class VideoDecoder {
    private static final int HEADER = 44;
    static final String AVC = "video/avc", HEVC = "video/hevc";

    private final Link link;
    private final byte[] buf = new byte[4 << 20];
    private int len;
    private final ArrayBlockingQueue<Frame> queue = new ArrayBlockingQueue<>(12);
    private boolean needKey = true;

    private volatile Surface surface;
    private volatile Surface activeSurface;
    private final Object surfaceLock = new Object();

    volatile String codecName = "";
    volatile int width, height, fps;
    volatile long framesIn, framesShown, dropped, lastFrameAt;
    private int fpsCount;
    private long fpsStart;

    private static final class Frame {
        final byte[] data;
        final boolean key;
        final long pts;
        final int w, h;

        Frame(byte[] data, boolean key, long pts, int w, int h) {
            this.data = data;
            this.key = key;
            this.pts = pts;
            this.w = w;
            this.h = h;
        }
    }

    VideoDecoder(Link link) {
        this.link = link;
        Link.startThread("video-decoder", this::decodeLoop);
    }

    boolean active() {
        return lastFrameAt != 0 && SystemClock.uptimeMillis() - lastFrameAt < 1500;
    }

    // ---------------- frame assembly (USB reader thread) ----------------

    void feed(byte[] b, int off, int n) {
        if (len + n > buf.length) len = 0;
        System.arraycopy(b, off, buf, len, n);
        len += n;
        int p = 0;
        while (true) {
            int m = findMarker(p);
            if (m < 0) { p = Math.max(p, len - 3); break; }
            if (len - m < HEADER) { p = m; break; }
            int plen = Protocol.s32(buf, m + 16);
            if (plen <= 0 || plen > buf.length - HEADER) { p = m + 4; continue; }
            if (len - m < HEADER + plen) { p = m; break; }
            boolean key = buf[m + 3] == (byte) 0xA1;
            int h = Protocol.u16(buf, m + 24), w = Protocol.u16(buf, m + 26);
            long pts = Protocol.s32(buf, m + 36) & 0xFFFFFFFFL;
            byte[] f = Arrays.copyOfRange(buf, m + HEADER, m + HEADER + plen);
            framesIn++;
            lastFrameAt = SystemClock.uptimeMillis();
            link.media.onFrame(f, key, pts, w, h);
            enqueue(new Frame(f, key, pts, w, h));
            p = m + HEADER + plen;
        }
        if (p > 0) {
            System.arraycopy(buf, p, buf, 0, len - p);
            len -= p;
        }
    }

    private int findMarker(int from) {
        for (int i = from; i + 3 < len; i++) {
            if (buf[i] == 0 && buf[i + 1] == 0 && buf[i + 2] == 1
                    && (buf[i + 3] == (byte) 0xA0 || buf[i + 3] == (byte) 0xA1)) return i;
        }
        return -1;
    }

    private void enqueue(Frame f) {
        if (needKey && !f.key) { dropped++; return; }
        needKey = false;
        if (!queue.offer(f)) {
            // The decoder fell behind: drop everything and restart at the next keyframe.
            queue.clear();
            dropped++;
            if (f.key) queue.offer(f);
            else needKey = true;
        }
    }

    // ---------------- decoding (own thread) ----------------

    /** Called from the UI thread; with null it waits until the decoder released the surface. */
    void setSurface(Surface s) {
        surface = s;
        if (s == null) {
            synchronized (surfaceLock) {
                long end = SystemClock.uptimeMillis() + 600;
                while (activeSurface != null && SystemClock.uptimeMillis() < end) {
                    try { surfaceLock.wait(50); } catch (InterruptedException e) { break; }
                }
            }
        }
    }

    private void decodeLoop() {
        MediaCodec codec = null;
        Surface current = null;
        String mime = null;
        int cw = 0, ch = 0;
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        while (true) {
            Frame f;
            try {
                f = queue.poll(100, TimeUnit.MILLISECONDS);
            } catch (InterruptedException e) {
                return;
            }
            Surface s = surface;
            if (s != current) {
                codec = release(codec);
                synchronized (surfaceLock) {
                    current = s;
                    activeSurface = s;
                    surfaceLock.notifyAll();
                }
            }
            if (codec != null) codec = drain(codec, info);
            if (f == null || current == null) continue;
            if (f.key) {
                String m = detect(f.data);
                if (m != null && codec != null && (!m.equals(mime) || f.w != cw || f.h != ch)) {
                    link.log("Video format changed, restarting the decoder.");
                    codec = release(codec);
                }
                if (codec == null && m != null) {
                    codec = configure(m, f, current);
                    mime = m;
                    cw = f.w;
                    ch = f.h;
                }
            }
            if (codec == null) { dropped++; continue; }
            try {
                int idx = codec.dequeueInputBuffer(30000);
                if (idx >= 0) {
                    ByteBuffer in = codec.getInputBuffer(idx);
                    in.clear();
                    if (f.data.length <= in.capacity()) {
                        in.put(f.data);
                        codec.queueInputBuffer(idx, 0, f.data.length, f.pts,
                                f.key ? MediaCodec.BUFFER_FLAG_KEY_FRAME : 0);
                    } else {
                        codec.queueInputBuffer(idx, 0, 0, f.pts, 0);
                        dropped++;
                    }
                } else {
                    dropped++;
                }
                codec = drain(codec, info);
            } catch (Exception e) {
                link.log("Video decoder error: " + e.getMessage());
                codec = release(codec);
            }
        }
    }

    private MediaCodec drain(MediaCodec c, MediaCodec.BufferInfo info) {
        try {
            while (true) {
                int o = c.dequeueOutputBuffer(info, 0);
                if (o >= 0) {
                    c.releaseOutputBuffer(o, true);
                    framesShown++;
                    countFps();
                } else if (o == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    MediaFormat fmt = c.getOutputFormat();
                    if (fmt.containsKey(MediaFormat.KEY_WIDTH)) width = fmt.getInteger(MediaFormat.KEY_WIDTH);
                    if (fmt.containsKey(MediaFormat.KEY_HEIGHT)) height = fmt.getInteger(MediaFormat.KEY_HEIGHT);
                } else if (o != MediaCodec.INFO_OUTPUT_BUFFERS_CHANGED) {
                    return c;
                }
            }
        } catch (Exception e) {
            link.log("Video output error: " + e.getMessage());
            return release(c);
        }
    }

    private void countFps() {
        long now = SystemClock.uptimeMillis();
        if (fpsStart == 0) fpsStart = now;
        fpsCount++;
        if (now - fpsStart >= 1000) {
            fps = (int) (fpsCount * 1000L / (now - fpsStart));
            fpsCount = 0;
            fpsStart = now;
        }
    }

    private MediaCodec configure(String mime, Frame key, Surface s) {
        int w = key.w > 0 ? key.w : 1280, h = key.h > 0 ? key.h : 720;
        for (int attempt = 0; attempt < 2; attempt++) {
            MediaCodec c = null;
            try {
                MediaFormat fmt = MediaFormat.createVideoFormat(mime, w, h);
                setCsd(fmt, mime, key.data);
                fmt.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 1 << 20);
                if (attempt == 0) {
                    fmt.setInteger(MediaFormat.KEY_PRIORITY, 0);  // real time
                    if (Build.VERSION.SDK_INT >= 30) fmt.setInteger(MediaFormat.KEY_LOW_LATENCY, 1);
                }
                c = MediaCodec.createDecoderByType(mime);
                c.configure(fmt, s, null, 0);
                c.start();
                codecName = HEVC.equals(mime) ? "H.265" : "H.264";
                width = w;
                height = h;
                link.log("Video: " + codecName + " " + w + "x" + h + " (" + c.getName() + ").");
                return c;
            } catch (Exception e) {
                if (c != null) c.release();
                if (attempt == 1) link.log("Video decoder could not start: " + e.getMessage());
            }
        }
        return null;
    }

    private MediaCodec release(MediaCodec c) {
        if (c != null) {
            try { c.stop(); } catch (Exception ignored) {}
            try { c.release(); } catch (Exception ignored) {}
        }
        return null;
    }

    /** Codec configuration from a keyframe: H.265 csd-0 = VPS+SPS+PPS; H.264 csd-0 = SPS, csd-1 = PPS. */
    static void setCsd(MediaFormat fmt, String mime, byte[] keyframe) {
        List<byte[]> nals = nalUnits(keyframe);
        if (HEVC.equals(mime)) {
            ByteArrayOutputStream csd = new ByteArrayOutputStream();
            for (byte[] n : nals) {
                int t = (n[0] >> 1) & 0x3F;
                if (t >= 32 && t <= 34) writeNal(csd, n);
            }
            if (csd.size() > 0) fmt.setByteBuffer("csd-0", ByteBuffer.wrap(csd.toByteArray()));
        } else {
            for (byte[] n : nals) {
                int t = n[0] & 0x1F;
                if (t == 7 && !fmt.containsKey("csd-0")) fmt.setByteBuffer("csd-0", ByteBuffer.wrap(withStartCode(n)));
                if (t == 8 && !fmt.containsKey("csd-1")) fmt.setByteBuffer("csd-1", ByteBuffer.wrap(withStartCode(n)));
            }
        }
    }

    /** "video/hevc" or "video/avc" from the parameter sets in a keyframe, or null. */
    static String detect(byte[] d) {
        for (byte[] n : nalUnits(d)) {
            int h = n[0] & 0xFF;
            int hevc = (h >> 1) & 0x3F;
            if ((h & 0x81) == 0 && hevc >= 32 && hevc <= 34 && n.length > 1 && (n[1] & 0xFF) == 0x01) return HEVC;
            if ((h & 0x80) == 0 && ((h & 0x1F) == 7 || (h & 0x1F) == 8)) return AVC;
        }
        return null;
    }

    /** NAL units (without start codes) of an Annex-B buffer. */
    static List<byte[]> nalUnits(byte[] d) {
        List<byte[]> out = new ArrayList<>();
        int start = -1;
        for (int i = 0; i + 2 < d.length; i++) {
            if (d[i] == 0 && d[i + 1] == 0 && d[i + 2] == 1) {
                if (start >= 0) {
                    int end = i;
                    if (end > start && d[end - 1] == 0) end--;  // 4-byte start code
                    if (end > start) out.add(Arrays.copyOfRange(d, start, end));
                }
                start = i + 3;
                i += 2;
            }
        }
        if (start >= 0 && start < d.length) out.add(Arrays.copyOfRange(d, start, d.length));
        return out;
    }

    private static byte[] withStartCode(byte[] n) {
        byte[] o = new byte[n.length + 4];
        o[3] = 1;
        System.arraycopy(n, 0, o, 4, n.length);
        return o;
    }

    private static void writeNal(ByteArrayOutputStream out, byte[] n) {
        out.write(0);
        out.write(0);
        out.write(0);
        out.write(1);
        out.write(n, 0, n.length);
    }

    void appendJson(StringBuilder sb) {
        sb.append("\"video\":{");
        Telemetry.str(sb, "codec", codecName);
        Telemetry.num(sb, "w", width);
        Telemetry.num(sb, "h", height);
        Telemetry.num(sb, "fps", active() ? fps : 0);
        Telemetry.num(sb, "active", active() ? 1 : 0);
        Telemetry.num(sb, "dropped", dropped);
        sb.setLength(sb.length() - 1);
        sb.append("},");
    }
}
