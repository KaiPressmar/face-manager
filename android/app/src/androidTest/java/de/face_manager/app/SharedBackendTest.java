package de.face_manager.app;

import static org.junit.Assert.assertTrue;

import android.content.Context;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.junit.Test;
import org.junit.runner.RunWith;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.util.UUID;

/** Runs the packaged Python API and shared backend regressions on the device. */
@RunWith(AndroidJUnit4.class)
public final class SharedBackendTest {
    private static File copyAsset(Context source, String name, File directory) throws Exception {
        File target = new File(directory, name);
        try (InputStream input = source.getAssets().open(name);
             FileOutputStream output = new FileOutputStream(target)) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
        }
        return target;
    }

    @Test public void packagedBackendRunsSharedRegressionsAndAuthenticatedImport() throws Exception {
        Context app = InstrumentationRegistry.getInstrumentation().getTargetContext();
        Context test = InstrumentationRegistry.getInstrumentation().getContext();
        File root = new File(app.getCacheDir(), "backend-test-" + UUID.randomUUID());
        assertTrue(root.mkdirs());
        File grace = copyAsset(test, "grace_hopper.jpg", root);
        File astronaut = copyAsset(test, "astronaut.png", root);
        File data = new File(root, "data");
        assertTrue(data.mkdirs());

        FaceEngine.initialize(app);
        if (!Python.isStarted()) Python.start(new AndroidPlatform(app));
        String report = Python.getInstance().getModule("android_test_support")
                .callAttr("run", app, new AndroidBridge(app), data.getAbsolutePath(),
                        grace.getAbsolutePath(), astronaut.getAbsolutePath()).toString();
        assertTrue(report, report.contains("shared backend tests passed"));
        assertTrue(report, report.contains("HTTP smoke passed"));
    }
}
