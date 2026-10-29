"""Reads a QGIS raster band in strips and feeds it to ExtremeTracker."""

import numpy as np
from qgis.core import Qgis, QgsRectangle

from .extremes import ExtremeTracker, nodata_mask

# Map QGIS raster data types to numpy dtypes
_DTYPES = {
    Qgis.DataType.Byte: np.uint8,
    Qgis.DataType.UInt16: np.uint16,
    Qgis.DataType.Int16: np.int16,
    Qgis.DataType.UInt32: np.uint32,
    Qgis.DataType.Int32: np.int32,
    Qgis.DataType.Float32: np.float32,
    Qgis.DataType.Float64: np.float64,
}
if hasattr(Qgis.DataType, "Int8"):  # QGIS >= 3.30
    _DTYPES[Qgis.DataType.Int8] = np.int8

CELLS_PER_STRIP = 4_000_000  # ~32 MB of float64 per strip


class ExtremesError(Exception):
    pass


def block_to_numpy(block, width, height):
    dtype = _DTYPES.get(block.dataType())
    if dtype is None:
        raise ExtremesError(
            "Unsupported raster data type (complex or ARGB rasters are not handled).")
    buf = bytes(block.data())
    return np.frombuffer(buf, dtype=dtype).reshape(height, width)


def find_extremes(layer, band, max_cells=1000, progress=None, is_canceled=None):
    """Scan one band of a raster layer.

    Returns (tracker, geometry) where geometry is a dict with
    xmin, ymax, xres, yres, width, height.
    """
    provider = layer.dataProvider()
    width, height = layer.width(), layer.height()
    if width <= 0 or height <= 0:
        raise ExtremesError("The raster has no cells.")

    ext = provider.extent()
    xres = ext.width() / width
    yres = ext.height() / height

    # User-defined NoData ranges (Layer Properties > Transparency)
    ranges = []
    for r in provider.userNoDataValues(band):
        ranges.append((r.min(), r.max(), r.bounds() in (0, 2), r.bounds() in (0, 1))
                      if hasattr(r, "bounds") else (r.min(), r.max(), True, True))

    tracker = ExtremeTracker(max_cells=max_cells)
    strip = max(1, CELLS_PER_STRIP // width)

    for r0 in range(0, height, strip):
        if is_canceled and is_canceled():
            return None, None
        r1 = min(height, r0 + strip)
        rows = r1 - r0
        rect = QgsRectangle(ext.xMinimum(), ext.yMaximum() - r1 * yres,
                            ext.xMaximum(), ext.yMaximum() - r0 * yres)
        block = provider.block(band, rect, width, rows)
        if block is None or not block.isValid():
            raise ExtremesError(f"Could not read rows {r0}-{r1}.")

        arr = block_to_numpy(block, width, rows)
        nd = block.noDataValue() if block.hasNoDataValue() else None
        tracker.update(arr, r0, nodata_mask(arr, nd, ranges))

        if progress:
            progress(int(100 * r1 / height))

    geom = dict(xmin=ext.xMinimum(), ymax=ext.yMaximum(),
                xres=xres, yres=yres, width=width, height=height)
    return tracker, geom
