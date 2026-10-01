package de.face_manager.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.Manifest;
import android.os.Build;
import android.os.ParcelFileDescriptor;
import android.content.Intent;
import android.webkit.WebView;
import android.view.View;
import android.view.ViewGroup;

import androidx.test.core.app.ActivityScenario;
import androidx.core.content.ContextCompat;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;

import org.junit.Test;
import org.junit.runner.RunWith;

import java.io.FileInputStream;
import java.io.IOException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Device smoke checks: access disclosure and real local React navigation. */
@RunWith(AndroidJUnit4.class)
public final class MainActivityTest {
    @Test public void coldLaunchExplainsLocalLibraryAccess() throws Exception {
        MainActivity.fileAccessOverrideForTest = false;
        try {
            try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
                AtomicReference<String> text = new AtomicReference<>("");
                scenario.onActivity(activity -> text.set(allText(
                        activity.findViewById(android.R.id.content))));
                assertTrue(text.get().contains("unabhängig von Face Manager auf deinem PC"));
                assertTrue(text.get().contains("Dateizugriff erlauben"));
                scenario.onActivity(activity -> assertTrue("Light onboarding needs dark navigation icons",
                        (activity.getWindow().getDecorView().getSystemUiVisibility()
                                & View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR) != 0));
            }
        } finally {
            MainActivity.fileAccessOverrideForTest = null;
        }
    }

    @Test public void grantedAccessOpensAllReactPagesAndZoomsMap() throws Exception {
        grantAllFilesAccess();
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
            String url = waitForWebUrl(scenario);
            assertTrue(url.startsWith("http://127.0.0.1:"));
            assertEquals("Bilder", waitForHeading(scenario, "Bilder"));
            navigate(scenario, "#/weltkarte");
            assertEquals("Fotokarte", waitForHeading(scenario, "Fotokarte"));
            String originalBox = waitForMapBox(scenario, null);
            evaluate(scenario, "document.querySelector('.page-pane--active "
                    + ".gps-map__controls button[aria-label=\"Vergrößern\"]').click()");
            String zoomedBox = waitForMapBox(scenario, originalBox);
            assertTrue("Map zoom did not change its SVG viewport",
                    !originalBox.equals(zoomedBox));
            navigate(scenario, "#/dateinamen");
            assertEquals("Dateinamen", waitForHeading(scenario, "Dateinamen"));
            navigate(scenario, "#/gesichter-pruefen");
            assertEquals("Gesichter prüfen", waitForHeading(scenario, "Gesichter prüfen"));
            navigate(scenario, "#/einstellungen/daten");
            assertEquals("Einstellungen", waitForHeading(scenario, "Einstellungen"));
        }
    }

    @Test public void backendCanStopAndRestartWithFreshSession() throws Exception {
        grantAllFilesAccess();
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
            waitForWebUrl(scenario);
            String oldToken = BackendService.getToken();
            scenario.onActivity(activity -> activity.stopService(
                    new Intent(activity, BackendService.class)));
            for (int i = 0; i < 100 && BackendService.isReady(); i++) Thread.sleep(100);
            assertTrue("Old service did not stop", !BackendService.isReady());
            scenario.onActivity(activity -> ContextCompat.startForegroundService(
                    activity, new Intent(activity, BackendService.class)));
            for (int i = 0; i < 1200; i++) {
                if (BackendService.isReady() && !oldToken.equals(BackendService.getToken())) {
                    assertTrue(waitForWebUrl(scenario).startsWith("http://127.0.0.1:"));
                    return;
                }
                Thread.sleep(100);
            }
            throw new AssertionError("Restart did not create a fresh local session: "
                    + BackendService.error());
        }
    }

    private static String waitForWebUrl(ActivityScenario<MainActivity> scenario) throws Exception {
        AtomicReference<String> found = new AtomicReference<>();
        for (int i = 0; i < 1200; i++) {
            scenario.onActivity(activity -> {
                WebView web = findWeb(activity.findViewById(android.R.id.content));
                found.set(web == null ? null : web.getUrl());
            });
            if (found.get() != null) return found.get();
            Thread.sleep(100);
        }
        throw new AssertionError("Local WebView did not start; backend error: " + BackendService.error());
    }

    private static void navigate(ActivityScenario<MainActivity> scenario, String hash)
            throws Exception {
        evaluate(scenario, "window.location.hash = '" + hash + "'");
    }

    private static String waitForMapBox(ActivityScenario<MainActivity> scenario,
                                        String differentFrom) throws Exception {
        for (int i = 0; i < 200; i++) {
            String value = evaluate(scenario,
                    "document.querySelector('.page-pane--active .gps-map__svg')"
                            + "?.getAttribute('viewBox') || ''");
            if (!value.isEmpty() && (differentFrom == null || !value.equals(differentFrom))) {
                return value;
            }
            Thread.sleep(100);
        }
        throw new AssertionError("Map viewport did not render or change");
    }

    private static String waitForHeading(ActivityScenario<MainActivity> scenario, String expected)
            throws Exception {
        for (int i = 0; i < 200; i++) {
            String result = evaluate(scenario,
                    "document.querySelector('.page-pane--active h1')?.textContent || ''");
            if (expected.equals(result)) return result;
            Thread.sleep(100);
        }
        throw new AssertionError("Expected React heading: " + expected);
    }

    private static String evaluate(ActivityScenario<MainActivity> scenario, String script)
            throws Exception {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        scenario.onActivity(activity -> {
            WebView web = findWeb(activity.findViewById(android.R.id.content));
            if (web == null) { latch.countDown(); return; }
            web.evaluateJavascript(script, value -> {
                result.set(value == null ? "" : value.replace("\"", ""));
                latch.countDown();
            });
        });
        latch.await(2, TimeUnit.SECONDS);
        return result.get();
    }

    private static WebView findWeb(View view) {
        if (view instanceof WebView) return (WebView) view;
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                WebView child = findWeb(group.getChildAt(i));
                if (child != null) return child;
            }
        }
        return null;
    }

    private static String allText(View view) {
        StringBuilder text = new StringBuilder();
        if (view instanceof android.widget.TextView) text.append(
                ((android.widget.TextView) view).getText()).append('\n');
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                text.append(allText(group.getChildAt(i)));
            }
        }
        return text.toString();
    }

    private static void grantAllFilesAccess() throws IOException {
        if (Build.VERSION.SDK_INT >= 33) {
            InstrumentationRegistry.getInstrumentation().getUiAutomation()
                    .grantRuntimePermission("de.face_manager.app", Manifest.permission.POST_NOTIFICATIONS);
        }
        ParcelFileDescriptor output = InstrumentationRegistry.getInstrumentation().getUiAutomation()
                .executeShellCommand("appops set de.face_manager.app MANAGE_EXTERNAL_STORAGE allow");
        try (FileInputStream input = new FileInputStream(output.getFileDescriptor())) {
            byte[] buffer = new byte[256];
            while (input.read(buffer) != -1) { /* wait until the command finishes */ }
        } finally {
            output.close();
        }
    }
}
