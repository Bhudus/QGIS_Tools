# Surface Interpolation - Minimum Curvature / Natural Neighbor (QGIS 3 plugin)

Adds a Processing algorithm that grids scattered point data into a raster using
either **minimum curvature** (with optional tension) or **natural neighbor
(Sibson)** interpolation.

**Processing Toolbox → Surface interpolation → Interpolation → Surface interpolation (minimum curvature / natural neighbor)**

The dialog mirrors the native *IDW interpolation* algorithm:

| Parameter | Notes |
|---|---|
| Input points | Point layer (any CRS) |
| Z value attribute | Numeric field; leave empty to use the Z coordinate of 3D points |
| Interpolation method | Minimum curvature, or Natural neighbor (Sibson) |
| Tension | *Minimum curvature only.* 0 = minimum curvature, 1 = membrane (Laplace) |
| Extent | Optional - defaults to the layer extent |
| Output raster size | Pixel size in layer units; 0 = automatic (~250 cells on the long side) |
| *Advanced:* Maximum iterations, Convergence limit, Relaxation factor | *Minimum curvature only* |
| Interpolated | Output raster (GeoTIFF, or any GDAL format by file extension) |

## Choosing a method

| | Minimum curvature | Natural neighbor |
|---|---|---|
| Character | Global, very smooth spline | Local, weighted average of neighbors |
| Overshoot beyond data range | Possible (reduce with tension) | Never |
| Outside the convex hull of the points | Extrapolated (every cell gets a value) | NoData |
| Honours the data | Within a small tolerance | Exactly |
| Best for | Smooth fields: elevation, potential fields | Irregular or clustered data where artefacts are unacceptable |

## Install
Download [`min_curvature.zip`](https://github.com/Bhudus/QGIS_Tools/raw/main/min_curvature/min_curvature.zip), then in QGIS use
`Plugins → Manage and Install Plugins → Install from ZIP`, choose `min_curvature.zip`, tick the plugin.
Requires QGIS ≥ 3.16. Only numpy and GDAL are required (both ship with QGIS).
Natural neighbor needs a Delaunay triangulation: scipy is used if installed,
otherwise the GEOS triangulation built into QGIS.

## Methods

**Minimum curvature** solves `(1 − T)∇⁴z − T∇²z = 0` on the output grid (13-point
stencil) with successive over-relaxation on a coarse→fine grid hierarchy
(Briggs 1974, Swain 1976, Smith & Wessel 1990).
A best-fit plane is removed first and added back at the end; each data point
constrains its nearest node, corrected with the local gradient (the corrected
values are under-relaxed, and if a grid level ever diverges it is repeated
without the correction, so the solver cannot blow up); points in the
same cell are averaged; edges use linear extrapolation.

**Natural neighbor** computes Sibson weights: the area a new point's Voronoi cell
takes from each neighbor's cell. The computation is vectorised over all cells
(Bowyer-Watson cavity of each cell → stolen-area polygons by the shoelace
formula). Linear surfaces are reproduced exactly. Cells exactly on a data point or in
degenerate (cocircular) configurations fall back to linear interpolation in the
containing triangle. All points of the layer are used - also those outside the
extent, since they influence cells near its edge. Points with identical
coordinates are averaged.

`mincurv_core.py` and `natneigh_core.py` are pure numpy and usable outside QGIS
(`tests/test_core.py`, `tests/test_natneigh.py`).
