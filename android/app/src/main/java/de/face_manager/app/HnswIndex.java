package de.face_manager.app;

/** Native HNSW cosine index. Calls and disposal are serialized per index. */
public final class HnswIndex implements AutoCloseable {
    static { System.loadLibrary("face_hnsw"); }
    private long handle;
    public HnswIndex(int dimensions, int maxElements, int efConstruction, int m, long seed) {
        handle = create(dimensions, maxElements, efConstruction, m, seed);
    }
    private void requireOpen() {
        if (handle == 0) throw new IllegalStateException("HNSW index is closed");
    }
    public synchronized void setEf(int ef) { requireOpen(); setEfNative(handle, ef); }
    public synchronized void add(float[] values, long[] labels) {
        requireOpen(); addNative(handle, values, labels);
    }
    public synchronized Result query(float[] values, int k) {
        requireOpen(); return queryNative(handle, values, k);
    }
    public synchronized int size() { requireOpen(); return sizeNative(handle); }
    @Override public synchronized void close() {
        if (handle != 0) { destroy(handle); handle = 0; }
    }
    // Python wrappers explicitly close, but collect an abandoned Java proxy too.
    @Override protected void finalize() throws Throwable {
        try { close(); } finally { super.finalize(); }
    }
    public static final class Result {
        public final long[] labels;
        public final float[] distances;
        public Result(long[] labels, float[] distances) {
            this.labels = labels; this.distances = distances;
        }
    }
    private static native long create(int dim, int maximum, int ef, int m, long seed);
    private static native void destroy(long handle);
    private static native void setEfNative(long handle, int ef);
    private static native void addNative(long handle, float[] values, long[] labels);
    private static native Result queryNative(long handle, float[] values, int k);
    private static native int sizeNative(long handle);
}
