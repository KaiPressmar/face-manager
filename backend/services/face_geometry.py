"""Compare face boxes in the same raw image pixel coordinates."""

from math import isfinite
from numbers import Real


def face_boxes_overlap(first, second, *, minimum_iou: float = 0.7) -> bool:
    """Identify the same face despite small differences in box placement."""
    if len(first) != 4 or len(second) != 4 or not all(
        isinstance(value, Real) and isfinite(value) for value in (*first, *second)
    ):
        return False
    x1, y1, w1, h1 = first
    x2, y2, w2, h2 = second
    if min(w1, h1, w2, h2) <= 0:
        return False
    width = min(x1 + w1, x2 + w2) - max(x1, x2)
    height = min(y1 + h1, y2 + h2) - max(y1, y2)
    if width <= 0 or height <= 0:
        return False
    intersection = width * height
    smaller, larger = sorted((w1 * h1, w2 * h2))
    if intersection >= minimum_iou * (smaller + larger - intersection):
        return True
    # A tightly drawn manual box can have low IoU against a larger detector
    # box. Require similar centers as well as containment to distinguish it
    # from another person whose box only overlaps near an edge.
    return (
        intersection >= 0.85 * smaller
        and min(w1, w2) >= 0.6 * max(w1, w2)
        and min(h1, h2) >= 0.6 * max(h1, h2)
        and abs(x1 + w1 / 2 - x2 - w2 / 2) <= 0.15 * min(w1, w2)
        and abs(y1 + h1 / 2 - y2 - h2 / 2) <= 0.15 * min(h1, h2)
    )
