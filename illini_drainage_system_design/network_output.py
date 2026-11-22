"""Shared with Illini Subsurface Drain Layout: DEM reading, map units, and the
style and fields of the Illini drainage network layer."""
import numpy as np
from qgis.core import (
    QgsField,
    QgsFields,
    QgsLineSymbol,
    QgsProcessingLayerPostProcessorInterface,
    QgsRectangle,
    QgsRuleBasedRenderer,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant

Z_FACTORS = [1.0, 0.3048, 1200.0 / 3937.0]


def _meters_factor(crs):
    """Map units -> metres."""
    try:
        from qgis.core import Qgis
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), Qgis.DistanceUnit.Meters)
    except (ImportError, AttributeError):
        return QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), QgsUnitTypes.DistanceMeters)


def _read_dem(layer, bounds, cell_map, nrows, ncols, feedback):
    """Resample the DEM onto the analysis grid (bilinear). Returns (array, valid)."""
    xmin, ymin, xmax, ymax = bounds
    arr = None
    if layer.providerType() == "gdal":
        try:
            from osgeo import gdal
            src = layer.dataProvider().dataSourceUri()
            tmp = "/vsimem/system_design_dem.tif"
            ds = gdal.Warp(tmp, src, format="GTiff",
                           outputBounds=(xmin, ymin, xmax, ymax),
                           width=ncols, height=nrows, resampleAlg="bilinear",
                           dstNodata=-99999.0, outputType=gdal.GDT_Float32)
            if ds is not None:
                arr = ds.GetRasterBand(1).ReadAsArray().astype(float)
                ds = None
            gdal.Unlink(tmp)
        except Exception as e:  # noqa: BLE001
            feedback.pushWarning("GDAL resampling failed ({}); falling back to "
                                 "nearest-neighbour reading.".format(e))
            arr = None
        if arr is not None:
            valid = np.isfinite(arr) & (arr > -99998.0)
            return arr, valid
    prov = layer.dataProvider()
    block = prov.block(1, QgsRectangle(xmin, ymin, xmax, ymax), ncols, nrows)
    arr = np.empty((nrows, ncols))
    valid = np.ones((nrows, ncols), dtype=bool)
    for i in range(nrows):
        for j in range(ncols):
            arr[i, j] = block.value(i, j)
            if block.isNoData(i, j):
                valid[i, j] = False
    return arr, valid & np.isfinite(arr)


class _Styler(QgsProcessingLayerPostProcessorInterface):
    """Style the network: mains and submains red, laterals blue, layout problems (flag)
    orange, grade problems (BURY_CHECK) purple. Depth problems are shown by the Tile
    elevation raster's Tile depth check layer."""

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsVectorLayer):
            return
        root = QgsRuleBasedRenderer.Rule(None)
        styles = [
            ("main", "185,28,28", "1.0", "Main"),
            ("submain", "185,28,28", "0.6", "Submain"),
            ("lateral", "37,99,235", "0.35", "Lateral"),
        ]
        for kind, color, width, label in styles:
            sym = QgsLineSymbol.createSimple({"color": color, "width": width})
            root.appendChild(QgsRuleBasedRenderer.Rule(
                sym, 0, 0, "\"kind\" = '{}' AND (\"flag\" IS NULL OR \"flag\" = '') AND NOT "
                "(\"BURY_CHECK\" ILIKE '%grade%' OR \"BURY_CHECK\" LIKE '%Not connected%')"
                .format(kind), label))
        flagged = QgsLineSymbol.createSimple({"color": "217,119,6", "width": "0.5"})
        root.appendChild(QgsRuleBasedRenderer.Rule(
            flagged, 0, 0, "\"flag\" <> ''", "Layout problem (see flag)"))
        grade = QgsLineSymbol.createSimple({"color": "147,51,234", "width": "0.6"})
        root.appendChild(QgsRuleBasedRenderer.Rule(
            grade, 0, 0, "(\"flag\" IS NULL OR \"flag\" = '') AND "
            "(\"BURY_CHECK\" ILIKE '%grade%' OR \"BURY_CHECK\" LIKE '%Not connected%')",
            "Grade problem (see BURY_CHECK)"))
        layer.setRenderer(QgsRuleBasedRenderer(root))
        layer.triggerRepaint()


def network_fields():
    """The fields of the Illini drainage network (Illini Subsurface Drain Layout)."""
    D, I, S = QVariant.Double, QVariant.Int, QVariant.String
    spec = [
        ("id", I), ("pipe_id", I), ("kind", S), ("pipe", S), ("from_node", I), ("to_node", I),
        ("ds_link", I), ("length", D), ("TileLength", I), ("ground_us", D), ("ground_ds", D),
        ("terrain_grade_pct", D), ("invert_us", D), ("invert_ds", D),
        ("design_grade_pct", D), ("max_depth", D), ("min_depth", D), ("depth_ds", D),
        ("upstream_length", D), ("drained_area", D),
        # Routine J (Illini Drainage Tools) fields: 1 = first vertex (upstream end)
        ("ElevDepth1", D), ("BuryDepth1", D), ("ElevDepth2", D), ("BuryDepth2", D),
        ("InSlope", D), ("OutSlope", D), ("BURY_CHECK", S),
        # Routine K (Illini Drainage Tools) fields, in Routine K's US units
        ("TILE_ORDER", I), ("FLOW_LENGTH", D), ("SIZE_SLOPE", D), ("D_COEFF", D),
        ("DRAIN_SPACE", I),
        ("D_AREA", D), ("CUM_SEG_FLOW", D), ("ACTUAL_SIZE", D), ("NOMINAL", I),
        ("PIPE_TYPE", S),
        ("flag", S)]
    fs = QgsFields()
    for name, typ in spec:
        fs.append(QgsField(name, typ))
    return fs


def network_attrs(it, uf):
    """Layer attributes of one engine network link, in design units (uf m per unit)."""
    import math
    lengths = ("length_m", "ground_us", "ground_ds", "invert_us", "invert_ds",
               "max_depth_m", "min_depth_m", "depth_ds_m")
    rename = {"length_m": "length", "max_depth_m": "max_depth", "min_depth_m": "min_depth",
              "depth_ds_m": "depth_ds"}
    a = dict(it["attrs"])
    for k in lengths:
        a[k] = round(it["attrs"][k] / uf, 3)
    a = {rename.get(k, k): v for k, v in a.items()}
    area_f = 1.0 if uf == 1.0 else 2.4710538     # ha, or acres in feet
    a["upstream_length"] = round(it["attrs"]["upstream_m"] / uf, 1)
    a["drained_area"] = round(it["attrs"]["drained_ha"] * area_f, 3)
    step = 5.0 if uf != 1.0 else 1.0             # nearest 5 ft, or the nearest metre
    a["TileLength"] = int(math.floor(a["length"] / step + 0.5) * step)
    a["ElevDepth1"] = a["invert_us"]
    a["BuryDepth1"] = round(a["ground_us"] - a["invert_us"], 3)
    a["ElevDepth2"] = a["invert_ds"]
    a["BuryDepth2"] = round(a["ground_ds"] - a["invert_ds"], 3)
    a["InSlope"] = it["attrs"]["in_slope"]
    a["OutSlope"] = it["attrs"]["out_slope"]
    a["BURY_CHECK"] = it["attrs"]["bury_check"]
    return a
