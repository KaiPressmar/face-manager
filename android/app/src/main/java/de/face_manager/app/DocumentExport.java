package de.face_manager.app;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.DocumentsContract;
import android.util.Log;

import androidx.activity.result.ActivityResultLauncher;

import com.chaquo.python.Python;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** A single export spans preparation, the system picker and the destination write. */
final class DocumentExport {
    private static final String TAG = "FaceManagerExport";
    private static final String KEY_SNAPSHOT = "pendingExport";
    private static final String KEY_WRITING = "exportWriting";
    private static final String KEY_PREPARING = "exportPreparing";
    private static final String KEY_FILE_TOKEN = "exportFileToken";
    private static final String EXPORT_PREFS = "documentExport";

    private enum Phase {
        IDLE, PREPARING, READY, CHOOSING, WRITING, SAVED, CANCELLED,
        SNAPSHOT_ERROR, PICKER_ERROR, WRITE_ERROR, PARTIAL_FILE, INTERRUPTED, INTERRUPTED_WRITE, OCCUPIED
    }

    private record FileResult(String token, Phase phase) {}

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private volatile Phase phase = Phase.IDLE;
    private volatile Phase databasePhase = Phase.IDLE;
    private volatile String stagedToken;
    private volatile FileResult lastFileResult;
    private volatile FileResult rejectedFile;
    private FileResult announcedFileResult;
    private File snapshot;
    private String filename;
    private String mimeType;
    private Runnable listener;
    private long request;

    String status() { return stagedToken == null ? databasePhase.name() : "FILE_BUSY"; }

    String stagedStatus(String token) {
        FileResult rejected = rejectedFile;
        FileResult result = lastFileResult;
        if (token != null && rejected != null && token.equals(rejected.token())) return rejected.phase().name();
        if (token != null && result != null && token.equals(result.token())) return result.phase().name();
        if (token != null && token.equals(stagedToken)) return phase.name();
        return Phase.IDLE.name();
    }

    String takeStagedFileNotice() {
        FileResult latest = lastFileResult;
        if (latest == null || latest == announcedFileResult) return null;
        announcedFileResult = latest;
        return switch (latest.phase()) {
            case SAVED -> "Datei gespeichert";
            case WRITE_ERROR, PICKER_ERROR -> "Die Datei konnte nicht gespeichert werden.";
            case PARTIAL_FILE -> "Die Datei ist unvollständig. Lösche sie am gewählten Speicherort.";
            default -> null;
        };
    }

    void attach(Runnable next) {
        listener = next;
    }

    void detach(Runnable previousListener, boolean finishing) {
        if (listener != previousListener) return;
        listener = null;
        if (finishing && (phase == Phase.PREPARING || phase == Phase.READY || phase == Phase.CHOOSING)) {
            ++request;
            finish(Phase.CANCELLED);
        }
    }

    void start(boolean previousLibrary) {
        if (isBusy()) return;
        filename = previousLibrary ? "face-manager-vorherige-bibliothek.sqlite" : "face-manager-database.sqlite";
        mimeType = "application/octet-stream";
        long id = ++request;
        setPhase(Phase.PREPARING);
        io.execute(() -> {
            File result = null;
            try {
                result = new File(Python.getInstance().getModule("android_runtime")
                        .callAttr("export_database", previousLibrary).toString());
                if (!result.isFile() || result.length() == 0) throw new IOException("Missing snapshot");
                File prepared = result;
                main.post(() -> {
                    if (id != request) {
                        deleteSnapshot(prepared);
                        return;
                    }
                    snapshot = prepared;
                    setPhase(Phase.READY);
                });
            } catch (Exception ex) {
                deleteSnapshot(result);
                Log.w(TAG, "Could not prepare library snapshot", ex);
                main.post(() -> { if (id == request) finish(Phase.SNAPSHOT_ERROR); });
            }
        });
    }

    void startStagedFile(Context context, String token, String suggestedName) {
        if (token == null || !token.matches("[0-9a-f]{32}")) return;
        if (token.equals(stagedToken)) return;
        if (isBusy()) {
            rejectedFile = new FileResult(token, Phase.OCCUPIED);
            notifyListener();
            return;
        }
        File directory = new File(context.getFilesDir(), "backend/exports");
        File staged = new File(directory, "album-" + token + ".upload");
        File pending = new File(directory, "face-manager-" + token + ".file");
        if (suggestedName == null || !(suggestedName.endsWith(".png") || suggestedName.endsWith(".zip"))
                || !staged.isFile() || pending.exists() || !staged.renameTo(pending)) {
            rejectedFile = new FileResult(token, Phase.SNAPSHOT_ERROR);
            notifyListener();
            return;
        }
        boolean png = suggestedName.endsWith(".png");
        filename = suggestedName.length() <= 180 && suggestedName.indexOf('/') < 0
                && suggestedName.indexOf('\\') < 0 && suggestedName.chars().noneMatch(Character::isISOControl)
                ? suggestedName : png ? "face-manager-fotobuch.png" : "face-manager-fotobuch.zip";
        mimeType = png ? "image/png" : "application/zip";
        snapshot = pending;
        stagedToken = token;
        setPhase(Phase.READY);
    }

    void openPicker(ActivityResultLauncher<Intent> picker) {
        if (phase != Phase.READY) return;
        Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        save.setType(mimeType);
        save.addCategory(Intent.CATEGORY_OPENABLE);
        save.putExtra(Intent.EXTRA_TITLE, filename);
        setPhase(Phase.CHOOSING);
        try {
            picker.launch(save);
        } catch (RuntimeException ex) {
            Log.w(TAG, "Could not open save dialog", ex);
            finish(Phase.PICKER_ERROR);
        }
    }

    void onPickerResult(Context context, Uri destination) {
        if (phase != Phase.CHOOSING) return;
        if (destination == null) {
            finish(Phase.CANCELLED);
            return;
        }
        File source = snapshot;
        Context application = context.getApplicationContext();
        setPhase(Phase.WRITING);
        io.execute(() -> {
            var preferences = application.getSharedPreferences(EXPORT_PREFS, Context.MODE_PRIVATE);
            if (!preferences.edit().putBoolean(KEY_WRITING, true).commit()) {
                Log.w(TAG, "Could not record the pending document write");
            }
            Phase outcome;
            try {
                outcome = writeSnapshot(application, source, destination);
            } finally {
                deleteSnapshot(source);
                preferences.edit().remove(KEY_WRITING).apply();
            }
            main.post(() -> finish(outcome));
        });
    }

    void save(Bundle state) {
        if (phase == Phase.CHOOSING) {
            state.putString(KEY_SNAPSHOT, snapshot.getAbsolutePath());
            state.putString(KEY_FILE_TOKEN, stagedToken);
        }
        if (phase == Phase.WRITING) state.putBoolean(KEY_WRITING, true);
        if (phase == Phase.PREPARING || phase == Phase.READY) state.putBoolean(KEY_PREPARING, true);
    }

    void restore(Context context, Bundle state) {
        // Configuration changes share the in-memory operation. A new process can
        // still receive a pending document-picker result through ActivityResultRegistry.
        if (phase != Phase.IDLE) return;
        var preferences = context.getSharedPreferences(EXPORT_PREFS, Context.MODE_PRIVATE);
        if (preferences.getBoolean(KEY_WRITING, false)) {
            preferences.edit().remove(KEY_WRITING).apply();
            setPhase(Phase.INTERRUPTED_WRITE);
            return;
        }
        if (state == null) return;
        String path = state.getString(KEY_SNAPSHOT);
        if (path != null) {
            File pending = new File(path);
            File directory = new File(context.getFilesDir(), "backend/exports");
            try {
                File canonical = pending.getCanonicalFile();
                String pendingToken = state.getString(KEY_FILE_TOKEN);
                boolean staged = pendingToken != null && pendingToken.matches("[0-9a-f]{32}")
                        && canonical.getName().equals("face-manager-" + pendingToken + ".file");
                if (directory.getCanonicalFile().equals(canonical.getParentFile())
                        && canonical.getName().startsWith("face-manager-")
                        && (canonical.getName().endsWith(".sqlite") || staged) && canonical.isFile()) {
                    snapshot = canonical;
                    if (staged) stagedToken = pendingToken;
                    setPhase(Phase.CHOOSING);
                    return;
                }
            } catch (IOException ex) {
                Log.w(TAG, "Could not locate the pending snapshot", ex);
            }
            setPhase(Phase.INTERRUPTED);
        } else if (state.getBoolean(KEY_WRITING)) {
            setPhase(Phase.INTERRUPTED_WRITE);
        } else if (state.getBoolean(KEY_PREPARING)) {
            setPhase(Phase.INTERRUPTED);
        }
    }

    private boolean isBusy() {
        return phase == Phase.PREPARING || phase == Phase.READY
                || phase == Phase.CHOOSING || phase == Phase.WRITING;
    }

    private void setPhase(Phase next) {
        phase = next;
        if (stagedToken == null) databasePhase = next;
        notifyListener();
    }

    private void notifyListener() {
        if (listener != null) listener.run();
    }

    private void finish(Phase outcome) {
        deleteSnapshot(snapshot);
        snapshot = null;
        if (stagedToken == null) {
            setPhase(outcome);
        } else {
            lastFileResult = new FileResult(stagedToken, outcome);
            stagedToken = null;
            phase = outcome;
            notifyListener();
        }
    }

    private static void deleteSnapshot(File file) {
        if (file != null && !file.delete() && file.exists()) {
            Log.w(TAG, "Could not delete the private temporary snapshot");
        }
    }

    private static Phase writeSnapshot(Context context, File source, Uri destination) {
        var resolver = context.getContentResolver();
        try (FileInputStream input = new FileInputStream(source);
             OutputStream target = resolver.openOutputStream(destination, "w")) {
            if (target == null) throw new IOException("Missing destination stream");
            byte[] chunk = new byte[64 * 1024];
            int count;
            while ((count = input.read(chunk)) != -1) target.write(chunk, 0, count);
        } catch (Exception ex) {
            Log.w(TAG, "Could not finish writing the library snapshot", ex);
            try {
                if (DocumentsContract.deleteDocument(resolver, destination)) return Phase.WRITE_ERROR;
            } catch (Exception cleanupError) {
                Log.w(TAG, "Could not delete the incomplete export", cleanupError);
            }
            return Phase.PARTIAL_FILE;
        }
        return Phase.SAVED;
    }
}
