"""Tile elevation / Tile depth check raster layers for the network check (QGIS)."""
import os
import tempfile

import numpy as np
from qgis.core import (
    QgsColorRampShader,
    QgsPalettedRasterRenderer,
    QgsRasterLayer,
    QgsRasterShader,
    QgsRectangle,
    QgsSingleBandPseudoColorRenderer,
)
from qgis.PyQt.QtGui import QColor

from .profile_core import DEPTH_DEEP, DEPTH_SHALLOW

NODATA = -9999.0
GROUP = "Tile Profile check"
ELEV_RAMP = ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"]   # viridis


# ---------------------------------------------------------------- DEM as an array
def read_dem(dem, extent, max_cells=60_000_000):
    """The DEM's first band over `extent` (in the DEM's CRS), on the DEM's own grid.

    Returns (array with NaN for no data, (x0, y0, cell_x, cell_y)) where x0, y0
    is the top-left corner."""
    prov = dem.dataProvider()
    full = dem.extent()
    rx, ry = abs(dem.rasterUnitsPerPixelX()), abs(dem.rasterUnitsPerPixelY())
    xmin = max(extent.xMinimum(), full.xMinimum())
    xmax = min(extent.xMaximum(), full.xMaximum())
    ymin = max(extent.yMinimum(), full.yMinimum())
    ymax = min(extent.yMaximum(), full.yMaximum())
    if xmax <= xmin or ymax <= ymin:
        raise ValueError("The DEM does not cover the network.")
    c0 = int(np.floor((xmin - full.xMinimum()) / rx))
    c1 = int(np.ceil((xmax - full.xMinimum()) / rx))
    r0 = int(np.floor((full.yMaximum() - ymax) / ry))
    r1 = int(np.ceil((full.yMaximum() - ymin) / ry))
    ncols, nrows = max(c1 - c0, 1), max(r1 - r0, 1)
    if ncols * nrows > max_cells:
        raise ValueError("The network covers {:,} DEM cells; that is too many to check "
                         "at once.".format(ncols * nrows))
    x0 = full.xMinimum() + c0 * rx
    y0 = full.yMaximum() - r0 * ry
    block = prov.block(1, QgsRectangle(x0, y0 - nrows * ry, x0 + ncols * rx, y0), ncols, nrows)
    try:
        arr = np.array(block.as_numpy(use_masking=False), dtype=float)
    except (AttributeError, TypeError):
        arr = np.array([[block.value(r, c) for c in range(ncols)] for r in range(nrows)],
                       dtype=float)
    if block.hasNoDataValue():
        arr[arr == block.noDataValue()] = np.nan
    try:
        if prov.sourceHasNoDataValue(1):
            arr[arr == prov.sourceNoDataValue(1)] = np.nan
    except AttributeError:
        pass
    return arr, (x0, y0, rx, ry)


def bilinear(arr, grid, x, y):
    """Value of a north-up grid at (x, y) between cell centres; None outside or no data."""
    x0, y0, rx, ry = grid
    fc = (x - x0) / rx - 0.5
    fr = (y0 - y) / ry - 0.5
    nr, nc = arr.shape
    if fc < -0.5 or fr < -0.5 or fc > nc - 0.5 or fr > nr - 0.5:
        return None
    c = min(max(int(np.floor(fc)), 0), nc - 2) if nc > 1 else 0
    r = min(max(int(np.floor(fr)), 0), nr - 2) if nr > 1 else 0
    tc = min(max(fc - c, 0.0), 1.0) if nc > 1 else 0.0
    tr = min(max(fr - r, 0.0), 1.0) if nr > 1 else 0.0
    q = arr[r:r + 2, c:c + 2]
    if q.shape != (2, 2):
        v = arr[min(max(int(round(fr)), 0), nr - 1), min(max(int(round(fc)), 0), nc - 1)]
        return None if np.isnan(v) else float(v)
    if np.isnan(q).any():
        v = arr[min(max(int(round(fr)), 0), nr - 1), min(max(int(round(fc)), 0), nc - 1)]
        return None if np.isnan(v) else float(v)
    top = q[0, 0] * (1 - tc) + q[0, 1] * tc
    bot = q[1, 0] * (1 - tc) + q[1, 1] * tc
    return float(top * (1 - tr) + bot * tr)


# ---------------------------------------------------------------- GeoTIFF
def write_raster(tile, depth, code, grid, wkt, units, path=None):
    """Three-band GeoTIFF: tile elevation, tile depth, depth check (1 ok, 2 shallow, 3 deep)."""
    from osgeo import gdal, osr
    if path is None:
        path = os.path.join(tempfile.mkdtemp(prefix="tile_profile_"), "tile_check.tif")
    x0, y0, rx, ry = grid
    nrows, ncols = tile.shape
    ds = gdal.GetDriverByName("GTiff").Create(path, ncols, nrows, 3, gdal.GDT_Float32,
                                              ["COMPRESS=DEFLATE"])
    ds.SetGeoTransform((x0, rx, 0.0, y0, 0.0, -ry))
    srs = osr.SpatialReference()
    srs.ImportFromWkt(wkt)
    ds.SetProjection(srs.ExportToWkt())
    bands = ((np.where(np.isfinite(tile), tile, NODATA), "Tile elevation ({})".format(units),
              NODATA),
             (np.where(np.isfinite(depth), depth, NODATA), "Tile depth ({})".format(units),
              NODATA),
             (code.astype(float), "Depth check: 1 within limits, 2 shallower than the "
                                  "absolute upper depth, 3 deeper than the absolute lower", 0.0))
    for b, (arr, desc, nd) in enumerate(bands, start=1):
        band = ds.GetRasterBand(b)
        band.WriteArray(arr.astype(np.float32))
        band.SetNoDataValue(nd)
        band.SetDescription(desc)
    ds.FlushCache()
    ds = None
    return path


# ---------------------------------------------------------------- styling
def elevation_renderer(provider, vmin, vmax):
    if not vmax > vmin:
        vmax = vmin + 1.0
    n = len(ELEV_RAMP)
    items = [QgsColorRampShader.ColorRampItem(
        vmin + (vmax - vmin) * k / (n - 1), QColor(c),
        "{:.2f}".format(vmin + (vmax - vmin) * k / (n - 1))) for k, c in enumerate(ELEV_RAMP)]
    fn = QgsColorRampShader(vmin, vmax)
    try:
        from qgis.core import Qgis
        fn.setColorRampType(Qgis.ShaderInterpolationMethod.Linear)
    except (ImportError, AttributeError):
        fn.setColorRampType(QgsColorRampShader.Interpolated)
    fn.setColorRampItemList(items)
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(fn)
    r = QgsSingleBandPseudoColorRenderer(provider, 1, shader)
    r.setClassificationMin(vmin)
    r.setClassificationMax(vmax)
    return r


def check_renderer(provider, shallow_label, deep_label):
    """Band 3: only the cells outside the depth limits are drawn."""
    return QgsPalettedRasterRenderer(provider, 3, [
        QgsPalettedRasterRenderer.Class(DEPTH_SHALLOW, QColor(220, 38, 38), shallow_label),
        QgsPalettedRasterRenderer.Class(DEPTH_DEEP, QColor(147, 51, 234), deep_label)])


def add_layers(project, path, vmin, vmax, shallow_label, deep_label):
    """Add "Tile depth check" above "Tile elevation" in a "Tile Profile check" group at
    the top of the layer tree, replacing the layers of the previous check."""
    root = project.layerTreeRoot()
    old = root.findGroup(GROUP)
    if old is not None:
        ids = [n.layerId() for n in old.findLayers()]
        root.removeChildNode(old)
        project.removeMapLayers(ids)
    group = root.insertGroup(0, GROUP)
    elev = QgsRasterLayer(path, "Tile elevation", "gdal")
    check = QgsRasterLayer(path, "Tile depth check", "gdal")
    if not elev.isValid() or not check.isValid():
        raise ValueError("Could not load the check raster {}.".format(path))
    elev.setRenderer(elevation_renderer(elev.dataProvider(), vmin, vmax))
    check.setRenderer(check_renderer(check.dataProvider(), shallow_label, deep_label))
    for lyr in (check, elev):
        project.addMapLayer(lyr, False)
        group.addLayer(lyr)
    return elev, check
