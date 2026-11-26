package com.ugvuav.gcs;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.RectF;
import android.net.Uri;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.DisplayCutout;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.PixelCopy;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.Collections;
import java.util.Locale;

/**
 * Full-screen landscape cockpit: hardware-decoded video in a SurfaceView underneath, and the
 * interface (assets/ui) in a transparent WebView on top. The interface talks to Java through the
 * "Android" JavaScript object below.
 */
public class MainActivity extends Activity implements SurfaceHolder.Callback {
    private static final String TAG = "UgvUav";

    private final Handler ui = new Handler(Looper.getMainLooper());
    private FrameLayout root;
    private SurfaceView videoView;
    private WebView web;
    private SharedPreferences prefs;
    private float density = 1;
    private RectF pip;              // video window in CSS pixels, null = full screen
    private boolean fill;           // full screen video: crop to fill instead of fit
    private int shownW, shownH;
    private volatile String insetsJson = "{\"l\":0,\"t\":0,\"r\":0,\"b\":0}";
    private volatile Location phone;
    private long lastGpsFix;
    private WifiManager.WifiLock wifiLock;
    private String version = "";

    private final Runnable videoWatch = new Runnable() {
        @Override
        public void run() {
            VideoDecoder v = Link.get().video;
            if (v.width != shownW || v.height != shownH) layoutVideo();
            ui.postDelayed(this, 1000);
        }
    };

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (Build.VERSION.SDK_INT >= 28) {
            WindowManager.LayoutParams lp = getWindow().getAttributes();
            lp.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
            getWindow().setAttributes(lp);
        }
        density = getResources().getDisplayMetrics().density;
        prefs = getSharedPreferences("settings", MODE_PRIVATE);
        try {
            version = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (PackageManager.NameNotFoundException ignored) {
        }

        Link link = Link.get();
        link.init(this);
        link.videoEnabled = prefs.getBoolean("video", true);
        link.pcVideo = prefs.getBoolean("pcVideo", false);
        link.flight.sticksEnabled = prefs.getBoolean("sticks", true);
        link.flight.stickPriority = prefs.getBoolean("stickPriority", false);
        if (prefs.contains("limAlt")) {
            link.flight.savedLimits = new int[]{prefs.getInt("limAlt", 120), prefs.getInt("limDist", 1500),
                    prefs.getInt("limRth", 30)};
        }
        fill = prefs.getBoolean("fill", false);

        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        videoView = new SurfaceView(this);
        videoView.getHolder().addCallback(this);
        root.addView(videoView, new FrameLayout.LayoutParams(1, 1, Gravity.CENTER));

        web = new WebView(this);
        web.setBackgroundColor(Color.TRANSPARENT);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.setVerticalScrollBarEnabled(false);
        web.setHorizontalScrollBarEnabled(false);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setTextZoom(100);
        web.addJavascriptInterface(new Api(), "Android");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return true;  // the cockpit never navigates away
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(ConsoleMessage m) {
                Log.i(TAG, "js: " + m.message() + " (" + m.sourceId() + ":" + m.lineNumber() + ")");
                if (m.messageLevel() == ConsoleMessage.MessageLevel.ERROR) {
                    Link.get().log("Interface error: " + m.message() + " (line " + m.lineNumber() + ")");
                }
                return true;
            }
        });
        WebView.setWebContentsDebuggingEnabled(true);
        root.addView(web, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));
        setContentView(root);

        root.addOnLayoutChangeListener((v, l, t, r, b, ol, ot, or, ob) -> {
            if (r - l != or - ol || b - t != ob - ot) ui.post(this::layoutVideo);
        });
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            readInsets(insets);
            return insets;
        });
        hideSystemBars();
        web.loadUrl("file:///android_asset/ui/index.html");

        WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        if (wm != null) {
            int mode = Build.VERSION.SDK_INT >= 29 ? WifiManager.WIFI_MODE_FULL_LOW_LATENCY
                    : WifiManager.WIFI_MODE_FULL_HIGH_PERF;
            wifiLock = wm.createWifiLock(mode, "ugvuav:pc-link");
            wifiLock.setReferenceCounted(false);
        }
        startLocation();
        ui.postDelayed(videoWatch, 1000);
    }

    @Override
    protected void onResume() {
        super.onResume();
        hideSystemBars();
        web.onResume();
        if (wifiLock != null) wifiLock.acquire();
    }

    @Override
    protected void onPause() {
        web.onPause();
        if (wifiLock != null && wifiLock.isHeld()) wifiLock.release();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        // Never stop the link here: closing the remote while a read is pending crashes this
        // phone. The connection ends when the remote is unplugged.
        ui.removeCallbacksAndMessages(null);
        LocationManager lm = getSystemService(LocationManager.class);
        if (lm != null) lm.removeUpdates(locationListener);
        super.onDestroy();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemBars();
    }

    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        // The interface closes its panels first; otherwise go to the background (keeps the link).
        web.evaluateJavascript("window.onBack ? onBack() : false", value -> {
            if (!"true".equals(value)) moveTaskToBack(true);
        });
    }

    @SuppressWarnings("deprecation")
    private void hideSystemBars() {
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) {
                c.hide(WindowInsets.Type.systemBars());
                c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    | View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN);
        }
    }

    private void readInsets(WindowInsets insets) {
        int l = 0, t = 0, r = 0, b = 0;
        if (Build.VERSION.SDK_INT >= 28) {
            DisplayCutout c = insets.getDisplayCutout();
            if (c != null) {
                l = c.getSafeInsetLeft();
                t = c.getSafeInsetTop();
                r = c.getSafeInsetRight();
                b = c.getSafeInsetBottom();
            }
        }
        insetsJson = String.format(Locale.US, "{\"l\":%.0f,\"t\":%.0f,\"r\":%.0f,\"b\":%.0f}",
                l / density, t / density, r / density, b / density);
    }

    // ---------------- video ----------------

    @Override
    public void surfaceCreated(SurfaceHolder holder) {
        Link.get().video.setSurface(holder.getSurface());
    }

    @Override
    public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) {
        Link.get().video.setSurface(holder.getSurface());
    }

    @Override
    public void surfaceDestroyed(SurfaceHolder holder) {
        Link.get().video.setSurface(null);
    }

    private void layoutVideo() {
        int rw = root.getWidth(), rh = root.getHeight();
        if (rw == 0 || rh == 0) return;
        VideoDecoder v = Link.get().video;
        shownW = v.width;
        shownH = v.height;
        float va = (shownW > 0 && shownH > 0) ? (float) shownW / shownH : 16f / 9f;
        float ax = 0, ay = 0, aw = rw, ah = rh;
        boolean crop = fill;
        if (pip != null) {
            ax = pip.left * density;
            ay = pip.top * density;
            aw = pip.width() * density;
            ah = pip.height() * density;
            crop = true;
        }
        float w = aw, h = aw / va;
        if (crop ? h < ah : h > ah) {
            h = ah;
            w = ah * va;
        }
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(Math.round(w), Math.round(h));
        lp.gravity = Gravity.TOP | Gravity.START;
        lp.leftMargin = Math.round(ax + (aw - w) / 2);
        lp.topMargin = Math.round(ay + (ah - h) / 2);
        videoView.setLayoutParams(lp);
        if (pip != null) {
            videoView.setClipBounds(null);
        }
    }

    // ---------------- phone location (for the map) ----------------

    private final LocationListener locationListener = new LocationListener() {
        @Override
        public void onLocationChanged(Location l) {
            long now = SystemClock.uptimeMillis();
            if (LocationManager.GPS_PROVIDER.equals(l.getProvider())) {
                lastGpsFix = now;
            } else if (now - lastGpsFix < 10000) {
                return;  // keep the better GPS position
            }
            phone = l;
        }

        @Override
        public void onStatusChanged(String provider, int status, Bundle extras) {}

        @Override
        public void onProviderEnabled(String provider) {}

        @Override
        public void onProviderDisabled(String provider) {}
    };

    private void startLocation() {
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION}, 1);
            return;
        }
        LocationManager lm = getSystemService(LocationManager.class);
        if (lm == null) return;
        for (String p : new String[]{LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER}) {
            try {
                if (lm.isProviderEnabled(p)) {
                    lm.requestLocationUpdates(p, 1000, 0, locationListener, Looper.getMainLooper());
                }
            } catch (Exception ignored) {
            }
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) startLocation();
    }

    // ---------------- JavaScript interface ----------------

    private final class Api {
        @JavascriptInterface
        public String state() {
            Link link = Link.get();
            StringBuilder sb = new StringBuilder(2048);
            sb.append('{');
            sb.append("\"link\":{");
            Telemetry.num(sb, "connected", link.isConnected() ? 1 : 0);
            Telemetry.num(sb, "present", link.isRemotePresent() ? 1 : 0);
            Telemetry.num(sb, "freshAttach", link.isFreshAttach() ? 1 : 0);
            Telemetry.num(sb, "rx", link.rxBytes);
            Telemetry.str(sb, "pc", link.pcAddress);
            Telemetry.num(sb, "pcControl", link.allowPcCommands ? 1 : 0);
            Telemetry.num(sb, "videoOn", link.videoEnabled ? 1 : 0);
            sb.setLength(sb.length() - 1);
            sb.append("},");
            link.telemetry.appendJson(sb);
            link.flight.appendJson(sb);
            link.media.appendJson(sb);
            link.video.appendJson(sb);
            Location p = phone;
            sb.append("\"phone\":");
            if (p != null) {
                sb.append(String.format(Locale.US, "{\"lat\":%.7f,\"lon\":%.7f,\"acc\":%.1f}",
                        p.getLatitude(), p.getLongitude(), p.getAccuracy()));
            } else {
                sb.append("null");
            }
            sb.append(",\"insets\":").append(insetsJson);
            sb.append(",\"logV\":").append(link.logVersion());
            sb.append('}');
            return sb.toString();
        }

        @JavascriptInterface
        public String command(String name) {
            String r = Link.get().flight.command(name);
            return r == null ? "" : r;
        }

        @JavascriptInterface
        public String mission(String json) {
            try {
                JSONObject o = new JSONObject(json);
                JSONArray a = o.getJSONArray("points");
                double[][] pts = new double[a.length()][];
                for (int i = 0; i < a.length(); i++) {
                    JSONArray p = a.getJSONArray(i);
                    pts[i] = new double[]{p.getDouble(0), p.getDouble(1)};
                }
                String r = Link.get().flight.startMission(pts, o.getDouble("alt"),
                        o.getInt("speed"), o.getInt("hover"));
                return r == null ? "" : r;
            } catch (Exception e) {
                return "Bad mission data: " + e.getMessage();
            }
        }

        /** Saves the live picture as a JPEG in the gallery (Pictures/Pindad UGV-UAV). */
        @JavascriptInterface
        public String snapshot() {
            if (!Link.get().video.active()) return "No live video to save to the phone.";
            ui.post(() -> {
                int w = videoView.getWidth(), h = videoView.getHeight();
                if (w <= 1 || h <= 1 || !videoView.getHolder().getSurface().isValid()) {
                    Link.get().log("Photo: the video window is not ready.");
                    return;
                }
                Bitmap bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
                PixelCopy.request(videoView, bmp, result -> {
                    if (result == PixelCopy.SUCCESS) {
                        Link.startThread("photo-save", () -> Link.get().media.savePhoto(bmp));
                    } else {
                        bmp.recycle();
                        Link.get().log("Photo: could not copy the picture (error " + result + ").");
                    }
                }, ui);
            });
            return "";
        }

        /** Opens the last saved photo or video in the phone's gallery app. */
        @JavascriptInterface
        public String openLast() {
            Media m = Link.get().media;
            if (m.lastUri.isEmpty()) return "Nothing saved yet.";
            ui.post(() -> {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW)
                            .setDataAndType(Uri.parse(m.lastUri), m.lastMime)
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK));
                } catch (Exception e) {
                    Link.get().log("Could not open the gallery: " + e.getMessage());
                }
            });
            return "";
        }

        /** Flight limits {"alt":m,"dist":m,"rth":m}: sent to the drone, kept, and re-sent after each connection. */
        @JavascriptInterface
        public String limits(String json) {
            try {
                JSONObject o = new JSONObject(json);
                int alt = o.getInt("alt"), dist = o.getInt("dist"), rth = o.getInt("rth");
                Flight f = Link.get().flight;
                String r = f.setLimits(alt, dist, rth);
                if (r != null) return r;
                f.savedLimits = new int[]{alt, dist, rth};
                prefs.edit().putInt("limAlt", alt).putInt("limDist", dist).putInt("limRth", rth).apply();
                return "";
            } catch (Exception e) {
                return "Bad limits: " + e.getMessage();
            }
        }

        @JavascriptInterface
        public void sticks(float yaw, float throttle, float roll, float pitch) {
            Link.get().flight.setSticks(yaw, throttle, roll, pitch);
        }

        @JavascriptInterface
        public void clearMission() {
            Link.get().flight.clearMission();
        }

        @JavascriptInterface
        public void connect() {
            ui.post(() -> Link.get().connect());
        }

        @JavascriptInterface
        public String log() {
            return Link.get().logText();
        }

        @JavascriptInterface
        public String settings() {
            Link link = Link.get();
            StringBuilder sb = new StringBuilder("{");
            Telemetry.num(sb, "video", link.videoEnabled ? 1 : 0);
            Telemetry.num(sb, "fill", fill ? 1 : 0);
            Telemetry.num(sb, "pcControl", link.allowPcCommands ? 1 : 0);
            Telemetry.num(sb, "pcVideo", link.pcVideo ? 1 : 0);
            Telemetry.num(sb, "sticks", link.flight.sticksEnabled ? 1 : 0);
            Telemetry.num(sb, "stickPriority", link.flight.stickPriority ? 1 : 0);
            Telemetry.num(sb, "port", Link.PORT);
            Telemetry.str(sb, "addresses", phoneAddresses());
            Telemetry.str(sb, "version", version);
            sb.setLength(sb.length() - 1);
            return sb.append('}').toString();
        }

        @JavascriptInterface
        public void set(String key, String value) {
            boolean on = "1".equals(value) || "true".equals(value);
            Link link = Link.get();
            switch (key) {
                case "video":
                    link.videoEnabled = on;
                    prefs.edit().putBoolean("video", on).apply();
                    break;
                case "fill":
                    fill = on;
                    prefs.edit().putBoolean("fill", on).apply();
                    ui.post(MainActivity.this::layoutVideo);
                    break;
                case "pcVideo":
                    link.pcVideo = on;
                    prefs.edit().putBoolean("pcVideo", on).apply();
                    break;
                case "sticks":
                    link.flight.sticksEnabled = on;
                    prefs.edit().putBoolean("sticks", on).apply();
                    break;
                case "stickPriority":
                    link.flight.stickPriority = on;
                    prefs.edit().putBoolean("stickPriority", on).apply();
                    break;
                case "pcControl":
                    if (link.allowPcCommands != on) {
                        link.allowPcCommands = on;
                        link.log(on ? "PC control ON: the app stopped sending flight messages."
                                : "PC control OFF: the app sends flight messages again.");
                    }
                    break;
                default:
                    break;
            }
        }

        @JavascriptInterface
        public void videoRect(float x, float y, float w, float h) {
            ui.post(() -> {
                pip = (w > 0 && h > 0) ? new RectF(x, y, x + w, y + h) : null;
                layoutVideo();
            });
        }

        @JavascriptInterface
        public void haptic() {
            ui.post(() -> web.performHapticFeedback(Build.VERSION.SDK_INT >= 30
                    ? HapticFeedbackConstants.CONFIRM : HapticFeedbackConstants.VIRTUAL_KEY));
        }
    }

    /** IPv4 addresses with a plain-language network type (for connecting a PC or the UGV). */
    static String phoneAddresses() {
        StringBuilder sb = new StringBuilder();
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                String name = ni.getName();
                String kind = name.startsWith("wlan") ? "Wi-Fi"
                        : name.startsWith("rmnet") || name.startsWith("ccmni") ? "mobile data"
                        : name.startsWith("tun") || name.startsWith("ppp") ? "VPN"
                        : name.startsWith("ap") || name.startsWith("swlan") ? "hotspot" : name;
                for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                    if (a instanceof Inet4Address) {
                        if (sb.length() > 0) sb.append(", ");
                        sb.append(a.getHostAddress()).append(" (").append(kind).append(')');
                    }
                }
            }
        } catch (Exception e) {
            return "unknown";
        }
        return sb.length() > 0 ? sb.toString() : "no network";
    }
}
