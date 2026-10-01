package de.face_manager.app;

import android.Manifest;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceError;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.RenderProcessGoneDetail;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.ComponentActivity;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.core.content.ContextCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;

import com.chaquo.python.Python;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Trusted WebView host for the same React application served by the on-device Python API. */
public final class MainActivity extends ComponentActivity {
    private static final int INK = Color.rgb(30, 48, 42);
    private static final int GREEN = Color.rgb(36, 106, 86);
    private static final int PAPER = Color.rgb(248, 248, 244);
    private static final int REQUEST_READ_FILES = 301;
    private static final int REQUEST_MEDIA_LOCATION = 302;
    private static final int REQUEST_NOTIFICATIONS = 303;
    private static final String KEY_EXPORT = "pendingExport";
    private static final String KEY_PERMISSION_IN_FLIGHT = "optionalPermissionInFlight";
    // Instrumentation cannot revoke MANAGE_EXTERNAL_STORAGE in its own UID: Android kills it.
    static volatile Boolean fileAccessOverrideForTest;

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private AndroidBridge bridge;
    private final BackendService.Listener backendListener = this::showCurrentState;
    private LinearLayout root;
    private WebView web;
    private String origin;
    private String pendingExport;
    private boolean pageErrorShown;
    private boolean optionalPermissionInFlight;
    private CompletableFuture<String> folderChoice;
    private WebChromeClient.FileChooserParams chooserParams;
    private android.webkit.ValueCallback<Uri[]> fileCallback;
    private ActivityResultLauncher<Intent> filePicker;
    private ActivityResultLauncher<Intent> exportPicker;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        bridge = new AndroidBridge(this);
        if (state != null) {
            pendingExport = state.getString(KEY_EXPORT);
            optionalPermissionInFlight = state.getBoolean(KEY_PERMISSION_IN_FLIGHT);
        }
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        getWindow().setStatusBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarColor(Color.TRANSPARENT);
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(PAPER);
        ViewCompat.setOnApplyWindowInsetsListener(root, (view, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars()
                    | WindowInsetsCompat.Type.displayCutout());
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return insets;
        });
        setContentView(root);
        filePicker = registerForActivityResult(new ActivityResultContracts.StartActivityForResult(), result -> {
            if (fileCallback != null) {
                fileCallback.onReceiveValue(result.getResultCode() == RESULT_OK
                        ? WebChromeClient.FileChooserParams.parseResult(result.getResultCode(), result.getData())
                        : null);
                fileCallback = null;
                chooserParams = null;
            }
        });
        exportPicker = registerForActivityResult(new ActivityResultContracts.StartActivityForResult(), result -> {
            String snapshot = pendingExport;
            pendingExport = null;
            if (snapshot == null) return;
            if (result.getResultCode() != RESULT_OK || result.getData() == null
                    || result.getData().getData() == null) {
                new File(snapshot).delete();
                return;
            }
            Uri destination = result.getData().getData();
            io.execute(() -> writeSnapshot(snapshot, destination));
        });
        AndroidBridge.attach(this);
        BackendService.addListener(backendListener);
    }

    @Override protected void onResume() {
        super.onResume();
        String pauseReason = BackendService.consumePauseReason(this);
        if (pauseReason != null) Toast.makeText(this, pauseReason, Toast.LENGTH_LONG).show();
        checkAccessAndStart();
    }

    @Override protected void onSaveInstanceState(Bundle out) {
        out.putString(KEY_EXPORT, pendingExport);
        out.putBoolean(KEY_PERMISSION_IN_FLIGHT, optionalPermissionInFlight);
        super.onSaveInstanceState(out);
    }

    @Override protected void onDestroy() {
        BackendService.removeListener(backendListener);
        AndroidBridge.detach(this);
        if (folderChoice != null) folderChoice.complete(null);
        if (fileCallback != null) fileCallback.onReceiveValue(null);
        if (web != null) {
            root.removeView(web);
            web.removeJavascriptInterface("AndroidExport");
            web.destroy();
            web = null;
        }
        io.shutdown();
        super.onDestroy();
    }

    @Override public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    private boolean hasFileAccess() {
        if (BuildConfig.DEBUG && fileAccessOverrideForTest != null) {
            return fileAccessOverrideForTest;
        }
        if (Build.VERSION.SDK_INT >= 30) return Environment.isExternalStorageManager();
        return checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void checkAccessAndStart() {
        if (!hasFileAccess()) {
            stopService(new Intent(this, BackendService.class));
            closeWeb();
            showIntro();
            return;
        }
        try {
            ContextCompat.startForegroundService(this, new Intent(this, BackendService.class));
        } catch (Exception ex) {
            Toast.makeText(this, "Lokalen Dienst starten: " + ex.getMessage(),
                    Toast.LENGTH_LONG).show();
        }
        showCurrentState();
        requestOptionalPermissions();
    }

    private void requestOptionalPermissions() {
        if (optionalPermissionInFlight || !hasFileAccess() || isFinishing() || isDestroyed()) return;
        if (Build.VERSION.SDK_INT >= 29
                && checkSelfPermission(Manifest.permission.ACCESS_MEDIA_LOCATION)
                != PackageManager.PERMISSION_GRANTED
                && !getPreferences(0).getBoolean("asked_media_location", false)) {
            getPreferences(0).edit().putBoolean("asked_media_location", true).apply();
            optionalPermissionInFlight = true;
            requestPermissions(new String[]{Manifest.permission.ACCESS_MEDIA_LOCATION}, REQUEST_MEDIA_LOCATION);
            return;
        }
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
                && !getPreferences(0).getBoolean("asked_notifications", false)) {
            getPreferences(0).edit().putBoolean("asked_notifications", true).apply();
            optionalPermissionInFlight = true;
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQUEST_NOTIFICATIONS);
        }
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions,
                                                      int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQUEST_READ_FILES) checkAccessAndStart();
        if (requestCode == REQUEST_MEDIA_LOCATION && grantResults.length > 0
                && grantResults[0] != PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "Standortdaten in Fotos können eingeschränkt sein.",
                    Toast.LENGTH_LONG).show();
        }
        if (requestCode == REQUEST_MEDIA_LOCATION || requestCode == REQUEST_NOTIFICATIONS) {
            optionalPermissionInFlight = false;
            main.post(this::requestOptionalPermissions);
        }
    }

    private void requestFileAccess() {
        if (Build.VERSION.SDK_INT >= 30) {
            Intent intent = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                    Uri.parse("package:" + getPackageName()));
            try { startActivity(intent); }
            catch (Exception ex) {
                startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
            }
        } else {
            requestPermissions(new String[]{Manifest.permission.READ_EXTERNAL_STORAGE},
                    REQUEST_READ_FILES);
        }
    }

    void showApkInstallHelp() {
        boolean needsSourcePermission = Build.VERSION.SDK_INT >= 26
                && !getPackageManager().canRequestPackageInstalls();
        String explanation = needsSourcePermission
                ? "Android konnte das Update nicht öffnen. Erlaube Face Manager in den Einstellungen, unbekannte Apps zu installieren. Kehre danach zurück und tippe erneut auf Installieren."
                : "Android konnte das Update nicht öffnen. Prüfe die APK-Datei und versuche die Installation erneut.";
        new AlertDialog.Builder(this)
                .setTitle("Update nicht geöffnet")
                .setMessage(explanation)
                .setNegativeButton("Schließen", null)
                .setPositiveButton(needsSourcePermission ? "Einstellungen öffnen" : "OK",
                        (dialog, which) -> {
                            if (!needsSourcePermission) return;
                            Intent settings = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                    Uri.parse("package:" + getPackageName()));
                            try { startActivity(settings); }
                            catch (Exception ex) {
                                try {
                                    startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                            Uri.parse("package:" + getPackageName())));
                                } catch (Exception ignored) {
                                    Toast.makeText(this, "Öffne die App-Einstellungen von Face Manager manuell.",
                                            Toast.LENGTH_LONG).show();
                                }
                            }
                        })
                .show();
    }

    private void showIntro() {
        if (isFinishing() || isDestroyed()) return;
        root.removeAllViews();
        root.setBackgroundColor(PAPER);
        WindowInsetsControllerCompat bars = new WindowInsetsControllerCompat(getWindow(), root);
        bars.setAppearanceLightStatusBars(true);
        bars.setAppearanceLightNavigationBars(true);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setGravity(Gravity.CENTER);
        page.setPadding(dp(28), dp(24), dp(28), dp(24));
        root.addView(page, new LinearLayout.LayoutParams(-1, -1));
        TextView mark = label("◎", 55, GREEN, true);
        page.addView(mark);
        TextView title = label("Face Manager für dein Telefon", 27, INK, true);
        page.addView(title);
        TextView detail = label("Deine Fotobibliothek wird nur auf diesem Gerät verwaltet und funktioniert offline. Sie ist unabhängig von Face Manager auf deinem PC. Originalfotos bleiben am Speicherort; um Ordner zu durchsuchen und Dateinamen zu ändern, benötigt die App Zugriff auf alle Dateien.", 16, INK, false);
        detail.setLineSpacing(dp(5), 1f);
        LinearLayout.LayoutParams copy = new LinearLayout.LayoutParams(-1, -2);
        copy.topMargin = dp(16);
        page.addView(detail, copy);
        Button grant = action("Dateizugriff erlauben", this::requestFileAccess);
        LinearLayout.LayoutParams grantLp = new LinearLayout.LayoutParams(-1, dp(56));
        grantLp.topMargin = dp(26);
        page.addView(grant, grantLp);
    }

    private void showCurrentState() {
        if (isFinishing() || isDestroyed() || !hasFileAccess()) return;
        if (BackendService.ready()) {
            showWeb(BackendService.port(), BackendService.token());
            return;
        }
        if (web != null) closeWeb();
        root.removeAllViews();
        root.setBackgroundColor(PAPER);
        WindowInsetsControllerCompat bars = new WindowInsetsControllerCompat(getWindow(), root);
        bars.setAppearanceLightStatusBars(true);
        bars.setAppearanceLightNavigationBars(true);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setGravity(Gravity.CENTER);
        page.setPadding(dp(24), dp(24), dp(24), dp(24));
        root.addView(page, new LinearLayout.LayoutParams(-1, -1));
        String error = BackendService.error();
        boolean stopped = error == null && !BackendService.starting();
        if (error == null && !stopped) {
            ProgressBar spinner = new ProgressBar(this);
            page.addView(spinner, new LinearLayout.LayoutParams(dp(42), dp(42)));
        }
        TextView title = label(error != null ? "Start fehlgeschlagen"
                : stopped ? "Bibliothek angehalten" : "Deine Bibliothek startet …", 22, INK, true);
        LinearLayout.LayoutParams titleLp = new LinearLayout.LayoutParams(-1, -2);
        titleLp.topMargin = dp(20);
        page.addView(title, titleLp);
        if (error != null || stopped) {
            TextView detail = label(error == null ? "Du kannst den lokalen Dienst erneut starten."
                    : error, 14, INK, false);
            detail.setPadding(0, dp(10), 0, dp(12));
            page.addView(detail);
            page.addView(action("Erneut versuchen", this::checkAccessAndStart),
                    new LinearLayout.LayoutParams(-1, dp(52)));
        }
    }

    private void showWeb(int port, String token) {
        String newOrigin = "http://127.0.0.1:" + port;
        if (web != null && newOrigin.equals(origin)) return;
        closeWeb();
        origin = newOrigin;
        pageErrorShown = false;
        root.removeAllViews();
        root.setBackgroundColor(Color.rgb(18, 29, 31));
        WindowInsetsControllerCompat bars = new WindowInsetsControllerCompat(getWindow(), root);
        bars.setAppearanceLightStatusBars(false);
        bars.setAppearanceLightNavigationBars(false);
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(18, 29, 31));
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        // The database restore input receives a one-time content URI from the system picker.
        settings.setAllowContentAccess(true);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setSafeBrowsingEnabled(true);
        web.setWebViewClient(new TrustedClient());
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView view,
                    android.webkit.ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                chooserParams = params;
                try { filePicker.launch(params.createIntent()); }
                catch (Exception ex) {
                    fileCallback = null;
                    chooserParams = null;
                    callback.onReceiveValue(null);
                    Toast.makeText(MainActivity.this, "Datei kann nicht ausgewählt werden.",
                            Toast.LENGTH_LONG).show();
                }
                return true;
            }
        });
        web.addJavascriptInterface(new WebExports(), "AndroidExport");
        root.addView(web, new LinearLayout.LayoutParams(-1, 0, 1));
        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.removeAllCookies(removed -> cookies.setCookie(newOrigin,
                "fm_session=" + token + "; Path=/; HttpOnly; SameSite=Strict", accepted -> {
                    if (accepted && web != null && newOrigin.equals(origin)) {
                        cookies.flush();
                        web.loadUrl(newOrigin + "/");
                    } else if (!accepted) {
                        Toast.makeText(this, "Lokale Anmeldung fehlgeschlagen.",
                                Toast.LENGTH_LONG).show();
                    }
                }));
    }

    private void closeWeb() {
        if (web == null) return;
        root.removeView(web);
        web.removeJavascriptInterface("AndroidExport");
        web.stopLoading();
        web.destroy();
        web = null;
        origin = null;
    }

    private boolean isTrusted(Uri uri) {
        if (uri == null || origin == null || !"http".equalsIgnoreCase(uri.getScheme())
                || !"127.0.0.1".equals(uri.getHost())) return false;
        return ("http://127.0.0.1:" + uri.getPort()).equals(origin);
    }

    private final class TrustedClient extends WebViewClient {
        @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            if (isTrusted(request.getUrl())) return false;
            if (request.isForMainFrame()) bridge.openExternalUrl(request.getUrl().toString());
            return true;
        }

        @Override public WebResourceResponse shouldInterceptRequest(WebView view,
                                                                      WebResourceRequest request) {
            Uri uri = request.getUrl();
            if (isTrusted(uri) || "data".equals(uri.getScheme())
                    || "blob".equals(uri.getScheme())) return null;
            return new WebResourceResponse("text/plain", "UTF-8",
                    new ByteArrayInputStream(new byte[0]));
        }

        @Override public void onReceivedError(WebView view, WebResourceRequest request,
                                               WebResourceError error) {
            if (request.isForMainFrame()) showPageError();
        }

        @Override public void onReceivedHttpError(WebView view, WebResourceRequest request,
                                                   WebResourceResponse response) {
            if (request.isForMainFrame() && response.getStatusCode() >= 500) showPageError();
        }

        @Override public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
            closeWeb();
            showCurrentState();
            return true;
        }
    }

    private void showPageError() {
        if (pageErrorShown || isFinishing() || isDestroyed()) return;
        pageErrorShown = true;
        new AlertDialog.Builder(this)
                .setTitle("Bibliothek nicht erreichbar")
                .setMessage("Die lokale Seite konnte nicht geladen werden.")
                .setPositiveButton("Erneut versuchen", (dialog, which) -> {
                    pageErrorShown = false;
                    if (web != null && origin != null) web.loadUrl(origin + "/");
                })
                .setNegativeButton("Schließen", null)
                .show();
    }

    /** JS is injected only into the trusted local page; remote frames and requests are blocked. */
    private final class WebExports {
        @JavascriptInterface public void requestDatabaseExport() {
            main.post(() -> {
                if (web == null || web.getUrl() == null
                        || !isTrusted(Uri.parse(web.getUrl())) || pendingExport != null) return;
                io.execute(() -> {
                    try {
                        String path = Python.getInstance().getModule("android_runtime")
                                .callAttr("export_database").toString();
                        if (!new File(path).isFile()) throw new IOException("Sicherung wurde nicht erstellt");
                        main.post(() -> {
                            if (isFinishing() || isDestroyed()) {
                                new File(path).delete();
                                return;
                            }
                            pendingExport = path;
                            Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT);
                            save.setType("application/octet-stream");
                            save.addCategory(Intent.CATEGORY_OPENABLE);
                            save.putExtra(Intent.EXTRA_TITLE, "face-manager-database.sqlite");
                            exportPicker.launch(save);
                        });
                    } catch (Exception ex) {
                        main.post(() -> Toast.makeText(MainActivity.this,
                                "Sicherung fehlgeschlagen: " + ex.getMessage(),
                                Toast.LENGTH_LONG).show());
                    }
                });
            });
        }
    }

    private void writeSnapshot(String path, Uri destination) {
        try (FileInputStream source = new FileInputStream(path);
             OutputStream target = getContentResolver().openOutputStream(destination, "w")) {
            if (target == null) throw new IOException("Zieldatei nicht verfügbar");
            byte[] chunk = new byte[64 * 1024];
            int count;
            while ((count = source.read(chunk)) != -1) target.write(chunk, 0, count);
            main.post(() -> Toast.makeText(this, "Sicherung gespeichert", Toast.LENGTH_SHORT).show());
        } catch (Exception ex) {
            main.post(() -> Toast.makeText(this, "Sicherung fehlgeschlagen: " + ex.getMessage(),
                    Toast.LENGTH_LONG).show());
        } finally {
            new File(path).delete();
        }
    }

    void pickDirectory(String initialPath, CompletableFuture<String> result) {
        if (!hasFileAccess()) { result.complete(null); return; }
        if (folderChoice != null && !folderChoice.isDone()) {
            result.complete(null);
            return;
        }
        File rootDirectory = Environment.getExternalStorageDirectory();
        File start = initialPath == null ? rootDirectory : new File(initialPath);
        try {
            File canonicalRoot = rootDirectory.getCanonicalFile();
            File canonicalStart = start.getCanonicalFile();
            if (!canonicalStart.isDirectory() || !inside(canonicalStart, canonicalRoot)) {
                canonicalStart = canonicalRoot;
            }
            folderChoice = result;
            showDirectory(canonicalStart, canonicalRoot, result);
        } catch (IOException ex) {
            result.complete(null);
        }
    }

    private void showDirectory(File folder, File boundary, CompletableFuture<String> result) {
        if (result.isDone() || isFinishing() || isDestroyed()) { result.complete(null); return; }
        File[] raw = folder.listFiles(File::isDirectory);
        File[] folders = raw == null ? new File[0] : raw;
        Arrays.sort(folders, Comparator.comparing(File::getName, String.CASE_INSENSITIVE_ORDER));
        ArrayList<String> labels = new ArrayList<>();
        if (!folder.equals(boundary)) labels.add("↑ Übergeordneter Ordner");
        for (File child : folders) labels.add(child.getName());
        int offset = folder.equals(boundary) ? 0 : 1;
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Ordner wählen · " + folder.getAbsolutePath())
                .setItems(labels.toArray(new String[0]), (whichDialog, which) -> {
                    File next = which < offset ? folder.getParentFile() : folders[which - offset];
                    try {
                        File canonical = next.getCanonicalFile();
                        if (inside(canonical, boundary)) showDirectory(canonical, boundary, result);
                    } catch (IOException ex) { result.complete(null); }
                })
                .setNegativeButton("Abbrechen", (d, which) -> result.complete(null))
                .setPositiveButton("Diesen Ordner wählen", (d, which) ->
                        result.complete(folder.getAbsolutePath()))
                .create();
        dialog.setOnCancelListener(d -> result.complete(null));
        dialog.show();
    }

    private static boolean inside(File candidate, File boundary) {
        return candidate.equals(boundary)
                || candidate.getPath().startsWith(boundary.getPath() + File.separator);
    }

    /** Last-resort local location view when the phone has no folder-capable file manager. */
    void showFileLocation(File selected) {
        File folder = selected.isDirectory() ? selected : selected.getParentFile();
        if (folder == null || !folder.isDirectory()) {
            Toast.makeText(this, "Speicherort nicht verfügbar", Toast.LENGTH_LONG).show();
            return;
        }
        File[] raw = folder.listFiles();
        File[] files = raw == null ? new File[0] : raw;
        Arrays.sort(files, Comparator.comparing(File::getName, String.CASE_INSENSITIVE_ORDER));
        String[] labels = new String[files.length];
        for (int i = 0; i < files.length; i++) {
            labels[i] = (files[i].equals(selected) ? "▶ " : "")
                    + (files[i].isDirectory() ? "▣ " : "") + files[i].getName();
        }
        new AlertDialog.Builder(this)
                .setTitle(folder.getAbsolutePath())
                .setItems(labels, (dialog, which) -> {
                    if (files[which].isDirectory()) showFileLocation(files[which]);
                    else if (files[which].equals(selected)) bridge.openFileDirect(
                            files[which].getAbsolutePath());
                })
                .setPositiveButton("Schließen", null)
                .show();
    }

    private Button action(String text, Runnable click) {
        Button button = new Button(this);
        button.setText(text);
        button.setAllCaps(false);
        button.setMinHeight(dp(48));
        button.setOnClickListener(v -> click.run());
        return button;
    }

    private TextView label(String value, int sp, int color, boolean bold) {
        TextView text = new TextView(this);
        text.setText(value);
        text.setTextSize(sp);
        text.setTextColor(color);
        if (bold) text.setTypeface(null, Typeface.BOLD);
        return text;
    }

    private int dp(float value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }
}
