# Line Angle (QGIS plugin)

Click a line segment on the map to get its angle in degrees, relative to the
positive horizontal direction.

## Install
1. Download [`line_angle.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/line_angle/line_angle.zip).
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Choose `line_angle.zip`, click **Install Plugin**.
4. A **Line Angle** button appears on the Plugins toolbar (and under Plugins → Line Angle).

## Use
1. Click the Line Angle button.
2. Click on (or near) any line feature in a visible line layer. The active layer is preferred.
3. The nearest segment is highlighted and the message bar shows:
   - **Angle** — 0–360°, counter-clockwise from the positive X axis (east),
     taken in the direction the line was digitized (vertex *i* → *i+1*).
   - **Orientation** — 0–180°, the same line ignoring direction.
   The angle is also copied to the clipboard.

## Two-point tool
Click the **Line Angle – Two Points** button, click a start point, then an end point.
A line follows the cursor and the live angle appears in the status bar; the final angle
(start → end, same 0–360° convention) is shown in the message bar and copied to the clipboard.
It uses the project's snapping settings, so you can snap to vertices or segments.
Right-click or **Esc** cancels a measurement in progress.

## Processing tool: Segment angles
Processing Toolbox → **Line Angle → Segment angles** (also usable in models and batch mode).

- Input: any line layer. Output: one feature per segment, keeping the original attributes plus
  `part`, `seg_index`, the angle field (default `angle`, 0–360°) and optionally the
  orientation field (default `orient`, 0–180°).
- Angles are computed in the input layer's CRS — reproject first if you need another CRS.
- Zero-length segments are skipped by default. Curves are straightened; Z/M are dropped.

## Notes
- Angles are computed in the **map canvas CRS**, so "horizontal" matches what you see.
  In a geographic CRS (degrees) the value is the angle on the plotted map, not a true bearing.
- This is the mathematical convention (0° = east, 90° = north). A compass azimuth
  (0° = north, clockwise) is `(90 − angle) mod 360`.
