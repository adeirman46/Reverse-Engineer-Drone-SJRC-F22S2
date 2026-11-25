package com.ugvuav.gcs;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

/**
 * Foreground service that runs while the remote is connected, so Android does not freeze or
 * kill the app in the middle of a USB read (which can crash this phone's kernel).
 */
public class LinkService extends Service {
    private static final String CHANNEL = "link";

    static void start(Context c) {
        try {
            c.startForegroundService(new Intent(c, LinkService.class));
        } catch (Exception e) {
            Link.get().log("Could not start the background service: " + e.getMessage());
        }
    }

    static void stop(Context c) {
        c.stopService(new Intent(c, LinkService.class));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager nm = getSystemService(NotificationManager.class);
        CharSequence name = getApplicationInfo().loadLabel(getPackageManager());
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, name, NotificationManager.IMPORTANCE_LOW));
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(this, CHANNEL)
                .setContentTitle(name + " is connected to the remote")
                .setContentText("Unplug the remote before closing the app.")
                .setSmallIcon(android.R.drawable.stat_sys_upload)
                .setContentIntent(open)
                .setOngoing(true)
                .build();
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
            } else {
                startForeground(1, n);
            }
        } catch (Exception e) {
            Link.get().log("Background service refused: " + e.getMessage());
            stopSelf();
        }
        return START_NOT_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
