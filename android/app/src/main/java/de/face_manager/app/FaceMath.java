package de.face_manager.app;

/** Small, deterministic operations on face embeddings. */
public final class FaceMath {
    private FaceMath() {}

    public static float[] normalized(float[] values) {
        if (values == null || values.length == 0) throw new IllegalArgumentException("Empty embedding");
        double sum = 0;
        for (float value : values) {
            if (!Float.isFinite(value)) throw new IllegalArgumentException("Invalid embedding");
            sum += (double) value * value;
        }
        if (sum < 1e-20) throw new IllegalArgumentException("Zero embedding");
        float scale = (float) (1.0 / Math.sqrt(sum));
        float[] result = new float[values.length];
        for (int i = 0; i < values.length; i++) result[i] = values[i] * scale;
        return result;
    }

    public static float cosine(float[] left, float[] right) {
        if (left == null || right == null || left.length != right.length || left.length == 0)
            throw new IllegalArgumentException("Embedding dimensions differ");
        double dot = 0, a = 0, b = 0;
        for (int i = 0; i < left.length; i++) {
            dot += (double) left[i] * right[i];
            a += (double) left[i] * left[i];
            b += (double) right[i] * right[i];
        }
        if (a < 1e-20 || b < 1e-20) return 0;
        return (float) (dot / Math.sqrt(a * b));
    }
}
