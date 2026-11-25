package com.ugvuav.gcs;

import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.usb.UsbAccessory;
import android.hardware.usb.UsbManager;
import android.os.Build;
import android.os.ParcelFileDescriptor;

import java.io.DataInputStream;
import java.io.FileDescriptor;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * The link to the SJRC remote (USB, Android Open Accessory) plus an optional PC bridge on TCP 9000.
 *
 * Remote -> phone: FF 56 53 frames. Type 3 = video, type 5 = telemetry stream, type 6 = status.
 * Phone -> remote: the same frame header around heartbeats and drone messages (see Protocol).
 *
 * PC bridge (for development and the UGV computer): every frame from the remote is copied to the
 * connected PC (video only if enabled). The PC may send [type LE16][length LE16][payload] packets,
 * which are forwarded only while "PC control" is on; the app then stops sending its own flight
 * messages so the two never mix. Frame types 18 (power) and 20 (firmware upgrade) are refused.
 *
 * Safety: this phone's USB driver (MediaTek musb + f_accessory) panics the kernel when an
 * accessory read is left pending (reader closed, killed or frozen) and a new read queues the
 * same request again. So:
 *  - the remote is only opened if it was plugged in while this app was running,
 *  - the connection is never closed while a read may be pending; it ends when the remote is
 *    unplugged (that resets the USB function),
 *  - a foreground service keeps the app from being frozen or killed while connected.
 */
public final class Link {
    public static final int PORT = 9000;
    static final String ACTION_PERMISSION = "com.ugvuav.gcs.USB_PERMISSION";

    private static final Link INSTANCE = new Link();
    public static Link get() { return INSTANCE; }

    // Heartbeats copied from the official app (UsbWriteCommands): sent every 1000 ms.
    private static final byte[] HB_TYPE1 = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9};
    private static final byte[] HB_TYPE5 = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 37, 37};
    private static final byte[] HB_VIDEO = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 40, 40};

    final Telemetry telemetry = new Telemetry(this);
    final Flight flight = new Flight(this, telemetry);
    final Media media = new Media(this);
    final VideoDecoder video = new VideoDecoder(this);

    public volatile boolean videoEnabled = true;
    public volatile boolean allowPcCommands = false;
    public volatile boolean pcVideo = false;
    public volatile long rxBytes, txFrames, pcDropped;
    public final long[] rxFrames = new long[256];
    public volatile String pcAddress;
    public volatile String lastError;

    private final StringBuilder logText = new StringBuilder();
    private volatile int logVersion;

    private Context app;
    private UsbManager usb;
    private volatile boolean connected;      // accessory opened, threads running
    private volatile boolean remotePresent;  // Android lists an accessory
    private volatile boolean freshAttach;    // plugged in while this app was running
    private boolean sawNoRemote;
    private boolean permissionPending;
    private ParcelFileDescriptor pfd;
    private FileInputStream usbIn;
    private FileOutputStream usbOut;
    private final Object usbWriteLock = new Object();
    private int seq;
    private volatile Socket pc;
    private final ArrayBlockingQueue<byte[]> pcQueue = new ArrayBlockingQueue<>(512);

    private Link() {}

    public boolean isConnected() { return connected; }
    Context context() { return app; }
    public boolean isRemotePresent() { return remotePresent; }
    public boolean isFreshAttach() { return freshAttach; }

    public synchronized void init(Context context) {
        if (app != null) return;
        app = context.getApplicationContext();
        usb = (UsbManager) app.getSystemService(Context.USB_SERVICE);
        IntentFilter filter = new IntentFilter(ACTION_PERMISSION);
        filter.addAction(UsbManager.ACTION_USB_ACCESSORY_DETACHED);
        BroadcastReceiver receiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context c, Intent i) {
                if (ACTION_PERMISSION.equals(i.getAction())) {
                    boolean ok = i.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false);
                    synchronized (Link.this) { permissionPending = false; }
                    log(ok ? "Permission granted." : "Permission denied.");
                    if (ok) connect();
                } else if (UsbManager.ACTION_USB_ACCESSORY_DETACHED.equals(i.getAction())) {
                    onDetached();
                }
            }
        };
        if (Build.VERSION.SDK_INT >= 33) {
            app.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            app.registerReceiver(receiver, filter);
        }
        startThread("remote-watch", this::watchLoop);
        startThread("pc-server", this::serverLoop);
        startThread("pc-writer", this::pcWriteLoop);
        log("Ready. Plug in the remote, then tap Connect.");
    }

    // ---------------- remote (USB accessory) ----------------

    private UsbAccessory firstAccessory() {
        UsbAccessory[] list = usb.getAccessoryList();
        return (list == null || list.length == 0) ? null : list[0];
    }

    /** Android has no "accessory attached" broadcast for apps, so watch the accessory list. */
    private void watchLoop() {
        boolean first = true;
        while (true) {
            boolean present = firstAccessory() != null;
            synchronized (this) {
                if (present && !remotePresent) {
                    if (!first && sawNoRemote) {
                        freshAttach = true;
                        log("Remote plugged in. Tap Connect.");
                    } else if (!connected) {
                        log("The remote was already plugged in when the app started. "
                                + "For safety, unplug it and plug it in again.");
                    }
                }
                if (!present) {
                    sawNoRemote = true;
                    freshAttach = false;
                }
                remotePresent = present;
            }
            first = false;
            sleep(300);
        }
    }

    public synchronized void connect() {
        if (connected) {
            log("Already connected. Unplug the remote to disconnect.");
            return;
        }
        UsbAccessory acc = firstAccessory();
        if (acc == null) {
            log("No remote found. Turn the remote on and plug it into this phone.");
            return;
        }
        if (!freshAttach) {
            log("Safety: unplug the remote, plug it in again while this app is open, then tap Connect.");
            return;
        }
        if (!usb.hasPermission(acc)) {
            if (permissionPending) {
                log("Waiting for your answer in the permission dialog.");
                return;
            }
            permissionPending = true;
            int flags = Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0;
            Intent intent = new Intent(ACTION_PERMISSION).setPackage(app.getPackageName());
            usb.requestPermission(acc, PendingIntent.getBroadcast(app, 0, intent, flags));
            log("Asking for permission to use the remote. Tap OK.");
            return;
        }
        ParcelFileDescriptor p;
        try {
            p = usb.openAccessory(acc);
        } catch (Exception e) {
            log("Could not open the remote: " + e.getMessage());
            return;
        }
        if (p == null) {
            log("Could not open the remote. Is another app (SJ F PRO) using it?");
            return;
        }
        pfd = p;
        FileDescriptor fd = p.getFileDescriptor();
        usbIn = new FileInputStream(fd);
        usbOut = new FileOutputStream(fd);
        seq = 0;
        telemetry.reset();
        connected = true;
        LinkService.start(app);
        startThread("usb-reader", this::usbReadLoop);
        startThread("heartbeat", this::heartbeatLoop);
        flight.start();
        log("Connected to " + acc.getManufacturer() + " " + acc.getModel()
                + ". Unplug the remote to disconnect.");
    }

    private void onDetached() {
        log("Remote unplugged.");
        synchronized (this) {
            freshAttach = false;
            remotePresent = false;
            sawNoRemote = true;
            if (connected) {
                connected = false;
                // Safe to close now: the USB function is gone, so no read can stay queued.
                try { if (usbIn != null) usbIn.close(); } catch (IOException ignored) {}
                try { if (usbOut != null) usbOut.close(); } catch (IOException ignored) {}
                try { if (pfd != null) pfd.close(); } catch (IOException ignored) {}
                usbIn = null;
                usbOut = null;
                pfd = null;
            }
        }
        LinkService.stop(app);
    }

    /** Wraps a payload in a USB frame, exactly like the official app's UsbWriteCommands. */
    public void sendFrame(int type, byte[] payload) throws IOException {
        if (type == 18 || type == 20) throw new IOException("frame type " + type + " is blocked");
        byte[] f = new byte[11 + payload.length];
        f[0] = (byte) 0xFF;
        f[1] = 0x56;
        f[2] = 0x53;
        f[5] = (byte) payload.length;
        f[6] = (byte) (payload.length >> 8);
        f[7] = (byte) type;
        f[8] = (byte) (type >> 8);
        byte x = 0;
        for (byte b : payload) x ^= b;
        f[9] = x;
        System.arraycopy(payload, 0, f, 11, payload.length);
        synchronized (usbWriteLock) {
            FileOutputStream out = usbOut;
            if (!connected || out == null) throw new IOException("not connected");
            seq++;
            if (seq > 65535) seq = 0;
            f[3] = (byte) seq;
            f[4] = (byte) (seq >> 8);
            out.write(f);
            out.flush();
            txFrames++;
        }
    }

    /** Sends a drone message (USB frame type 6); returns false if the remote is not connected. */
    boolean sendDrone(byte[] message) {
        try {
            sendFrame(6, message);
            return true;
        } catch (IOException e) {
            return false;
        }
    }

    private void usbReadLoop() {
        byte[] buf = new byte[16384];  // accessory reads use a 16 KB buffer
        FrameParser parser = new FrameParser();
        FileInputStream in = usbIn;
        try {
            while (connected) {
                int n = in.read(buf);
                if (n < 0) break;
                if (n == 0) continue;
                rxBytes += n;
                parser.feed(buf, n);
            }
        } catch (IOException e) {
            if (connected) {
                lastError = "USB read: " + e.getMessage();
                log(lastError + ". Unplug the remote and plug it in again.");
            }
        }
        // Do not close here: closing with a read still queued is what crashes this phone.
    }

    private void heartbeatLoop() {
        sleep(10);
        int failures = 0;
        while (connected) {
            try {
                sendFrame(1, HB_TYPE1);
                sendFrame(5, HB_TYPE5);
                if (videoEnabled) sendFrame(3, HB_VIDEO);
                failures = 0;
            } catch (IOException e) {
                if (!connected) break;
                if (failures++ == 0) {
                    lastError = "USB write: " + e.getMessage();
                    log(lastError);
                }
            }
            sleep(1000);
        }
    }

    /** Splits the byte stream from the remote into FF 56 53 frames and hands them out. */
    private final class FrameParser {
        private final byte[] b = new byte[1 << 17];
        private int len;

        void feed(byte[] data, int n) {
            if (len + n > b.length) len = 0;  // resync on overflow
            System.arraycopy(data, 0, b, len, n);
            len += n;
            int p = 0;
            while (true) {
                int m = find(p);
                if (m < 0) { p = Math.max(p, len - 2); break; }
                if (len - m < 11) { p = m; break; }
                int plen = (b[m + 5] & 0xFF) | (b[m + 6] & 0xFF) << 8;
                int type = (b[m + 7] & 0xFF) | (b[m + 8] & 0xFF) << 8;
                if (len - m < 11 + plen) { p = m; break; }
                rxFrames[type & 0xFF]++;
                if (type == 3) {
                    if (videoEnabled) video.feed(b, m + 11, plen);
                } else {
                    telemetry.onFrame(type, b, m + 11, plen);
                }
                if (pc != null && (type != 3 || pcVideo)) {
                    if (!pcQueue.offer(Arrays.copyOfRange(b, m, m + 11 + plen))) pcDropped++;
                }
                p = m + 11 + plen;
            }
            System.arraycopy(b, p, b, 0, len - p);
            len -= p;
        }

        private int find(int from) {
            for (int i = from; i + 2 < len; i++) {
                if (b[i] == (byte) 0xFF && b[i + 1] == 0x56 && b[i + 2] == 0x53) return i;
            }
            return -1;
        }
    }

    // ---------------- PC (TCP) ----------------

    private void serverLoop() {
        while (true) {
            try (ServerSocket ss = new ServerSocket(PORT)) {
                while (true) {
                    Socket s = ss.accept();
                    s.setTcpNoDelay(true);
                    closePc();
                    pcQueue.clear();
                    pc = s;
                    pcAddress = s.getInetAddress().getHostAddress();
                    log("PC connected: " + pcAddress);
                    startThread("pc-reader", () -> pcReadLoop(s));
                }
            } catch (IOException e) {
                log("PC server error: " + e.getMessage());
                sleep(2000);
            }
        }
    }

    private void pcWriteLoop() {
        while (true) {
            try {
                byte[] f = pcQueue.poll(1, TimeUnit.SECONDS);
                Socket s = pc;
                if (f == null || s == null) continue;
                try {
                    OutputStream out = s.getOutputStream();
                    out.write(f);
                } catch (IOException e) {
                    if (pc == s) closePc();
                }
            } catch (InterruptedException ignored) {
                return;
            }
        }
    }

    private void pcReadLoop(Socket s) {
        int shown = 0;
        try {
            DataInputStream in = new DataInputStream(s.getInputStream());
            byte[] hdr = new byte[4];
            while (!s.isClosed()) {
                in.readFully(hdr);
                int type = (hdr[0] & 0xFF) | (hdr[1] & 0xFF) << 8;
                int len = (hdr[2] & 0xFF) | (hdr[3] & 0xFF) << 8;
                byte[] payload = new byte[len];
                in.readFully(payload);
                if (!allowPcCommands) {
                    if (shown++ < 3) log("PC command blocked (PC control is off in Settings).");
                    continue;
                }
                try {
                    sendFrame(type, payload);
                } catch (IOException e) {
                    log("PC frame refused: " + e.getMessage());
                }
            }
        } catch (IOException e) {
            // PC disconnected
        }
        if (pc == s) {
            log("PC disconnected.");
            closePc();
        }
    }

    private void closePc() {
        Socket s = pc;
        pc = null;
        pcAddress = null;
        try { if (s != null) s.close(); } catch (IOException ignored) {}
    }

    // ---------------- log ----------------

    public void log(String s) {
        String line = new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date()) + "  " + s + "\n";
        synchronized (logText) {
            logText.append(line);
            if (logText.length() > 30000) logText.delete(0, logText.length() - 20000);
            logVersion++;
        }
    }

    public int logVersion() { return logVersion; }

    public String logText() {
        synchronized (logText) { return logText.toString(); }
    }

    static void startThread(String name, Runnable r) {
        Thread t = new Thread(r, name);
        t.setDaemon(true);
        t.start();
    }

    static void sleep(long ms) {
        try { Thread.sleep(ms); } catch (InterruptedException ignored) {}
    }
}
