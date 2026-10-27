"""Pure-math helpers for the Line Angle plugin (no QGIS imports, easy to test)."""
import math


def segment_angle(x1, y1, x2, y2):
    """Angle of the segment (x1, y1) -> (x2, y2) in degrees.

    Measured counter-clockwise from the positive horizontal (east) direction,
    normalised to the range [0, 360).  Returns None for a zero-length segment.
    """
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return None
    angle = math.degrees(math.atan2(dy, dx)) % 360.0
    # Avoid returning 360.0 because of floating point rounding
    return 0.0 if math.isclose(angle, 360.0) else angle


def undirected_angle(angle):
    """Fold a directed angle into [0, 180) - the line's orientation regardless of direction."""
    if angle is None:
        return None
    a = angle % 180.0
    return 0.0 if math.isclose(a, 180.0) else a
