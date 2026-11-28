"""Tile drainage layout: half-spacing setback guide, laterals and main connections.

Laterals are usually started half a spacing in from the field edge so that
laterals in an adjoining field end up exactly one spacing apart across the
boundary. This algorithm draws that half-spacing setback so the person
digitizing the layout has a line to start the first and last laterals on.
"""

import math
import os

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QIcon
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineSymbol,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsPointXY,
    QgsProcessingException,
    QgsProcessingLayerPostProcessorInterface,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsSingleSymbolRenderer,
    QgsSpatialIndex,
    QgsUnitTypes,
    QgsWkbTypes,
)

FEET_TO_METERS = 0.3048
UNIT_NAMES = ['Feet', 'Meters']
ALIGN_NAMES = ['Start on guide line (leftover on far side)',
               'Centered (leftover split between both sides)']
JOIN_NAMES = ['Miter (square corners, follows field shape)', 'Round', 'Bevel']


# --------------------------------------------------------------------------
# Small compatibility helpers (QGIS 3.16 through 3.40+, Qt5 and Qt6)
# --------------------------------------------------------------------------

def _field(name, kind):
    """Create a QgsField of type 'double' or 'string' on old and new APIs."""
    try:
        from qgis.PyQt.QtCore import QMetaType
        qtype = QMetaType.Type.Double if kind == 'double' else QMetaType.Type.QString
        return QgsField(name, qtype)
    except (AttributeError, TypeError):
        qtype = QVariant.Double if kind == 'double' else QVariant.String
        return QgsField(name, qtype)


def _meters_unit():
    try:
        return Qgis.DistanceUnit.Meters
    except AttributeError:
        return QgsUnitTypes.DistanceMeters


def _buffer_styles(join_index):
    try:
        cap = Qgis.EndCapStyle.Round
        joins = [Qgis.JoinStyle.Miter, Qgis.JoinStyle.Round, Qgis.JoinStyle.Bevel]
    except AttributeError:
        cap = QgsGeometry.CapRound
        joins = [QgsGeometry.JoinStyleMiter, QgsGeometry.JoinStyleRound,
                 QgsGeometry.JoinStyleBevel]
    return cap, joins[join_index]


def _polygon_type():
    try:
        return Qgis.GeometryType.Polygon
    except AttributeError:
        return QgsWkbTypes.PolygonGeometry


def _line_type():
    try:
        return Qgis.GeometryType.Line
    except AttributeError:
        return QgsWkbTypes.LineGeometry


def _longest_edge_azimuth(geom, tolerance):
    """Azimuth (0-180, clockwise from north) of the longest outer-ring edge.

    The boundary is simplified first so that a long, slightly wobbly
    digitized edge counts as one edge rather than many short ones.
    """
    g = geom.simplify(tolerance) if tolerance > 0 else QgsGeometry(geom)
    if g is None or g.isEmpty():
        g = QgsGeometry(geom)
    g.convertToMultiType()
    best_len, best_az = -1.0, 0.0
    for poly in g.asMultiPolygon():
        if not poly:
            continue
        ring = poly[0]
        for a, b in zip(ring, ring[1:]):
            dx, dy = b.x() - a.x(), b.y() - a.y()
            seg_len = math.hypot(dx, dy)
            if seg_len > best_len:
                best_len = seg_len
                best_az = math.degrees(math.atan2(dx, dy)) % 180.0
    return best_az


MAX_LATERALS_PER_FIELD = 20000


def _make_laterals(area, spacing, azimuth, min_length, centered=False, exclude=None):
    """Parallel laterals clipped to `area`, `spacing` apart, at `azimuth`.

    The polygon is rotated so that laterals become vertical lines, the lines
    are laid out across its width, clipped, and rotated back.
    centered=False: the first lateral sits on the guide line along one side
    and the leftover (< one spacing) falls on the opposite side.
    centered=True: the leftover is split equally between both sides.
    exclude: optional polygon (no-tile zones). The layout is still positioned
    on the full `area`, so laterals stay aligned with the guide line, but
    they are clipped to stay out of `exclude`.
    Returns a list of single-part line geometries.
    """
    center = area.boundingBox().center()
    rotated = QgsGeometry(area)
    rotated.rotate(-azimuth, center)
    bbox = rotated.boundingBox()
    if exclude is not None and not exclude.isEmpty():
        clip_area = area.difference(exclude)
        if clip_area is None or clip_area.isEmpty():
            return []
        rotated = QgsGeometry(clip_area)
        rotated.rotate(-azimuth, center)

    eps = spacing * 1e-4  # nudge off the exact edge so GEOS returns a line
    usable = bbox.width() - 2 * eps
    count = int(usable // spacing) + 1 if usable >= 0 else 0
    leftover = usable - (count - 1) * spacing if count > 0 else 0.0
    start = bbox.xMinimum() + eps + (leftover / 2.0 if centered else 0.0)
    if count > MAX_LATERALS_PER_FIELD:
        raise QgsProcessingException(
            'More than {} laterals would be created in one field. '
            'Check the spacing and its units.'.format(MAX_LATERALS_PER_FIELD))

    y0, y1 = bbox.yMinimum() - spacing, bbox.yMaximum() + spacing
    laterals = []
    for i in range(max(count, 0)):
        x = start + i * spacing
        line = QgsGeometry.fromPolylineXY([QgsPointXY(x, y0), QgsPointXY(x, y1)])
        clipped = line.intersection(rotated)
        if clipped is None or clipped.isEmpty():
            continue
        for part in clipped.asGeometryCollection():
            if part.type() != _line_type():
                continue
            if part.length() <= max(min_length, eps):
                continue
            part.rotate(azimuth, center)
            laterals.append(part)
    return laterals


CONNECT_NAMES = ['Extend laterals straight to the main',
                 'Extend straight, else add shortest connector to the main']


def _end_points(line):
    pts = line.asPolyline()
    if not pts:
        mpts = line.asMultiPolyline()
        pts = [p for part in mpts for p in part]
    return (pts[0], pts[-1]) if pts else (None, None)


def _crosses_zone(segment, blocked, tol):
    """True if `segment` runs through the no-tile polygon `blocked`.

    Touching the zone edge (a lateral that ends on the zone setback) is fine;
    only a stretch of line inside the zone counts.
    """
    if blocked is None or not segment.intersects(blocked):
        return False
    inside = segment.intersection(blocked)
    return inside is not None and not inside.isEmpty() and inside.length() > tol


def _connect_lateral(lat, mains, method, max_dist, reach, stub_len, tol, blocked=None):
    """Connect one lateral to the mains geometry.

    Returns a list of (geometry, status, connection_length, [junction points]).
    - A lateral that crosses a main is split there; each piece ends on the
      main. One-ended pieces shorter than `stub_len` (stubs past the main)
      are dropped.
    - Otherwise the nearer end is extended straight along the lateral until
      it meets a main (within `max_dist`, 0 = any distance up to `reach`).
    - If that fails and method == 1, a straight connector is added from the
      nearer end to the closest point on a main.
    Extensions and connectors that would pass through `blocked` (no-tile
    zones) are not used.
    """
    if lat.intersects(mains):
        out = []
        for piece in lat.difference(mains).asGeometryCollection():
            if piece.isEmpty() or piece.type() != _line_type():
                continue
            a, b = _end_points(piece)
            if a is None:
                continue
            touching = [p for p in (a, b)
                        if mains.distance(QgsGeometry.fromPointXY(p)) <= tol]
            if len(touching) == 1 and piece.length() < stub_len:
                continue  # short stub beyond the main
            status = 'crossing' if touching else 'none'
            out.append((piece, status, 0.0, touching))
        return out

    a, b = _end_points(lat)
    length = math.hypot(b.x() - a.x(), b.y() - a.y())
    if length <= 0:
        return [(lat, 'none', 0.0, [])]
    ux, uy = (b.x() - a.x()) / length, (b.y() - a.y()) / length
    limit = max_dist if max_dist > 0 else reach

    # 1) Straight extension along the lateral, from whichever end hits first
    best = None
    for end, sign in ((a, -1.0), (b, 1.0)):
        far = QgsPointXY(end.x() + sign * ux * limit, end.y() + sign * uy * limit)
        hit = QgsGeometry.fromPolylineXY([end, far]).intersection(mains)
        if hit is None or hit.isEmpty():
            continue
        for v in hit.vertices():
            d = math.hypot(v.x() - end.x(), v.y() - end.y())
            if d > tol and (best is None or d < best[0]):
                pt = QgsPointXY(v.x(), v.y())
                if _crosses_zone(QgsGeometry.fromPolylineXY([end, pt]), blocked, tol):
                    continue
                best = (d, sign, pt)
    if best is not None:
        d, sign, hit_pt = best
        pts = [hit_pt, b] if sign < 0 else [a, hit_pt]
        return [(QgsGeometry.fromPolylineXY(pts), 'extended', d, [hit_pt])]

    # 2) Shortest straight connector from the nearer end
    if method == 1:
        best = None
        for end, sign in ((a, -1.0), (b, 1.0)):
            near = mains.nearestPoint(QgsGeometry.fromPointXY(end)).asPoint()
            pt = QgsPointXY(near.x(), near.y())
            if _crosses_zone(QgsGeometry.fromPolylineXY([end, pt]), blocked, tol):
                continue
            d = math.hypot(near.x() - end.x(), near.y() - end.y())
            if best is None or d < best[0]:
                best = (d, sign, pt)
        if best is not None:
            d, sign, near_pt = best
        if best is not None and (max_dist <= 0 or d <= max_dist):
            pts = [near_pt, a, b] if sign < 0 else [a, b, near_pt]
            return [(QgsGeometry.fromPolylineXY(pts), 'connector', d, [near_pt])]

    return [(lat, 'none', 0.0, [])]


FLOW_NAMES = ['Downhill on the DEM (flag laterals that fall away from their main)',
              'Toward the main (report surface fall from the DEM)']
Z_UNIT_NAMES = ['Meters', 'Feet']
FLAT_FALL_M = 0.02  # end-to-end surface fall below this counts as flat


class _DemSampler:
    """Reads elevations from band 1 of a DEM, in the working CRS."""

    def __init__(self, layer, work_crs, transform_context):
        # A cloned provider is safe to read from the processing thread.
        self.provider = layer.dataProvider().clone()
        self.transform = None
        if layer.crs() != work_crs:
            self.transform = QgsCoordinateTransform(work_crs, layer.crs(), transform_context)

    def z(self, pt):
        try:
            p = self.transform.transform(pt) if self.transform is not None else pt
            value, ok = self.provider.sample(QgsPointXY(p.x(), p.y()), 1)
        except Exception:  # outside the raster, transform failure, etc.
            return None
        if not ok or value is None or math.isnan(value):
            return None
        return float(value)


def _reverse_line(geom):
    pts = geom.asPolyline()
    return QgsGeometry.fromPolylineXY(list(reversed(pts))) if pts else geom


def _orient_lateral(geom, mains, dem, rule, z_to_m, tol):
    """Point a lateral in its flow direction (first vertex upstream).

    rule 0: downhill on the DEM; falls back to toward the main where the
            DEM can't decide (no DEM, no data, or flat).
    rule 1: toward the main; unconnected laterals point downhill if possible.
    Returns (geometry, z_start, z_end, flow) where flow is one of
    'to_main', 'away_from_main', 'uphill_to_main', 'downhill', 'flat',
    'no_dem', or 'none' (no main and no DEM to decide).
    """
    a, b = _end_points(geom)
    if a is None:
        return geom, None, None, 'none'

    # Which end, if either, is on a main?
    main_end = None
    if mains is not None:
        da = mains.distance(QgsGeometry.fromPointXY(a))
        db = mains.distance(QgsGeometry.fromPointXY(b))
        if min(da, db) <= tol:
            main_end = 'a' if da <= db else 'b'

    za = dem.z(a) if dem is not None else None
    zb = dem.z(b) if dem is not None else None
    have_z = za is not None and zb is not None
    flat = have_z and abs(za - zb) * z_to_m < FLAT_FALL_M

    def toward_main():
        return main_end == 'a'  # reverse so the main end comes last

    reverse = False
    if rule == 0:
        if have_z and not flat:
            reverse = za < zb
            down_end = 'a' if reverse else 'b'
            if main_end is None:
                flow = 'downhill'
            else:
                flow = 'to_main' if down_end == main_end else 'away_from_main'
        else:
            reverse = toward_main()
            flow = 'flat' if flat else ('no_dem' if main_end is not None else 'none')
    else:
        if main_end is not None:
            reverse = toward_main()
            if not have_z:
                flow = 'no_dem'
            elif flat:
                flow = 'flat'
            else:
                z_up, z_down = (zb, za) if reverse else (za, zb)
                flow = 'to_main' if z_up > z_down else 'uphill_to_main'
        elif have_z and not flat:
            reverse = za < zb
            flow = 'downhill'
        else:
            flow = 'flat' if flat else 'none'

    if reverse:
        geom = _reverse_line(geom)
        za, zb = zb, za
    return geom, za, zb, flow


def _utm_crs_for(source, context):
    """Pick the WGS84 UTM zone at the centre of the layer's extent."""
    src_crs = source.sourceCrs()
    to_wgs = QgsCoordinateTransform(
        src_crs, QgsCoordinateReferenceSystem('EPSG:4326'), context.transformContext())
    centre = to_wgs.transform(source.sourceExtent().center())
    zone = int((centre.x() + 180.0) // 6.0) + 1
    zone = min(max(zone, 1), 60)
    epsg = (32600 if centre.y() >= 0 else 32700) + zone
    return QgsCoordinateReferenceSystem('EPSG:{}'.format(epsg))


# --------------------------------------------------------------------------
# Styling of the output layers when they are added to the map
# --------------------------------------------------------------------------

class _GuideLineStyler(QgsProcessingLayerPostProcessorInterface):
    instance = None  # keep a Python reference or QGIS will crash

    def postProcessLayer(self, layer, context, feedback):  # noqa: N802
        symbol = QgsLineSymbol.createSimple({
            'line_color': '230,30,30,255',
            'line_width': '0.6',
            'line_style': 'dash',
        })
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.triggerRepaint()

    @staticmethod
    def create():
        _GuideLineStyler.instance = _GuideLineStyler()
        return _GuideLineStyler.instance


class _LateralStyler(QgsProcessingLayerPostProcessorInterface):
    instance = None

    def postProcessLayer(self, layer, context, feedback):  # noqa: N802
        symbol = QgsLineSymbol.createSimple({
            'line_color': '30,90,200,255',
            'line_width': '0.4',
        })
        # Small arrowheads along each lateral show its flow direction.
        try:
            from qgis.core import QgsMarkerLineSymbolLayer, QgsMarkerSymbol
            arrow = QgsMarkerSymbol.createSimple({
                'name': 'arrowhead', 'color': '30,90,200,255',
                'outline_color': '30,90,200,255', 'size': '2',
            })
            markers = QgsMarkerLineSymbolLayer(True, 20)  # rotate with line, every 20 mm
            markers.setSubSymbol(arrow)
            symbol.appendSymbolLayer(markers)
        except Exception:
            pass  # plain line on QGIS builds without these classes
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.triggerRepaint()

    @staticmethod
    def create():
        _LateralStyler.instance = _LateralStyler()
        return _LateralStyler.instance


class _AreaStyler(QgsProcessingLayerPostProcessorInterface):
    instance = None

    def postProcessLayer(self, layer, context, feedback):  # noqa: N802
        symbol = QgsFillSymbol.createSimple({
            'color': '40,160,220,40',
            'outline_color': '40,160,220,200',
            'outline_width': '0.3',
        })
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.triggerRepaint()

    @staticmethod
    def create():
        _AreaStyler.instance = _AreaStyler()
        return _AreaStyler.instance


# --------------------------------------------------------------------------
# The algorithm
# --------------------------------------------------------------------------

class TileDrainageLayoutAlgorithm(QgsProcessingAlgorithm):

    INPUT = 'INPUT'
    SPACING = 'SPACING'
    UNITS = 'UNITS'
    SPACING_FIELD = 'SPACING_FIELD'
    JOIN_STYLE = 'JOIN_STYLE'
    GENERATE_LATERALS = 'GENERATE_LATERALS'
    LATERAL_ANGLE = 'LATERAL_ANGLE'
    ANGLE_FIELD = 'ANGLE_FIELD'
    AUTO_ANGLE = 'AUTO_ANGLE'
    MIN_LENGTH = 'MIN_LENGTH'
    ALIGNMENT = 'ALIGNMENT'
    OUTPUT_AREA = 'OUTPUT_AREA'
    OUTPUT_LINE = 'OUTPUT_LINE'
    OUTPUT_LATERALS = 'OUTPUT_LATERALS'
    MAINS = 'MAINS'
    CONNECT_METHOD = 'CONNECT_METHOD'
    MAX_CONNECT = 'MAX_CONNECT'
    OUTPUT_JUNCTIONS = 'OUTPUT_JUNCTIONS'
    NO_TILE = 'NO_TILE'
    NO_TILE_SETBACK = 'NO_TILE_SETBACK'
    DEM = 'DEM'
    Z_UNITS = 'Z_UNITS'
    FLOW_RULE = 'FLOW_RULE'

    def tr(self, text):
        return QCoreApplication.translate('TileDrainageLayoutAlgorithm', text)

    def createInstance(self):  # noqa: N802
        return TileDrainageLayoutAlgorithm()

    def name(self):
        return 'tiledrainagelayout'

    def displayName(self):  # noqa: N802
        return self.tr('Tile drainage layout')

    def group(self):
        return self.tr('Layout')

    def groupId(self):  # noqa: N802
        return 'layout'

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), 'icon.png'))

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            '<p>Buffers each field boundary <b>inward</b> by half of the tile '
            '(lateral) spacing. The dashed guide line marks where the first and '
            'last laterals should run, so that laterals in the next field over '
            'end up one full spacing apart across the boundary.</p>'
            '<p><b>Tile spacing</b>: lateral spacing, in the units chosen below. '
            'The setback is half this value (e.g. 60 ft spacing gives a 30 ft setback).</p>'
            '<p><b>Spacing field</b> (optional): a numeric attribute holding the '
            'spacing for each field, in the same units. Features with an empty or '
            'zero value use the default spacing.</p>'
            '<p><b>Corner style</b>: Miter keeps square corners that parallel the '
            'field edges, which is usually what you want for a lateral layout.</p>'
            '<p>Holes in a boundary (ponds, waterways, building sites) are buffered '
            'outward by the same distance automatically. Layers in a geographic '
            'CRS such as WGS84 are processed and output in the local UTM zone.</p>'
            '<p>Fields too narrow for the setback produce no output and are '
            'reported in the log.</p>'
            '<h3>Laterals</h3>'
            '<p>Tick <b>Generate laterals</b> to fill each inset area with parallel '
            'laterals at the full tile spacing.</p>'
            '<p><b>Lateral alignment</b>: <i>Start on guide line</i> puts the first '
            'lateral on the guide and leaves any remainder narrower than one spacing '
            'on the far side. <i>Centered</i> splits that remainder equally between '
            'both sides, so the outer laterals sit the same distance in from each '
            'guide (centered on the field\'s width across the laterals).</p>'
            '<p><b>Lateral direction</b> is an azimuth in degrees clockwise from '
            'north: 0 = north–south laterals, 90 = east–west, 45 = NE–SW. '
            'An optional <b>angle field</b> sets the direction per field, and '
            '<b>parallel to longest edge</b> picks it automatically from each '
            'field\'s longest boundary edge (overriding both).</p>'
            '<p>Laterals crossing a hole are split in two. Pieces shorter than '
            '<b>Minimum lateral length</b> are dropped, which removes short stubs '
            'in corners.</p>'
            '<h3>Connecting to a main</h3>'
            '<p>Supply a <b>Mains</b> line layer to connect the generated laterals '
            'to it. Only mains within or near each field are used.</p>'
            '<ul><li>A lateral that <b>crosses</b> a main is split at the main, so '
            'each piece ends on it. Pieces shorter than the setback left beyond a '
            'main (stubs) are dropped.</li>'
            '<li>Otherwise the lateral is <b>extended straight</b>, along its own '
            'direction, from its nearer end to the main.</li>'
            '<li>With <i>Extend straight, else add shortest connector</i>, laterals '
            'whose line never meets a main get a straight connector to the closest '
            'point on the main.</li></ul>'
            '<p><b>Maximum connection distance</b> (spacing units, 0 = no limit) '
            'stops laterals from reaching for a distant main. Each lateral gets a '
            '<i>connection</i> attribute (crossing, extended, connector, none) and '
            '<i>conn_len</i>. The optional <b>Junctions</b> output marks each '
            'connection as a tee, or a cross where laterals meet the main from '
            'both sides, which helps with counting fittings.</p>'
            '<h3>No-tile zones</h3>'
            '<p>An optional polygon layer of areas to keep tile out of (wetlands, '
            'wells, buried utilities, building sites). Laterals are clipped at each '
            'zone plus the <b>setback</b>, and extensions or connectors that would '
            'pass through a zone are not used. Laterals stay aligned with the guide '
            'line; zones only remove pieces of them.</p>'
            '<h3>Flow direction</h3>'
            '<p>Every lateral is drawn from its upstream end to its downstream end, '
            'shown by arrows on the layer. With a <b>DEM</b>:</p>'
            '<ul><li><i>Downhill on the DEM</i>: each lateral points from its higher '
            'end to its lower end. If its lower end is not on the main it gets '
            '<i>flow</i> = away_from_main.</li>'
            '<li><i>Toward the main</i>: connected laterals always end on the main; '
            'those whose surface rises toward the main get flow = uphill_to_main.</li></ul>'
            '<p>Laterals with less than 2 cm of end-to-end fall, outside the DEM, or '
            'with no DEM point toward the main if they reach one (flow = flat or '
            'no_dem). Elevations are sampled at the two ends only; <i>elev_start</i>, '
            '<i>elev_end</i>, <i>fall</i> (spacing units) and <i>slope_pct</i> are '
            'surface values, not tile grade.</p>')

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr('Field boundaries'),
            [QgsProcessing.TypeVectorPolygon]))

        spacing = QgsProcessingParameterNumber(
            self.SPACING, self.tr('Tile spacing'),
            QgsProcessingParameterNumber.Double, defaultValue=60.0, minValue=0.0)
        self.addParameter(spacing)

        self.addParameter(QgsProcessingParameterEnum(
            self.UNITS, self.tr('Spacing units'), options=UNIT_NAMES, defaultValue=0))

        self.addParameter(QgsProcessingParameterField(
            self.SPACING_FIELD, self.tr('Spacing field (optional, overrides default per feature)'),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Numeric, optional=True))

        self.addParameter(QgsProcessingParameterEnum(
            self.JOIN_STYLE, self.tr('Corner style'), options=JOIN_NAMES, defaultValue=0))

        self.addParameter(QgsProcessingParameterBoolean(
            self.GENERATE_LATERALS, self.tr('Generate laterals within the buffer'),
            defaultValue=False))

        self.addParameter(QgsProcessingParameterNumber(
            self.LATERAL_ANGLE,
            self.tr('Lateral direction (azimuth °, 0 = north–south, 90 = east–west)'),
            QgsProcessingParameterNumber.Double, defaultValue=0.0,
            minValue=-360.0, maxValue=360.0))

        self.addParameter(QgsProcessingParameterField(
            self.ANGLE_FIELD, self.tr('Lateral direction field (optional, per feature)'),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Numeric, optional=True))

        self.addParameter(QgsProcessingParameterBoolean(
            self.AUTO_ANGLE,
            self.tr('Run laterals parallel to each field\'s longest edge (overrides direction)'),
            defaultValue=False))

        self.addParameter(QgsProcessingParameterEnum(
            self.ALIGNMENT, self.tr('Lateral alignment'), options=ALIGN_NAMES,
            defaultValue=0))

        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_LENGTH, self.tr('Minimum lateral length (spacing units)'),
            QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=0.0))

        self.addParameter(QgsProcessingParameterFeatureSource(
            self.MAINS, self.tr('Mains to connect laterals to (optional)'),
            [QgsProcessing.TypeVectorLine], optional=True))

        self.addParameter(QgsProcessingParameterEnum(
            self.CONNECT_METHOD, self.tr('Connection method'), options=CONNECT_NAMES,
            defaultValue=0))

        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_CONNECT,
            self.tr('Maximum connection distance (spacing units, 0 = no limit)'),
            QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=0.0))

        self.addParameter(QgsProcessingParameterFeatureSource(
            self.NO_TILE, self.tr('No-tile zones (optional polygons, e.g. wetlands, wells, utilities)'),
            [QgsProcessing.TypeVectorPolygon], optional=True))

        self.addParameter(QgsProcessingParameterNumber(
            self.NO_TILE_SETBACK,
            self.tr('Setback from no-tile zones (spacing units)'),
            QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=0.0))

        self.addParameter(QgsProcessingParameterRasterLayer(
            self.DEM, self.tr('Elevation model (DEM, optional; sets downhill direction)'),
            optional=True))

        self.addParameter(QgsProcessingParameterEnum(
            self.Z_UNITS, self.tr('DEM elevation units'), options=Z_UNIT_NAMES,
            defaultValue=0))

        self.addParameter(QgsProcessingParameterEnum(
            self.FLOW_RULE, self.tr('Lateral direction of flow'), options=FLOW_NAMES,
            defaultValue=0))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_LINE, self.tr('Tile guide line'), QgsProcessing.TypeVectorLine))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_LATERALS, self.tr('Laterals'), QgsProcessing.TypeVectorLine,
            optional=True, createByDefault=True))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_JUNCTIONS, self.tr('Junctions (lateral–main connections)'),
            QgsProcessing.TypeVectorPoint, optional=True, createByDefault=True))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_AREA, self.tr('Inset area'), QgsProcessing.TypeVectorPolygon,
            optional=True, createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))

        default_spacing = self.parameterAsDouble(parameters, self.SPACING, context)
        unit_index = self.parameterAsEnum(parameters, self.UNITS, context)
        spacing_field = self.parameterAsString(parameters, self.SPACING_FIELD, context)
        join_index = self.parameterAsEnum(parameters, self.JOIN_STYLE, context)
        make_laterals = self.parameterAsBool(parameters, self.GENERATE_LATERALS, context)
        default_angle = self.parameterAsDouble(parameters, self.LATERAL_ANGLE, context)
        angle_field = self.parameterAsString(parameters, self.ANGLE_FIELD, context)
        auto_angle = self.parameterAsBool(parameters, self.AUTO_ANGLE, context)
        min_length = self.parameterAsDouble(parameters, self.MIN_LENGTH, context)
        centered = self.parameterAsEnum(parameters, self.ALIGNMENT, context) == 1
        mains_source = self.parameterAsSource(parameters, self.MAINS, context)
        connect_method = self.parameterAsEnum(parameters, self.CONNECT_METHOD, context)
        max_connect = self.parameterAsDouble(parameters, self.MAX_CONNECT, context)
        no_tile_source = self.parameterAsSource(parameters, self.NO_TILE, context)
        no_tile_setback = self.parameterAsDouble(parameters, self.NO_TILE_SETBACK, context)
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        z_to_m = FEET_TO_METERS if self.parameterAsEnum(parameters, self.Z_UNITS, context) == 1 else 1.0
        flow_rule = self.parameterAsEnum(parameters, self.FLOW_RULE, context)
        if mains_source is not None and not make_laterals:
            feedback.reportError(self.tr(
                'Mains were supplied but Generate laterals is not ticked; '
                'nothing will be connected.'))

        if default_spacing <= 0 and not spacing_field:
            raise QgsProcessingException(self.tr('Tile spacing must be greater than zero.'))

        # ---- Working CRS: must be projected so distances are real lengths ----
        src_crs = source.sourceCrs()
        if not src_crs.isValid():
            raise QgsProcessingException(self.tr(
                'The field boundary layer has no CRS set. Assign one and try again.'))

        transform = None
        work_crs = src_crs
        if src_crs.isGeographic():
            work_crs = _utm_crs_for(source, context)
            transform = QgsCoordinateTransform(src_crs, work_crs, context.transformContext())
            feedback.pushInfo(self.tr('Input is in a geographic CRS; working and output CRS is {}.')
                              .format(work_crs.authid()))

        meters_to_map = QgsUnitTypes.fromUnitToUnitFactor(_meters_unit(), work_crs.mapUnits())
        unit_to_meters = FEET_TO_METERS if unit_index == 0 else 1.0
        unit_label = UNIT_NAMES[unit_index].lower()

        cap_style, join_style = _buffer_styles(join_index)
        miter_limit = 4.0
        segments = 8

        # ---- Output fields: everything from input + spacing info ----
        fields = QgsFields(source.fields())
        for name, kind in (('tile_spacing', 'double'), ('setback', 'double'),
                           ('units', 'string')):
            if fields.lookupField(name) == -1:
                fields.append(_field(name, kind))
        idx_spacing = fields.lookupField('tile_spacing')
        idx_setback = fields.lookupField('setback')
        idx_units = fields.lookupField('units')

        line_sink, line_id = self.parameterAsSink(
            parameters, self.OUTPUT_LINE, context, fields,
            QgsWkbTypes.MultiLineString, work_crs)
        if line_sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT_LINE))

        area_sink, area_id = self.parameterAsSink(
            parameters, self.OUTPUT_AREA, context, fields,
            QgsWkbTypes.MultiPolygon, work_crs)

        lat_sink, lat_id = None, None
        if make_laterals:
            lat_fields = QgsFields(source.fields())
            lat_names = (('lateral_no', 'double'), ('azimuth', 'double'),
                         ('tile_spacing', 'double'), ('length', 'double'),
                         ('units', 'string'), ('connection', 'string'),
                         ('conn_len', 'double'), ('flow', 'string'),
                         ('elev_start', 'double'), ('elev_end', 'double'),
                         ('fall', 'double'), ('slope_pct', 'double'))
            for name, kind in lat_names:
                if lat_fields.lookupField(name) == -1:
                    lat_fields.append(_field(name, kind))
            lat_idx = {n: lat_fields.lookupField(n) for n, _k in lat_names}
            lat_sink, lat_id = self.parameterAsSink(
                parameters, self.OUTPUT_LATERALS, context, lat_fields,
                QgsWkbTypes.LineString, work_crs)
            if lat_sink is None:
                feedback.reportError(self.tr(
                    'Generate laterals is ticked but the Laterals output is skipped; '
                    'no laterals will be written.'))

        spacing_idx_in = source.fields().lookupField(spacing_field) if spacing_field else -1
        angle_idx_in = source.fields().lookupField(angle_field) if angle_field else -1
        lateral_total = 0

        # ---- Mains: load into the working CRS with a spatial index ----
        mains_geoms, mains_index = {}, None
        junc_sink, junc_id = None, None
        connect_counts = {'crossing': 0, 'extended': 0, 'connector': 0, 'none': 0}
        if lat_sink is not None and mains_source is not None:
            mains_tr = None
            if mains_source.sourceCrs() != work_crs:
                mains_tr = QgsCoordinateTransform(
                    mains_source.sourceCrs(), work_crs, context.transformContext())
            mains_index = QgsSpatialIndex()
            for mf in mains_source.getFeatures():
                mg = mf.geometry()
                if mg is None or mg.isEmpty():
                    continue
                mg = QgsGeometry(mg)
                if mains_tr is not None:
                    mg.transform(mains_tr)
                f2 = QgsFeature(mf.id())
                f2.setGeometry(mg)
                mains_index.addFeature(f2)
                mains_geoms[mf.id()] = mg
            if not mains_geoms:
                feedback.reportError(self.tr('The mains layer has no line features.'))
                mains_index = None
            else:
                feedback.pushInfo(self.tr('Loaded {} main(s).').format(len(mains_geoms)))
                junc_fields = QgsFields(source.fields())
                for name, kind in (('junction', 'string'), ('laterals', 'double')):
                    if junc_fields.lookupField(name) == -1:
                        junc_fields.append(_field(name, kind))
                junc_idx_type = junc_fields.lookupField('junction')
                junc_idx_count = junc_fields.lookupField('laterals')
                junc_sink, junc_id = self.parameterAsSink(
                    parameters, self.OUTPUT_JUNCTIONS, context, junc_fields,
                    QgsWkbTypes.Point, work_crs)

        # ---- No-tile zones: working CRS, setback applied, spatial index ----
        zone_geoms, zone_index = {}, None
        if lat_sink is not None and no_tile_source is not None:
            zone_tr = None
            if no_tile_source.sourceCrs() != work_crs:
                zone_tr = QgsCoordinateTransform(
                    no_tile_source.sourceCrs(), work_crs, context.transformContext())
            zone_dist = no_tile_setback * unit_to_meters * meters_to_map
            zone_index = QgsSpatialIndex()
            for zf in no_tile_source.getFeatures():
                zg = zf.geometry()
                if zg is None or zg.isEmpty():
                    continue
                zg = QgsGeometry(zg)
                if zone_tr is not None:
                    zg.transform(zone_tr)
                if not zg.isGeosValid():
                    zg = zg.makeValid()
                    polys = zg.convertToType(_polygon_type(), True)
                    if polys is None or polys.isNull() or polys.isEmpty():
                        continue
                    zg = polys
                if zone_dist > 0:
                    zg = zg.buffer(zone_dist, segments)
                if zg is None or zg.isEmpty():
                    continue
                f2 = QgsFeature(zf.id())
                f2.setGeometry(zg)
                zone_index.addFeature(f2)
                zone_geoms[zf.id()] = zg
            if zone_geoms:
                feedback.pushInfo(self.tr('Loaded {} no-tile zone(s).').format(len(zone_geoms)))
            else:
                feedback.reportError(self.tr('The no-tile zones layer has no polygons.'))
                zone_index = None

        # ---- DEM for flow direction ----
        dem = None
        if lat_sink is not None and dem_layer is not None:
            if dem_layer.isValid():
                dem = _DemSampler(dem_layer, work_crs, context.transformContext())
                feedback.pushInfo(self.tr('Using DEM {} for lateral flow direction.')
                                  .format(dem_layer.name()))
            else:
                feedback.reportError(self.tr('The DEM could not be read; it is ignored.'))
        if lat_sink is not None and flow_rule == 0 and dem is None:
            feedback.pushInfo(self.tr(
                'No DEM supplied, so "downhill" cannot be determined; laterals connected '
                'to a main will point toward it instead.'))
        flow_counts = {}

        total = source.featureCount() or 0
        step = 100.0 / total if total else 0
        written = skipped = 0

        for current, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(int(current * step))

            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue

            # Spacing for this feature
            spacing = default_spacing
            if spacing_idx_in != -1:
                value = feat.attribute(spacing_idx_in)
                try:
                    value = float(value)
                    if value > 0:
                        spacing = value
                except (TypeError, ValueError):
                    pass
            if spacing <= 0:
                feedback.reportError(self.tr('Feature {}: no valid spacing, skipped.')
                                     .format(feat.id()))
                skipped += 1
                continue

            setback = spacing / 2.0
            distance = setback * unit_to_meters * meters_to_map

            geom = QgsGeometry(geom)
            if transform is not None:
                geom.transform(transform)
            if not geom.isGeosValid():
                geom = geom.makeValid()
                polys = geom.convertToType(_polygon_type(), True)
                if polys is not None and not polys.isNull() and not polys.isEmpty():
                    geom = polys

            inset = geom.buffer(-distance, segments, cap_style, join_style, miter_limit)

            if inset is None or inset.isEmpty() or inset.area() <= 0:
                feedback.reportError(self.tr(
                    'Feature {}: field is too narrow for a {} {} setback; no guide created.')
                    .format(feat.id(), round(setback, 3), unit_label))
                skipped += 1
                continue

            inset.convertToMultiType()
            attrs = list(feat.attributes()) + [None] * (fields.count() - len(feat.attributes()))
            attrs[idx_spacing] = spacing
            attrs[idx_setback] = setback
            attrs[idx_units] = unit_label

            if area_sink is not None:
                out = QgsFeature(fields)
                out.setGeometry(inset)
                out.setAttributes(attrs)
                area_sink.addFeature(out, QgsFeatureSink.FastInsert)

            line = QgsGeometry(inset.constGet().boundary())
            line.convertToMultiType()
            out = QgsFeature(fields)
            out.setGeometry(line)
            out.setAttributes(attrs)
            line_sink.addFeature(out, QgsFeatureSink.FastInsert)
            written += 1

            # ---- Laterals at full spacing inside the inset area ----
            if lat_sink is not None:
                spacing_map = spacing * unit_to_meters * meters_to_map
                if auto_angle:
                    azimuth = _longest_edge_azimuth(geom, distance)
                else:
                    azimuth = default_angle
                    if angle_idx_in != -1:
                        try:
                            value = feat.attribute(angle_idx_in)
                            if value is not None and str(value).strip() != '':
                                azimuth = float(value)
                        except (TypeError, ValueError):
                            pass
                azimuth %= 180.0

                min_len_map = min_length * unit_to_meters * meters_to_map
                max_map = max_connect * unit_to_meters * meters_to_map
                search_box = geom.boundingBox()
                search_box.grow(max(spacing_map, max_map))

                # No-tile zones in and around this field
                zones_here = None
                if zone_index is not None:
                    z_ids = [i for i in zone_index.intersects(search_box)
                             if zone_geoms[i].boundingBox().intersects(search_box)]
                    if z_ids:
                        zones_here = QgsGeometry.unaryUnion([zone_geoms[i] for i in z_ids])

                raw = _make_laterals(inset, spacing_map, azimuth, min_len_map, centered,
                                     exclude=zones_here)
                if not raw and zones_here is not None and inset.difference(zones_here).isEmpty():
                    feedback.reportError(self.tr(
                        'Feature {}: the whole field is inside no-tile zones.').format(feat.id()))

                # ---- Connect to mains near this field ----
                laterals = [(g, '', 0.0) for g in raw]
                mains_here = None
                tol = max(spacing_map * 1e-5, 1e-6)
                if mains_index is not None and raw:
                    search = geom.buffer(max(spacing_map, max_map), segments)
                    near_ids = [i for i in mains_index.intersects(search.boundingBox())
                                if mains_geoms[i].intersects(search)]
                    if near_ids:
                        mains_here = QgsGeometry.unaryUnion([mains_geoms[i] for i in near_ids])
                        bb = geom.boundingBox()
                        reach = 2.0 * math.hypot(bb.width(), bb.height()) + max_map
                        junctions = {}
                        laterals = []
                        for g in raw:
                            for piece, status, clen, jpts in _connect_lateral(
                                    g, mains_here, connect_method, max_map, reach,
                                    max(distance, min_len_map), tol, blocked=zones_here):
                                if piece.length() <= min_len_map and status == 'none':
                                    continue
                                laterals.append((piece, status, clen))
                                connect_counts[status] += 1
                                for jp in jpts:
                                    key = (round(jp.x() / tol / 10), round(jp.y() / tol / 10))
                                    junctions.setdefault(key, [jp, 0])[1] += 1
                        if junc_sink is not None:
                            base = list(feat.attributes())
                            for jp, n_lat in junctions.values():
                                j_attrs = base + [None] * (junc_fields.count() - len(base))
                                j_attrs[junc_idx_type] = 'cross' if n_lat >= 2 else 'tee'
                                j_attrs[junc_idx_count] = n_lat
                                jf = QgsFeature(junc_fields)
                                jf.setGeometry(QgsGeometry.fromPointXY(jp))
                                jf.setAttributes(j_attrs)
                                junc_sink.addFeature(jf, QgsFeatureSink.FastInsert)
                    else:
                        laterals = [(g, 'none', 0.0) for g in raw]
                        connect_counts['none'] += len(raw)
                        feedback.reportError(self.tr(
                            'Feature {}: no main within reach; laterals left unconnected.')
                            .format(feat.id()))

                base_attrs = list(feat.attributes())
                for n, (lat_geom, status, clen) in enumerate(laterals, start=1):
                    lat_geom, z0, z1, flow = _orient_lateral(
                        lat_geom, mains_here, dem, flow_rule, z_to_m, tol * 10)
                    flow_counts[flow] = flow_counts.get(flow, 0) + 1
                    lat_attrs = base_attrs + [None] * (lat_fields.count() - len(base_attrs))
                    lat_attrs[lat_idx['flow']] = flow
                    if z0 is not None and z1 is not None:
                        fall_m = (z0 - z1) * z_to_m
                        lat_attrs[lat_idx['elev_start']] = round(z0, 3)
                        lat_attrs[lat_idx['elev_end']] = round(z1, 3)
                        lat_attrs[lat_idx['fall']] = round(fall_m / unit_to_meters, 3)
                        len_m = lat_geom.length() / meters_to_map
                        if len_m > 0:
                            lat_attrs[lat_idx['slope_pct']] = round(100.0 * fall_m / len_m, 3)
                    lat_attrs[lat_idx['lateral_no']] = n
                    lat_attrs[lat_idx['azimuth']] = round(azimuth, 3)
                    lat_attrs[lat_idx['tile_spacing']] = spacing
                    lat_attrs[lat_idx['length']] = round(
                        lat_geom.length() / meters_to_map / unit_to_meters, 2)
                    lat_attrs[lat_idx['units']] = unit_label
                    if status:
                        lat_attrs[lat_idx['connection']] = status
                        lat_attrs[lat_idx['conn_len']] = round(
                            clen / meters_to_map / unit_to_meters, 2)
                    lat_feat = QgsFeature(lat_fields)
                    lat_feat.setGeometry(lat_geom)
                    lat_feat.setAttributes(lat_attrs)
                    lat_sink.addFeature(lat_feat, QgsFeatureSink.FastInsert)
                lateral_total += len(laterals)
                if not laterals:
                    feedback.reportError(self.tr('Feature {}: no laterals fit inside the buffer.')
                                         .format(feat.id()))

        feedback.pushInfo(self.tr('Created guides for {} field(s); {} skipped.')
                          .format(written, skipped))
        if lat_sink is not None:
            feedback.pushInfo(self.tr('Created {} lateral(s).').format(lateral_total))
        if mains_index is not None:
            feedback.pushInfo(self.tr(
                'Connections: {crossing} split at a crossing main, {extended} extended, '
                '{connector} with a connector, {none} not connected.').format(**connect_counts))
            if connect_counts['none']:
                feedback.reportError(self.tr(
                    '{} lateral piece(s) are not connected; filter connection = \'none\' '
                    'to review them.').format(connect_counts['none']))

        if flow_counts:
            feedback.pushInfo(self.tr('Flow direction: {}.').format(
                ', '.join('{} {}'.format(n, k) for k, n in sorted(flow_counts.items()))))
            bad = flow_counts.get('away_from_main', 0) + flow_counts.get('uphill_to_main', 0)
            if bad:
                feedback.reportError(self.tr(
                    '{} lateral(s) slope away from their main on the surface; filter '
                    'flow = \'away_from_main\' or \'uphill_to_main\' to review them '
                    '(they need a deeper cut at the main end or a different main).')
                    .format(bad))

        # Style the outputs when they are loaded into the project
        if context.willLoadLayerOnCompletion(line_id):
            context.layerToLoadOnCompletionDetails(line_id).setPostProcessor(
                _GuideLineStyler.create())
        if area_id and context.willLoadLayerOnCompletion(area_id):
            context.layerToLoadOnCompletionDetails(area_id).setPostProcessor(
                _AreaStyler.create())

        if lat_id and context.willLoadLayerOnCompletion(lat_id):
            context.layerToLoadOnCompletionDetails(lat_id).setPostProcessor(
                _LateralStyler.create())

        results = {self.OUTPUT_LINE: line_id}
        if lat_id:
            results[self.OUTPUT_LATERALS] = lat_id
        if junc_id:
            results[self.OUTPUT_JUNCTIONS] = junc_id
        if area_id:
            results[self.OUTPUT_AREA] = area_id
        return results
