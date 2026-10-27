# -*- coding: utf-8 -*-
"""
Surface interpolation - Processing algorithm (minimum curvature / natural neighbor).

The parameter layout intentionally follows the native "IDW interpolation"
algorithm (input points + attribute, method, extent, output raster size,
output raster), with the solver settings tucked away under "Advanced
parameters".
"""
import math
import os

import numpy as np

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.core import (
    Qgis,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsRasterFileWriter,
    QgsRectangle,
    QgsWkbTypes,
)

from . import mincurv_core, natneigh_core


# ---------------------------------------------------------------------------
# Enum compatibility: new-style names (QGIS >= 3.36) with a fallback to the
# old-style names used by earlier 3.x releases.
# ---------------------------------------------------------------------------
def _pick(*getters):
    for getter in getters:
        try:
            return getter()
        except AttributeError:
            continue
    raise AttributeError('No compatible QGIS enum found')


_FLAG_ADVANCED = _pick(
    lambda: Qgis.ProcessingParameterFlag.Advanced,
    lambda: QgsProcessingParameterDefinition.FlagAdvanced)
_NUM_DOUBLE = _pick(
    lambda: Qgis.ProcessingNumberParameterType.Double,
    lambda: QgsProcessingParameterNumber.Double)
_NUM_INTEGER = _pick(
    lambda: Qgis.ProcessingNumberParameterType.Integer,
    lambda: QgsProcessingParameterNumber.Integer)
_TYPE_POINT = _pick(
    lambda: Qgis.ProcessingSourceType.VectorPoint,
    lambda: QgsProcessing.TypeVectorPoint)
_FIELD_NUMERIC = _pick(
    lambda: Qgis.ProcessingFieldParameterDataType.Numeric,
    lambda: QgsProcessingParameterField.Numeric)

NODATA = -9999.0
MAX_CELLS = 50000000

METHOD_MINCURV = 0
METHOD_NATNEIGH = 1


class MinCurvatureAlgorithm(QgsProcessingAlgorithm):

    INPUT = 'INPUT'
    Z_FIELD = 'Z_FIELD'
    METHOD = 'METHOD'
    TENSION = 'TENSION'
    EXTENT = 'EXTENT'
    PIXEL_SIZE = 'PIXEL_SIZE'
    MAX_ITERATIONS = 'MAX_ITERATIONS'
    TOLERANCE = 'TOLERANCE'
    RELAXATION = 'RELAXATION'
    OUTPUT = 'OUTPUT'

    # -- boilerplate --------------------------------------------------------
    def tr(self, string):
        return QCoreApplication.translate('MinCurvatureAlgorithm', string)

    def createInstance(self):  # noqa: N802
        return MinCurvatureAlgorithm()

    def name(self):
        # kept from version 1.0 so that saved models and scripts keep working
        return 'minimumcurvature'

    def displayName(self):  # noqa: N802
        return self.tr('Surface interpolation (minimum curvature / natural neighbor)')

    def group(self):
        return self.tr('Interpolation')

    def groupId(self):  # noqa: N802
        return 'interpolation'

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), 'icon.svg'))

    def shortDescription(self):  # noqa: N802
        return self.tr('Interpolates point data to a raster using either '
                       'minimum curvature or natural neighbor (Sibson) '
                       'interpolation.')

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            '<p>Creates a raster surface from point data. Choose the '
            '<b>interpolation method</b>:</p>'
            '<p><b>Minimum curvature</b> - the smoothest surface (lowest '
            'total squared curvature) that passes through the data. An '
            'optional <b>tension</b> blends in a membrane term to suppress '
            'overshoots between widely spaced points. The surface satisfies '
            '<i>(1 - T)&nabla;<sup>4</sup>z - T&nabla;<sup>2</sup>z = 0</i> '
            'and is solved on the output grid by successive over-relaxation '
            'on a coarse-to-fine grid hierarchy (Briggs 1974; Swain 1976; '
            'Smith &amp; Wessel 1990). A best-fit plane is removed before '
            'solving and added back afterwards. Every cell of the extent '
            'receives a value (the surface is extrapolated).</p>'
            '<p><b>Natural neighbor</b> - Sibson interpolation: each cell is '
            'a weighted average of its natural neighbors, the weights being '
            'the area the cell would take over from each neighbor\'s '
            'Voronoi region. The surface is local, never over- or '
            'undershoots the data, is smooth between points and reproduces '
            'linear surfaces exactly. It is only defined inside the convex '
            'hull of the points; cells outside it are set to NoData.</p>'
            '<h3>Parameters</h3>'
            '<p><b>Input points</b> - point layer to interpolate.</p>'
            '<p><b>Z value attribute</b> - numeric field holding the values. '
            'If left empty, the Z coordinates of the (3D) point geometries '
            'are used.</p>'
            '<p><b>Interpolation method</b> - minimum curvature or natural '
            'neighbor.</p>'
            '<p><b>Tension</b> - <i>minimum curvature only.</i> 0 gives pure '
            'minimum curvature (smoothest, but may overshoot); 1 gives a '
            'Laplace/membrane surface (no overshoots, but cusps at the data '
            'points). Values of 0.2-0.5 are commonly useful for irregular '
            'data.</p>'
            '<p><b>Extent</b> - output extent. If empty, the extent of the '
            'input layer is used.</p>'
            '<p><b>Output raster size</b> - pixel size in layer units. 0 '
            'selects a size that gives about 250 cells along the longer '
            'side of the extent.</p>'
            '<h3>Advanced parameters (minimum curvature only)</h3>'
            '<p><b>Maximum iterations</b> - iteration limit per grid level.</p>'
            '<p><b>Convergence limit</b> - iteration stops when the largest '
            'change of any cell in one iteration falls below this fraction '
            'of the data range (after removing the plane).</p>'
            '<p><b>Relaxation factor</b> - SOR over-relaxation factor '
            '(1 &le; &omega; &lt; 2). Lower it to 1.0-1.2 if the solution '
            'does not settle.</p>'
            '<h3>Notes</h3>'
            '<p>Points falling in the same output cell (minimum curvature) '
            'or at identical coordinates (natural neighbor) are averaged. '
            'Minimum curvature ignores points outside the extent; natural '
            'neighbor uses all points, because neighbors outside the extent '
            'influence cells near its edge.</p>')

    # -- parameters ---------------------------------------------------------
    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr('Input points'), [_TYPE_POINT]))

        self.addParameter(QgsProcessingParameterField(
            self.Z_FIELD,
            self.tr('Z value attribute (empty = use Z coordinate)'),
            parentLayerParameterName=self.INPUT,
            type=_FIELD_NUMERIC,
            optional=True))

        self.addParameter(QgsProcessingParameterEnum(
            self.METHOD, self.tr('Interpolation method'),
            options=[self.tr('Minimum curvature'), self.tr('Natural neighbor (Sibson)')],
            allowMultiple=False, defaultValue=METHOD_MINCURV))

        self.addParameter(QgsProcessingParameterNumber(
            self.TENSION,
            self.tr('Tension (minimum curvature only: 0 = minimum curvature, 1 = membrane)'),
            type=_NUM_DOUBLE, defaultValue=0.0, minValue=0.0, maxValue=1.0))

        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT,
            self.tr('Extent (empty = extent of input layer)'),
            optional=True))

        size = QgsProcessingParameterNumber(
            self.PIXEL_SIZE,
            self.tr('Output raster size (pixel size in layer units, 0 = automatic)'),
            type=_NUM_DOUBLE, defaultValue=0.0, minValue=0.0)
        size.setMetadata({'widget_wrapper': {'decimals': 6}})
        self.addParameter(size)

        iters = QgsProcessingParameterNumber(
            self.MAX_ITERATIONS,
            self.tr('Maximum iterations per grid level (minimum curvature only)'),
            type=_NUM_INTEGER, defaultValue=1000, minValue=1, maxValue=1000000)
        tol = QgsProcessingParameterNumber(
            self.TOLERANCE,
            self.tr('Convergence limit, fraction of data range (minimum curvature only)'),
            type=_NUM_DOUBLE, defaultValue=0.0001, minValue=1e-10, maxValue=1.0)
        tol.setMetadata({'widget_wrapper': {'decimals': 8}})
        omega = QgsProcessingParameterNumber(
            self.RELAXATION, self.tr('Relaxation factor (minimum curvature only)'),
            type=_NUM_DOUBLE, defaultValue=1.4, minValue=1.0, maxValue=1.95)
        for p in (iters, tol, omega):
            p.setFlags(p.flags() | _FLAG_ADVANCED)
            self.addParameter(p)

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr('Interpolated')))

    # -- validation ---------------------------------------------------------
    def checkParameterValues(self, parameters, context):  # noqa: N802
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is not None:
            z_field = self.parameterAsString(parameters, self.Z_FIELD, context)
            if not z_field and not QgsWkbTypes.hasZ(source.wkbType()):
                return False, self.tr(
                    'The input layer has no Z coordinates. Please choose a '
                    'Z value attribute.')
        return super().checkParameterValues(parameters, context)

    # -- processing ---------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))

        z_field = self.parameterAsString(parameters, self.Z_FIELD, context) or ''
        z_idx = -1
        if z_field:
            z_idx = source.fields().lookupField(z_field)
            if z_idx < 0:
                raise QgsProcessingException(
                    self.tr('Field "{}" was not found in the input layer.').format(z_field))

        method = self.parameterAsEnum(parameters, self.METHOD, context)
        tension = self.parameterAsDouble(parameters, self.TENSION, context)
        cell = self.parameterAsDouble(parameters, self.PIXEL_SIZE, context)
        max_iter = self.parameterAsInt(parameters, self.MAX_ITERATIONS, context)
        tol = self.parameterAsDouble(parameters, self.TOLERANCE, context)
        omega = self.parameterAsDouble(parameters, self.RELAXATION, context)
        output = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        crs = source.sourceCrs()

        # ---- grid geometry ---------------------------------------------------
        extent = QgsRectangle()
        if parameters.get(self.EXTENT) not in (None, ''):
            extent = self.parameterAsExtent(parameters, self.EXTENT, context, crs)
        if extent.isNull() or extent.isEmpty():
            extent = source.sourceExtent()
        if extent.isNull() or extent.isEmpty():
            raise QgsProcessingException(self.tr(
                'The extent has no width or height. Please specify an extent '
                'explicitly.'))

        width, height = extent.width(), extent.height()
        if cell <= 0:
            cell = max(width, height) / 250.0
            feedback.pushInfo(self.tr('Automatic pixel size: {}').format(cell))
        ncols = max(1, int(math.ceil(width / cell - 1e-9)))
        nrows = max(1, int(math.ceil(height / cell - 1e-9)))
        if ncols < 4 or nrows < 4:
            raise QgsProcessingException(self.tr(
                'The output raster would be {} x {} cells. At least 4 x 4 '
                'cells are required - please use a smaller pixel size.').format(ncols, nrows))
        if ncols * nrows > MAX_CELLS:
            raise QgsProcessingException(self.tr(
                'The output raster would have {} x {} cells, which is more '
                'than the supported {} cells. Please use a larger pixel '
                'size.').format(ncols, nrows, MAX_CELLS))

        xmin, ymax = extent.xMinimum(), extent.yMaximum()
        grid_rect = QgsRectangle(xmin, ymax - nrows * cell, xmin + ncols * cell, ymax)
        feedback.pushInfo(self.tr('Output raster: {} columns x {} rows, pixel size {}').format(
            ncols, nrows, cell))

        # ---- read the points ---------------------------------------------------
        # Minimum curvature only uses points inside the grid.  Natural neighbor
        # needs every point: neighbours outside the grid shape the cells near
        # its edge, and dropping them would shrink the convex hull.
        request = QgsFeatureRequest()
        if method == METHOD_MINCURV:
            request.setFilterRect(grid_rect)
        request.setSubsetOfAttributes([z_idx] if z_idx >= 0 else [])
        xs, ys, zs = self._read_points(source, request, z_idx, feedback)
        if xs is None:
            return {}
        if len(xs) < 3:
            raise QgsProcessingException(self.tr(
                'At least 3 points with valid Z values are required '
                '(found {}).').format(len(xs)))

        # ---- solve ---------------------------------------------------------------
        try:
            if method == METHOD_NATNEIGH:
                grid = self._natural_neighbor(xs, ys, zs, xmin, ymax, cell, nrows, ncols, feedback)
            else:
                grid = self._minimum_curvature(xs, ys, zs, xmin, ymax, cell, nrows, ncols,
                                               tension, max_iter, tol, omega, feedback)
        except (mincurv_core.Canceled, natneigh_core.Canceled):
            return {}
        except ValueError as exc:
            raise QgsProcessingException(str(exc))

        # ---- write the raster ----------------------------------------------------
        feedback.pushInfo(self.tr('Writing raster'))
        self._write_raster(output, grid, xmin, ymax, cell, crs)
        feedback.setProgress(100)
        return {self.OUTPUT: output}

    # -- reading ---------------------------------------------------------------
    def _read_points(self, source, request, z_idx, feedback):
        """Returns three lists (x, y, z), or (None, None, None) if canceled."""
        total = source.featureCount()
        total = total if total and total > 0 else 1
        xs, ys, zs = [], [], []
        skipped = 0
        for i, feat in enumerate(source.getFeatures(request)):
            if feedback.isCanceled():
                return None, None, None
            feedback.setProgress(10.0 * i / total)
            geom = feat.geometry()
            if geom is None or geom.isNull() or geom.isEmpty():
                skipped += 1
                continue
            if z_idx >= 0:
                try:
                    zval = float(feat.attributes()[z_idx])
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                if math.isnan(zval):
                    skipped += 1
                    continue
                for pt in geom.vertices():
                    xs.append(pt.x())
                    ys.append(pt.y())
                    zs.append(zval)
            else:
                for pt in geom.vertices():
                    zval = pt.z()
                    if math.isnan(zval):
                        skipped += 1
                        continue
                    xs.append(pt.x())
                    ys.append(pt.y())
                    zs.append(zval)
        if skipped:
            feedback.pushWarning(self.tr(
                '{} point(s) without a valid Z value were ignored.').format(skipped))
        return xs, ys, zs

    # -- methods ---------------------------------------------------------------
    def _minimum_curvature(self, xs, ys, zs, xmin, ymax, cell, nrows, ncols,
                           tension, max_iter, tol, omega, feedback):
        feedback.pushInfo(self.tr('Minimum curvature: {} point(s), tension = {}').format(
            len(xs), tension))

        def progress(fraction):
            feedback.setProgress(10.0 + 85.0 * fraction)

        return mincurv_core.minimum_curvature_grid(
            xs, ys, zs, xmin, ymax, cell, nrows, ncols,
            tension=tension, max_iter=max_iter, tol=tol, omega=omega,
            progress=progress, log=feedback.pushInfo, cancelled=feedback.isCanceled)

    def _natural_neighbor(self, xs, ys, zs, xmin, ymax, cell, nrows, ncols, feedback):
        px, py, pz = natneigh_core.dedupe_points(xs, ys, zs)
        feedback.pushInfo(self.tr('Natural neighbor: {} distinct point(s)').format(px.size))
        if px.size < 3:
            raise ValueError(self.tr('At least 3 points at distinct locations are required.'))

        feedback.pushInfo(self.tr('Triangulating'))
        tris = self._triangulate(px, py, feedback)
        feedback.setProgress(20)
        if feedback.isCanceled():
            raise natneigh_core.Canceled()

        def progress(fraction):
            feedback.setProgress(20.0 + 75.0 * fraction)

        return natneigh_core.natural_neighbor_grid(
            px, py, pz, tris, xmin, ymax, cell, nrows, ncols,
            progress=progress, log=feedback.pushInfo, cancelled=feedback.isCanceled)

    def _triangulate(self, px, py, feedback):
        """Delaunay triangulation: scipy when present, otherwise QGIS/GEOS."""
        try:
            return natneigh_core.triangulate_scipy(px, py)
        except ImportError:
            feedback.pushInfo(self.tr('scipy is not available - using the GEOS Delaunay '
                                      'triangulation built into QGIS'))
        except Exception as exc:  # Qhull failure, typically collinear points
            reason = (str(exc).strip().splitlines() or [''])[0]
            raise ValueError(self.tr(
                'Delaunay triangulation failed - do all points lie on one line? ({})').format(reason))
        return self._triangulate_geos(px, py)

    def _triangulate_geos(self, px, py):
        xl, yl = px.tolist(), py.tolist()
        index = {(x, y): i for i, (x, y) in enumerate(zip(xl, yl))}
        multipoint = QgsGeometry.fromMultiPointXY([QgsPointXY(x, y) for x, y in zip(xl, yl)])
        result = multipoint.delaunayTriangulation(0.0, False)
        if result is None or result.isNull() or result.isEmpty():
            raise ValueError(self.tr('Delaunay triangulation failed (are the points collinear?).'))
        tris = []
        for part in result.asGeometryCollection():
            rings = part.asPolygon()
            if not rings or len(rings[0]) < 4:
                continue
            try:
                tris.append([index[(rings[0][k].x(), rings[0][k].y())] for k in range(3)])
            except KeyError:
                continue
        if not tris:
            raise ValueError(self.tr('Delaunay triangulation failed (are the points collinear?).'))
        return np.array(tris, dtype=np.intp)

    # -- output ---------------------------------------------------------------
    @staticmethod
    def _write_raster(path, grid, xmin, ymax, cell, crs):
        from osgeo import gdal

        nrows, ncols = grid.shape
        data = np.where(np.isfinite(grid), grid, NODATA).astype(np.float32)

        mem = gdal.GetDriverByName('MEM').Create('', ncols, nrows, 1, gdal.GDT_Float32)
        mem.SetGeoTransform((xmin, cell, 0.0, ymax, 0.0, -cell))
        if crs.isValid():
            mem.SetProjection(crs.toWkt())
        band = mem.GetRasterBand(1)
        band.SetNoDataValue(NODATA)
        band.WriteArray(data)
        band.FlushCache()

        ext = os.path.splitext(path)[1].lstrip('.')
        driver_name = QgsRasterFileWriter.driverForExtension(ext) or 'GTiff'
        driver = gdal.GetDriverByName(driver_name)
        if driver is None:
            raise QgsProcessingException('GDAL driver "{}" is not available.'.format(driver_name))
        out = driver.CreateCopy(path, mem, 0)
        if out is None:
            raise QgsProcessingException('Could not write the output raster "{}".'.format(path))
        out.FlushCache()
        out = None
        mem = None
