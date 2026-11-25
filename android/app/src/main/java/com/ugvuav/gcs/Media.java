package com.ugvuav.gcs;

import android.content.ContentValues;
import android.content.Context;
import android.graphics.Bitmap;
import android.media.MediaCodec;
import android.media.MediaFormat;
import android.media.MediaMuxer;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.os.ParcelFileDescriptor;
import android.os.SystemClock;
import android.provider.MediaStore;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * Saves photos and videos into the phone's gallery (Pictures / Movies, folder "Pindad UGV-UAV").
 *
 * Video: the frames arrive already compressed (H.264 or H.265), so they are written straight into
 * an MP4 with MediaMuxer, no re-encoding and no quality loss. Recording starts at the next keyframe.
 * Photo: a JPEG of the live picture (MainActivity copies it from the video surface).
 * The drone also keeps its own full-quality copies on its SD card.
 */
final class Media {
    static final String FOLDER = "Pindad UGV-UAV";
    private final Link link;

    volatile String lastUri = "";
    volatile String lastMime = "";
    volatile long lastAt;

    private volatile boolean recording;
    volatile long recStartedAt;
    private MediaMuxer muxer;
    private ParcelFileDescriptor pfd;
    private Uri uri;
    private File file;
    private String name;
    private int track = -1;
    private long firstPts, lastPts, rawPrev, wrap, frames;

    Media(Link link) {
        this.link = link;
    }

    boolean isRecording() {
        return recording;
    }

    synchronized void startRecording() {
        if (recording) return;
        recording = true;
        frames = 0;
        firstPts = lastPts = rawPrev = -1;
        wrap = 0;
        recStartedAt = SystemClock.uptimeMillis();
    }

    synchronized void stopRecording() {
        finish(true);
    }

    /** Every compressed frame from the remote (USB reader thread). */
    synchronized void onFrame(byte[] data, boolean key, long rawPts, int w, int h) {
        if (!recording) return;
        try {
            if (muxer == null) {
                if (!key) return;
                String mime = VideoDecoder.detect(data);
                if (mime == null) return;
                open(mime, data, w > 0 ? w : 1280, h > 0 ? h : 720);
            }
            if (rawPrev >= 0 && rawPts < rawPrev - 0x80000000L) wrap += 0x100000000L;  // 32-bit microsecond clock wrapped
            rawPrev = rawPts;
            long pts = rawPts + wrap;
            if (firstPts < 0) firstPts = pts;
            long t = pts - firstPts;
            if (t <= lastPts) t = lastPts + 1000;
            lastPts = t;
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            info.set(0, data.length, t, key ? MediaCodec.BUFFER_FLAG_KEY_FRAME : 0);
            muxer.writeSampleData(track, ByteBuffer.wrap(data), info);
            frames++;
        } catch (Exception e) {
            link.log("Phone recording stopped: " + e.getMessage());
            finish(false);
        }
    }

    private void open(String mime, byte[] key, int w, int h) throws Exception {
        MediaFormat fmt = MediaFormat.createVideoFormat(mime, w, h);
        VideoDecoder.setCsd(fmt, mime, key);
        name = "UGVUAV_" + stamp() + ".mp4";
        Context c = link.context();
        if (Build.VERSION.SDK_INT >= 29) {
            ContentValues v = new ContentValues();
            v.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
            v.put(MediaStore.MediaColumns.MIME_TYPE, "video/mp4");
            v.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_MOVIES + "/" + FOLDER);
            v.put(MediaStore.MediaColumns.IS_PENDING, 1);
            uri = c.getContentResolver().insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, v);
            if (uri == null) throw new IllegalStateException("the gallery refused the file");
            pfd = c.getContentResolver().openFileDescriptor(uri, "rw");
            muxer = new MediaMuxer(pfd.getFileDescriptor(), MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
        } else {
            File dir = c.getExternalFilesDir(Environment.DIRECTORY_MOVIES);
            if (dir != null) dir.mkdirs();
            file = new File(dir, name);
            muxer = new MediaMuxer(file.getAbsolutePath(), MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
        }
        track = muxer.addTrack(fmt);
        muxer.start();
        link.log("Recording to the phone: " + name);
    }

    private void finish(boolean keep) {
        recording = false;
        if (muxer == null) {
            if (keep) link.log("Phone recording: no video arrived, nothing saved (is Live video on?).");
            return;
        }
        boolean ok = keep && frames > 0;
        try { muxer.stop(); } catch (Exception e) { ok = false; }
        try { muxer.release(); } catch (Exception ignored) {}
        muxer = null;
        try { if (pfd != null) pfd.close(); } catch (Exception ignored) {}
        pfd = null;
        Context c = link.context();
        try {
            if (uri != null) {
                if (ok) {
                    ContentValues v = new ContentValues();
                    v.put(MediaStore.MediaColumns.IS_PENDING, 0);
                    c.getContentResolver().update(uri, v, null, null);
                    saved(uri.toString(), "video/mp4", "Movies/" + FOLDER + "/" + name);
                } else {
                    c.getContentResolver().delete(uri, null, null);
                }
            } else if (file != null && ok) {
                saved(Uri.fromFile(file).toString(), "video/mp4", file.getAbsolutePath());
            }
        } catch (Exception e) {
            link.log("Could not finish the video file: " + e.getMessage());
        }
        uri = null;
        file = null;
    }

    /** JPEG into Pictures/Pindad UGV-UAV. Runs on a background thread. */
    void savePhoto(Bitmap bmp) {
        String n = "UGVUAV_" + stamp() + ".jpg";
        Context c = link.context();
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                ContentValues v = new ContentValues();
                v.put(MediaStore.MediaColumns.DISPLAY_NAME, n);
                v.put(MediaStore.MediaColumns.MIME_TYPE, "image/jpeg");
                v.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/" + FOLDER);
                v.put(MediaStore.MediaColumns.IS_PENDING, 1);
                Uri u = c.getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, v);
                if (u == null) throw new IllegalStateException("the gallery refused the file");
                try (OutputStream out = c.getContentResolver().openOutputStream(u)) {
                    bmp.compress(Bitmap.CompressFormat.JPEG, 93, out);
                }
                v.clear();
                v.put(MediaStore.MediaColumns.IS_PENDING, 0);
                c.getContentResolver().update(u, v, null, null);
                saved(u.toString(), "image/jpeg", "Pictures/" + FOLDER + "/" + n);
            } else {
                File dir = c.getExternalFilesDir(Environment.DIRECTORY_PICTURES);
                if (dir != null) dir.mkdirs();
                File f = new File(dir, n);
                try (OutputStream out = new FileOutputStream(f)) {
                    bmp.compress(Bitmap.CompressFormat.JPEG, 93, out);
                }
                saved(Uri.fromFile(f).toString(), "image/jpeg", f.getAbsolutePath());
            }
        } catch (Exception e) {
            link.log("Could not save the photo: " + e.getMessage());
        } finally {
            bmp.recycle();
        }
    }

    private void saved(String u, String mime, String where) {
        lastUri = u;
        lastMime = mime;
        lastAt = SystemClock.uptimeMillis();
        link.log("Saved to Gallery: " + where);
    }

    private static String stamp() {
        return new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date());
    }

    void appendJson(StringBuilder sb) {
        sb.append("\"media\":{");
        Telemetry.str(sb, "uri", lastUri);
        Telemetry.str(sb, "mime", lastMime);
        Telemetry.num(sb, "at", lastAt);
        Telemetry.num(sb, "rec", recording ? 1 : 0);
        sb.setLength(sb.length() - 1);
        sb.append("},");
    }
}
