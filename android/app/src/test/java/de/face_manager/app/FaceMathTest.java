package de.face_manager.app;

import static org.junit.Assert.*;
import org.junit.Test;

public class FaceMathTest {
    @Test public void normalizedVectorsHaveUnitLength() {
        float[] result = FaceMath.normalized(new float[]{3, 4});
        assertEquals(1f, FaceMath.cosine(result, result), 0.00001f);
        assertEquals(0.6f, result[0], 0.00001f);
    }

    @Test public void cosineSeparatesDifferentDirections() {
        assertEquals(0f, FaceMath.cosine(new float[]{1, 0}, new float[]{0, 1}), 0.00001f);
        assertEquals(-1f, FaceMath.cosine(new float[]{1, 0}, new float[]{-1, 0}), 0.00001f);
    }

    @Test(expected = IllegalArgumentException.class) public void rejectsInvalidVector() {
        FaceMath.normalized(new float[]{Float.NaN, 1});
    }
}
