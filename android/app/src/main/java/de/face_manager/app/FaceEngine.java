package de.face_manager.app;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.RectF;
import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtException;
import ai.onnxruntime.OrtSession;
import org.opencv.android.Utils;
import org.opencv.android.OpenCVLoader;
import org.opencv.core.CvType;
import org.opencv.core.Mat;
import org.opencv.core.Scalar;
import org.opencv.core.Size;
import org.opencv.imgproc.Imgproc;
import org.opencv.objdetect.FaceDetectorYN;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.FloatBuffer;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Offline YuNet detection and the desktop buffalo_l ArcFace 512D recognizer. */
public final class FaceEngine implements AutoCloseable {
    private static final int MAX_DETECTION_SIDE = 1024;
    private static FaceEngine instance;
    private final FaceDetectorYN detector;
    private final OrtEnvironment environment;
    private final OrtSession recognizer;
    private final String inputName;
    private boolean closed;
    private static final double[][] TEMPLATE = {
        {38.2946, 51.6963}, {73.5318, 51.5014}, {56.0252, 71.7366},
        {41.5493, 92.3655}, {70.7299, 92.2041}
    };

    public static final class Detection {
        public final RectF bounds;
        public final float[] embedding;
        Detection(RectF bounds, float[] embedding) {
            this.bounds = bounds;
            this.embedding = embedding;
        }
    }

    public static synchronized void initialize(Context context) throws IOException {
        if (instance == null) instance = new FaceEngine(context.getApplicationContext());
    }

    public static synchronized FaceEngine getInstance() {
        if (instance == null) throw new IllegalStateException("Initialize the Android face engine before starting the backend");
        return instance;
    }

    public FaceEngine(Context context) throws IOException {
        if (!OpenCVLoader.initLocal()) throw new IOException("OpenCV failed to initialize");
        String detectionModel = copyModel(context, "face_detection_yunet_2023mar.onnx");
        String recognitionModel = copyModel(context, "w600k_r50.onnx");
        detector = FaceDetectorYN.create(detectionModel, "", new Size(320, 320), 0.8f);
        environment = OrtEnvironment.getEnvironment();
        try (OrtSession.SessionOptions options = new OrtSession.SessionOptions()) {
            options.setIntraOpNumThreads(Math.min(4, Runtime.getRuntime().availableProcessors()));
            options.setInterOpNumThreads(1);
            options.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT);
            recognizer = environment.createSession(recognitionModel, options);
            inputName = recognizer.getInputNames().iterator().next();
        } catch (OrtException e) {
            throw new IOException("Cannot load the bundled ArcFace recognition model", e);
        }
    }

    private static synchronized String copyModel(Context context, String name) throws IOException {
        File folder = new File(context.getFilesDir(), "models");
        if (!folder.isDirectory() && !folder.mkdirs()) throw new IOException("Cannot create model directory");
        File target = new File(folder, name);
        long expectedLength;
        try (android.content.res.AssetFileDescriptor descriptor = context.getAssets().openFd("models/" + name)) {
            expectedLength = descriptor.getLength();
        }
        if (target.length() == expectedLength && expectedLength > 0) return target.getAbsolutePath();
        File temp = new File(folder, name + ".tmp");
        try (InputStream in = context.getAssets().open("models/" + name);
             FileOutputStream out = new FileOutputStream(temp)) {
            byte[] buffer = new byte[65536];
            int count;
            while ((count = in.read(buffer)) != -1) out.write(buffer, 0, count);
            out.getFD().sync();
        }
        if (temp.length() != expectedLength || !temp.renameTo(target))
            throw new IOException("Cannot install model " + name);
        return target.getAbsolutePath();
    }

    public synchronized List<Detection> detectRgb(byte[] pixels, int width, int height) {
        if (width <= 0 || height <= 0 || (long) width * height * 3 != pixels.length)
            throw new IllegalArgumentException("RGB buffer dimensions do not match");
        Mat rgb = new Mat(height, width, CvType.CV_8UC3);
        Mat bgr = new Mat();
        try {
            rgb.put(0, 0, pixels);
            Imgproc.cvtColor(rgb, bgr, Imgproc.COLOR_RGB2BGR);
            return detectBgr(bgr);
        } finally { rgb.release(); bgr.release(); }
    }

    public synchronized List<Detection> detect(Bitmap upright) {
        Mat rgba = new Mat();
        Mat bgr = new Mat();
        try {
            Utils.bitmapToMat(upright, rgba);
            Imgproc.cvtColor(rgba, bgr, Imgproc.COLOR_RGBA2BGR);
            return detectBgr(bgr);
        } finally { rgba.release(); bgr.release(); }
    }

    private List<Detection> detectBgr(Mat bgr) {
        if (closed) throw new IllegalStateException("Face engine is closed");
        Mat scaled = new Mat();
        Mat faces = new Mat();
        List<Detection> result = new ArrayList<>();
        double scale = Math.min(1.0, (double) MAX_DETECTION_SIDE / Math.max(bgr.cols(), bgr.rows()));
        try {
            int width = Math.max(1, (int) Math.round(bgr.cols() * scale));
            int height = Math.max(1, (int) Math.round(bgr.rows() * scale));
            Imgproc.resize(bgr, scaled, new Size(width, height));
            double sx = (double) bgr.cols() / width, sy = (double) bgr.rows() / height;
            detector.setInputSize(scaled.size());
            detector.detect(scaled, faces);
            for (int i = 0; i < faces.rows(); i++) {
                float[] row = new float[15];
                if (faces.get(i, 0, row) < row.length) continue;
                for (int j = 0; j < 14; j += 2) { row[j] *= sx; row[j + 1] *= sy; }
                Mat transform = alignment(row);
                Mat aligned = new Mat();
                try {
                    Imgproc.warpAffine(bgr, aligned, transform, new Size(112, 112),
                            Imgproc.INTER_LINEAR, org.opencv.core.Core.BORDER_CONSTANT, new Scalar(0));
                    RectF bounds = new RectF(Math.max(0, row[0]), Math.max(0, row[1]),
                            Math.min(bgr.cols(), row[0] + row[2]), Math.min(bgr.rows(), row[1] + row[3]));
                    if (bounds.width() > 0 && bounds.height() > 0)
                        result.add(new Detection(bounds, embedding(aligned)));
                } finally { transform.release(); aligned.release(); }
            }
            return result;
        } catch (OrtException e) {
            throw new IllegalStateException("ArcFace recognition failed", e);
        } finally { scaled.release(); faces.release(); }
    }

    /** Least-squares similarity transform matching InsightFace's five-point alignment. */
    static Mat alignment(float[] face) {
        double sourceX = 0, sourceY = 0, targetX = 0, targetY = 0;
        for (int i = 0; i < 5; i++) {
            sourceX += face[4 + i * 2] / 5.0;
            sourceY += face[5 + i * 2] / 5.0;
            targetX += TEMPLATE[i][0] / 5.0;
            targetY += TEMPLATE[i][1] / 5.0;
        }
        double denominator = 0, a = 0, b = 0;
        for (int i = 0; i < 5; i++) {
            double x = face[4 + i * 2] - sourceX, y = face[5 + i * 2] - sourceY;
            double u = TEMPLATE[i][0] - targetX, v = TEMPLATE[i][1] - targetY;
            denominator += x * x + y * y;
            a += x * u + y * v;
            b += x * v - y * u;
        }
        if (denominator < 1e-10) throw new IllegalArgumentException("Degenerate facial landmarks");
        a /= denominator; b /= denominator;
        Mat matrix = new Mat(2, 3, CvType.CV_64FC1);
        matrix.put(0, 0, a, -b, targetX - a * sourceX + b * sourceY,
                b, a, targetY - b * sourceX - a * sourceY);
        return matrix;
    }

    private float[] embedding(Mat alignedBgr) throws OrtException {
        byte[] pixels = new byte[112 * 112 * 3];
        alignedBgr.get(0, 0, pixels);
        float[] nchw = new float[pixels.length];
        // Preserve the desktop pipeline's historical channel order: it passes RGB
        // to InsightFace, whose swapRB blob builder produces BGR-order tensors.
        // Correcting only Android to RGB would make existing database vectors differ.
        for (int p = 0; p < 112 * 112; p++)
            for (int c = 0; c < 3; c++)
                nchw[c * 112 * 112 + p] = ((pixels[p * 3 + c] & 255) - 127.5f) / 127.5f;
        try (OnnxTensor tensor = OnnxTensor.createTensor(environment, FloatBuffer.wrap(nchw), new long[]{1, 3, 112, 112});
             OrtSession.Result output = recognizer.run(Collections.singletonMap(inputName, tensor))) {
            float[][] feature = (float[][]) output.get(0).getValue();
            if (feature.length != 1 || feature[0].length != 512)
                throw new IllegalStateException("ArcFace must return a 512-dimensional embedding");
            return FaceMath.normalized(feature[0]);
        }
    }

    @Override public synchronized void close() {
        if (!closed) {
            closed = true;
            try { recognizer.close(); }
            catch (OrtException e) { throw new IllegalStateException("Cannot close ArcFace session", e); }
        }
    }
}
