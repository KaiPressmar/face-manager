package de.face_manager.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Environment;

import androidx.annotation.Nullable;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Foreground lifetime for the loopback Python API and its persisted import jobs. */
public final class BackendService extends Service {
    public interface Listener { void onBackendChanged(); }

    public static final String ACTION_STOP = "de.face_manager.app.STOP_BACKEND";
    private static final String CHANNEL_ID = "face_manager_library";
    private static final int NOTIFICATION_ID = 104;
    private static final String PAUSE_PREFS = "backend_state";
    private static final String PAUSE_KEY = "timeout_message";
    private static final String TIMEOUT_MESSAGE = "Android hat die Hintergrundverarbeitung zeitlich begrenzt. Öffne Face Manager, um fortzufahren.";
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final Object STATE_LOCK = new Object();
    private static final CopyOnWriteArrayList<Listener> LISTENERS = new CopyOnWriteArrayList<>();
    private static volatile int port;
    private static volatile String token;
    private static volatile String error;
    private static volatile boolean starting;

    // Python is process-wide, so every Service instance must use the same serial lane.
    private static final ExecutorService LIFECYCLE = Executors.newSingleThreadExecutor();
    private static volatile long generation;
    private long myGeneration;
    private volatile boolean stopping;

    public static int port() { return port; }
    public static String token() { return token; }
    public static String error() { return error; }
    public static boolean starting() { return starting; }
    public static boolean ready() { return port > 0 && token != null; }
    public static int getPort() { return port(); }
    public static String getToken() { return token(); }
    public static boolean isReady() { return ready(); }

    public static String consumePauseReason(Context context) {
        String message = context.getSharedPreferences(PAUSE_PREFS, MODE_PRIVATE)
                .getString(PAUSE_KEY, null);
        if (message != null) context.getSharedPreferences(PAUSE_PREFS, MODE_PRIVATE)
                .edit().remove(PAUSE_KEY).apply();
        return message;
    }

    public static void addListener(Listener listener) {
        LISTENERS.addIfAbsent(listener);
        MAIN.post(listener::onBackendChanged);
    }

    public static void removeListener(Listener listener) {
        LISTENERS.remove(listener);
    }

    @Override public void onCreate() {
        super.onCreate();
        synchronized (STATE_LOCK) {
            myGeneration = ++generation;
            port = 0;
            token = null;
            starting = false;
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL_ID,
                "Lokale Fotobibliothek", NotificationManager.IMPORTANCE_LOW));
        startForegroundNow(notification("Lokale Fotobibliothek wird gestartet …"));
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (Build.VERSION.SDK_INT >= 30 && !Environment.isExternalStorageManager()) {
            error = "Der Zugriff auf die Fotobibliothek wurde entzogen.";
            publish();
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!starting && !ready()) {
            starting = true;
            error = null;
            publish();
            LIFECYCLE.execute(this::startPython);
        }
        return START_STICKY;
    }

    private void startPython() {
        try {
            if (stopping || generation != myGeneration) return;
            if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
            // If an earlier Service stopped and restarted quickly, close its runtime first.
            stopPython();
            if (stopping || generation != myGeneration) return;
            PyObject result = Python.getInstance().getModule("android_runtime")
                    .callAttr("start", getApplicationContext(), new AndroidBridge(this));
            int newPort = 0;
            String newToken = null;
            for (Map.Entry<PyObject, PyObject> entry : result.asMap().entrySet()) {
                if ("port".equals(entry.getKey().toString())) {
                    newPort = Integer.parseInt(entry.getValue().toString());
                } else if ("token".equals(entry.getKey().toString())) {
                    newToken = entry.getValue().toString();
                }
            }
            if (newPort < 1 || newToken == null || newToken.isEmpty()) {
                throw new IllegalStateException("Der lokale Server lieferte keine Adresse.");
            }
            synchronized (STATE_LOCK) {
                if (stopping || generation != myGeneration) return;
                port = newPort;
                token = newToken;
                error = null;
                starting = false;
            }
            publish();
            if (!stopping && generation == myGeneration) {
                getSystemService(NotificationManager.class).notify(NOTIFICATION_ID,
                        notification("Deine Fotobibliothek läuft lokal auf diesem Gerät"));
            }
        } catch (Exception ex) {
            synchronized (STATE_LOCK) {
                if (stopping || generation != myGeneration) return;
                port = 0;
                token = null;
                starting = false;
                error = describe(ex);
            }
            publish();
            stopSelf();
        }
    }

    private Notification notification(String message) {
        Intent open = new Intent(this, MainActivity.class);
        open.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent openPending = PendingIntent.getActivity(this, 0, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent stop = new Intent(this, BackendService.class).setAction(ACTION_STOP);
        PendingIntent stopPending = PendingIntent.getService(this, 1, stop,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_menu_gallery)
                .setContentTitle("Face Manager")
                .setContentText(message)
                .setContentIntent(openPending)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .addAction(android.R.drawable.ic_menu_close_clear_cancel,
                        "Beenden", stopPending)
                .build();
    }

    private void startForegroundNow(Notification notification) {
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    @Override public void onTimeout(int startId, int fgsType) {
        getSharedPreferences(PAUSE_PREFS, MODE_PRIVATE).edit()
                .putString(PAUSE_KEY, TIMEOUT_MESSAGE).commit();
        synchronized (STATE_LOCK) {
            if (generation == myGeneration) error = TIMEOUT_MESSAGE;
        }
        publish();
        stopSelf();
    }

    @Override public void onDestroy() {
        stopping = true;
        synchronized (STATE_LOCK) {
            if (generation == myGeneration) {
                port = 0;
                token = null;
                starting = false;
            }
        }
        publish();
        LIFECYCLE.execute(() -> {
            // A successor's start task closes our runtime before starting its own.
            if (generation != myGeneration) return;
            try {
                stopPython();
            } catch (Exception ex) {
                synchronized (STATE_LOCK) {
                    if (generation != myGeneration) return;
                    error = "Lokaler Dienst konnte nicht beendet werden: " + describe(ex);
                }
                publish();
            }
        });
        super.onDestroy();
    }

    private void stopPython() {
        if (Python.isStarted()) Python.getInstance().getModule("android_runtime").callAttr("stop");
    }

    private static String describe(Exception ex) {
        return ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage();
    }

    private static void publish() {
        MAIN.post(() -> {
            for (Listener listener : LISTENERS) listener.onBackendChanged();
        });
    }

    @Nullable @Override public IBinder onBind(Intent intent) { return null; }
}
