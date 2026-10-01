package de.face_manager.app;

import static org.junit.Assert.assertTrue;

import android.Manifest;
import android.content.Context;
import android.os.Environment;
import android.os.ParcelFileDescriptor;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.junit.Test;
import org.junit.runner.RunWith;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.UUID;

/** Runs the packaged Python API and shared backend regressions on the device. */
@RunWith(AndroidJUnit4.class)
public final class SharedBackendTest {
    private static void grantPublicPhotoAccess(Context app) throws Exception {
        ParcelFileDescriptor output = InstrumentationRegistry.getInstrumentation().getUiAutomation()
                .executeShellCommand("appops set " + app.getPackageName()
                        + " MANAGE_EXTERNAL_STORAGE allow");
        try (InputStream input = new ParcelFileDescriptor.AutoCloseInputStream(output)) {
            byte[] buffer = new byte[256];
            while (input.read(buffer) != -1) { /* Wait for appops to finish. */ }
        }
        InstrumentationRegistry.getInstrumentation().getUiAutomation()
                .grantRuntimePermission(app.getPackageName(), Manifest.permission.ACCESS_MEDIA_LOCATION);
        assertTrue("All-files access is needed to import the public fixtures",
                Environment.isExternalStorageManager());
        assertTrue("GPS EXIF permission is needed for the public fixtures",
                app.checkSelfPermission(Manifest.permission.ACCESS_MEDIA_LOCATION)
                        == android.content.pm.PackageManager.PERMISSION_GRANTED);
    }

    private static void deleteFixtureFolder(File file) throws IOException {
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children == null) throw new IOException("Cannot list test fixture folder " + file);
            for (File child : children) deleteFixtureFolder(child);
        }
        if (file.exists() && !file.delete()) throw new IOException("Cannot delete test fixture " + file);
    }

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
        grantPublicPhotoAccess(app);
        File pictures = new File(Environment.getExternalStorageDirectory(), "Pictures");
        File publicFixtures = new File(pictures, "FaceManager-Test-" + UUID.randomUUID());
        try {
            assertTrue("Cannot create public photo fixture folder", publicFixtures.mkdirs());
            FaceEngine.initialize(app);
            if (!Python.isStarted()) Python.start(new AndroidPlatform(app));
            String report = Python.getInstance().getModule("android_test_support")
                    .callAttr("run", app, new AndroidBridge(app), data.getAbsolutePath(),
                            grace.getAbsolutePath(), astronaut.getAbsolutePath(),
                            publicFixtures.getAbsolutePath()).toString();
            assertTrue(report, report.contains("shared backend tests passed"));
            assertTrue(report, report.contains("HTTP smoke passed"));
        } finally {
            if (publicFixtures.exists()) deleteFixtureFolder(publicFixtures);
        }
    }
}
