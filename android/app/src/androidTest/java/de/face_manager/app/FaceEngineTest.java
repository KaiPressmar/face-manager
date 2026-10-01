package de.face_manager.app;

import static org.junit.Assert.*;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import java.io.InputStream;
import java.util.List;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public class FaceEngineTest {
    private Bitmap asset(String name) throws Exception {
        try (InputStream in = InstrumentationRegistry.getInstrumentation().getContext().getAssets().open(name)) {
            return BitmapFactory.decodeStream(in);
        }
    }


    @Test public void alignmentRecoversKnownSimilarityTransform() throws Exception {
        assertTrue(org.opencv.android.OpenCVLoader.initLocal());
        double[][] template = {{38.2946,51.6963},{73.5318,51.5014},{56.0252,71.7366},
            {41.5493,92.3655},{70.7299,92.2041}};
        float[] face = new float[15];
        for (int i = 0; i < 5; i++) {
            face[4 + 2 * i] = (float) (template[i][0] * 2 + 10);
            face[5 + 2 * i] = (float) (template[i][1] * 2 + 20);
        }
        org.opencv.core.Mat transform = FaceEngine.alignment(face);
        try {
            double[] values = new double[6];
            transform.get(0, 0, values);
            assertArrayEquals(new double[]{0.5, 0, -5, 0, 0.5, -10}, values, 1e-4);
        } finally { transform.release(); }
    }

    @Test public void nativeModelsDetectAndDistinguishRealFaces() throws Exception {
        Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
        FaceEngine engine = new FaceEngine(context);
        Bitmap grace = asset("grace_hopper.jpg");
        Bitmap astronaut = asset("astronaut.png");
        Bitmap resizedGrace = Bitmap.createScaledBitmap(grace, grace.getWidth() * 3 / 4, grace.getHeight() * 3 / 4, true);
        Bitmap blank = Bitmap.createBitmap(512, 512, Bitmap.Config.ARGB_8888);
        try {
            List<FaceEngine.Detection> original = engine.detect(grace);
            List<FaceEngine.Detection> resized = engine.detect(resizedGrace);
            List<FaceEngine.Detection> different = engine.detect(astronaut);
            assertFalse("Grace Hopper face", original.isEmpty());
            assertFalse("resized face", resized.isEmpty());
            assertFalse("astronaut face", different.isEmpty());
            assertTrue("blank image has no face", engine.detect(blank).isEmpty());
            assertEquals(512, original.get(0).embedding.length);
            assertEquals(1f, FaceMath.cosine(original.get(0).embedding, original.get(0).embedding), 1e-5f);
            int[] argb = new int[grace.getWidth() * grace.getHeight()];
            grace.getPixels(argb, 0, grace.getWidth(), 0, 0, grace.getWidth(), grace.getHeight());
            byte[] rgb = new byte[argb.length * 3];
            for (int i = 0; i < argb.length; i++) {
                rgb[i * 3] = (byte) (argb[i] >> 16);
                rgb[i * 3 + 1] = (byte) (argb[i] >> 8);
                rgb[i * 3 + 2] = (byte) argb[i];
            }
            List<FaceEngine.Detection> bridged = engine.detectRgb(rgb, grace.getWidth(), grace.getHeight());
            assertEquals(original.size(), bridged.size());
            assertArrayEquals(original.get(0).embedding, bridged.get(0).embedding, 1e-5f);
            float same = FaceMath.cosine(original.get(0).embedding, resized.get(0).embedding);
            float other = FaceMath.cosine(original.get(0).embedding, different.get(0).embedding);
            assertTrue("same person should score above different people: " + same + " vs " + other, same > other + 0.1f);
        } finally { engine.close(); grace.recycle(); astronaut.recycle(); resizedGrace.recycle(); blank.recycle(); }
    }
}
