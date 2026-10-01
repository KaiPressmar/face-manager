package de.face_manager.app;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.DocumentsContract;
import android.webkit.MimeTypeMap;

import androidx.core.content.FileProvider;

import java.io.File;
import java.lang.ref.WeakReference;
import java.util.Locale;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/** Android-only operations called by the embedded Python server, never exposed directly to JS. */
public final class AndroidBridge {
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static volatile WeakReference<MainActivity> activity = new WeakReference<>(null);
    private final Context context;

    public AndroidBridge(Context context) {
        this.context = context.getApplicationContext();
    }

    static void attach(MainActivity target) {
        activity = new WeakReference<>(target);
    }

    static void detach(MainActivity target) {
        if (activity.get() == target) activity = new WeakReference<>(null);
    }

    /** Blocks a Python worker while the user selects an existing folder. Null means cancelled. */
    public String chooseFolder(String initialPath) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            throw new IllegalStateException("chooseFolder must run off the UI thread");
        }
        CompletableFuture<String> result = new CompletableFuture<>();
        MAIN.post(() -> {
            MainActivity current = activity.get();
            if (current == null || current.isFinishing() || current.isDestroyed()) {
                result.complete(null);
            } else {
                current.pickDirectory(initialPath, result);
            }
        });
        try {
            return result.get(10, TimeUnit.MINUTES);
        } catch (Exception ex) {
            result.cancel(false);
            return null;
        }
    }

    public boolean openFileLocation(String path) {
        if (path == null || path.trim().isEmpty()) return false;
        try {
            File file = new File(path).getCanonicalFile();
            File sharedRoot = Environment.getExternalStorageDirectory().getCanonicalFile();
            File parent = file.isDirectory() ? file : file.getParentFile();
            if (parent == null || !file.exists() || !inside(parent, sharedRoot)) return false;
            String relative = sharedRoot.toPath().relativize(parent.toPath())
                    .toString().replace(File.separatorChar, '/');
            String documentId = "primary:" + relative;
            Uri directory = DocumentsContract.buildDocumentUri(
                    "com.android.externalstorage.documents", documentId);
            Intent viewDirectory = new Intent(Intent.ACTION_VIEW);
            viewDirectory.setDataAndType(directory, DocumentsContract.Document.MIME_TYPE_DIR);
            viewDirectory.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                    | Intent.FLAG_ACTIVITY_NEW_TASK);
            try {
                context.startActivity(viewDirectory);
                return true;
            } catch (Exception ignored) { /* Fall through to the system file picker. */ }
            Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT);
            picker.addCategory(Intent.CATEGORY_OPENABLE);
            picker.setType("*/*");
            picker.putExtra(DocumentsContract.EXTRA_INITIAL_URI, directory);
            picker.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            try {
                context.startActivity(picker);
                return true;
            } catch (Exception ignored) { /* Show the in-app location below. */ }
            MainActivity current = activity.get();
            if (current != null && !current.isFinishing() && !current.isDestroyed()) {
                MAIN.post(() -> current.showFileLocation(file));
                return true;
            }
            return openFile(path, mimeFor(path));
        } catch (Exception ex) {
            return false;
        }
    }

    public boolean installApk(String path) {
        return openFile(path, "application/vnd.android.package-archive");
    }

    boolean openFileDirect(String path) {
        return openFile(path, mimeFor(path));
    }

    public boolean openExternalUrl(String url) {
        if (url == null) return false;
        Uri uri;
        try { uri = Uri.parse(url); }
        catch (Exception ex) { return false; }
        String scheme = uri.getScheme();
        if (!"https".equalsIgnoreCase(scheme) && !"http".equalsIgnoreCase(scheme)) return false;
        try {
            Intent intent = new Intent(Intent.ACTION_VIEW, uri);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(intent);
            return true;
        } catch (Exception ex) {
            return false;
        }
    }

    private boolean openFile(String path, String mime) {
        if (path == null || path.trim().isEmpty()) return false;
        try {
            File file = new File(path).getCanonicalFile();
            if (!file.exists() || !file.isFile()) return false;
            Uri uri = FileProvider.getUriForFile(context,
                    context.getPackageName() + ".files", file);
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(uri, mime);
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(intent);
            return true;
        } catch (Exception ex) {
            return false;
        }
    }

    private static String mimeFor(String path) {
        if (path == null) return "*/*";
        String name = path.toLowerCase(Locale.ROOT);
        int dot = name.lastIndexOf('.');
        String extension = dot < 0 ? "" : name.substring(dot + 1);
        String mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension);
        return mime == null ? "*/*" : mime;
    }

    private static boolean inside(File candidate, File boundary) {
        return candidate.equals(boundary)
                || candidate.getPath().startsWith(boundary.getPath() + File.separator);
    }
}
