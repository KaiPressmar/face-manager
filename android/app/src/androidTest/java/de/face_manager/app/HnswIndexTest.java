package de.face_manager.app;

import static org.junit.Assert.*;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public class HnswIndexTest {
    @Test public void cosineLabelsUpdatesAndValidationMatchDesktop() {
        try (HnswIndex index = new HnswIndex(3, 4, 100, 16, 17)) {
            index.add(new float[]{2, 0, 0, 0, 3, 0, -1, 0, 0}, new long[]{4000000000L, 7, 99});
            index.setEf(100);
            HnswIndex.Result found = index.query(new float[]{1, 0, 0}, 3);
            assertArrayEquals(new long[]{4000000000L, 7, 99}, found.labels);
            assertArrayEquals(new float[]{0, 1, 2}, found.distances, 1e-6f);
            index.add(new float[]{0, 0, 2}, new long[]{7});
            assertEquals(3, index.size());
            assertEquals(7, index.query(new float[]{0, 0, 1}, 1).labels[0]);
            assertThrows(IllegalArgumentException.class, () -> index.query(new float[]{1, 0, 0}, 4));
            assertThrows(IllegalArgumentException.class, () -> index.add(new float[]{Float.NaN, 0, 0}, new long[]{8}));
            assertThrows(IllegalArgumentException.class, () -> index.add(new float[]{1, 0}, new long[]{8}));
        }
    }

    @Test public void growsAndClosesDeterministically() {
        HnswIndex index = new HnswIndex(3, 1100, 100, 16, 17);
        float[] vectors = new float[1100 * 3];
        long[] labels = new long[1100];
        for (int i = 0; i < 1100; i++) {
            vectors[i * 3] = (float) Math.cos(i * 0.005);
            vectors[i * 3 + 1] = (float) Math.sin(i * 0.005);
            labels[i] = i;
        }
        index.add(vectors, labels);
        assertEquals(1100, index.size());
        assertEquals(1000, index.query(new float[]{vectors[3000], vectors[3001], 0}, 1).labels[0]);
        assertThrows(IllegalArgumentException.class, () -> index.add(new float[]{0, 0, 1}, new long[]{1100}));
        index.close();
        index.close();
        assertThrows(IllegalStateException.class, () -> index.query(new float[]{1, 0, 0}, 1));
    }
}
