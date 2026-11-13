"""Map tool for tracing a field boundary by clicking on the map canvas."""
from qgis.core import (
    Qgis,
    QgsDistanceArea,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor

SQ_M_PER_HA = 10000.0


def _enum(owner, name, scope):
    value = getattr(owner, name, None)
    if value is None:
        value = getattr(getattr(owner, scope), name)
    return value


try:
    POLYGON_TYPE = Qgis.GeometryType.Polygon
except AttributeError:
    POLYGON_TYPE = QgsWkbTypes.PolygonGeometry

LEFT = _enum(Qt, "LeftButton", "MouseButton")
RIGHT = _enum(Qt, "RightButton", "MouseButton")
KEY_ESC = _enum(Qt, "Key_Escape", "Key")
KEY_BACKSPACE = _enum(Qt, "Key_Backspace", "Key")
KEY_DELETE = _enum(Qt, "Key_Delete", "Key")
KEY_RETURN = _enum(Qt, "Key_Return", "Key")
KEY_ENTER = _enum(Qt, "Key_Enter", "Key")
CROSS_CURSOR = _enum(Qt, "CrossCursor", "CursorShape")


class FieldTraceTool(QgsMapTool):
    """Left-click adds a corner, right-click or Enter finishes,
    Backspace removes the last corner, Esc starts over."""

    traced = pyqtSignal(object)  # emits the new QgsVectorLayer

    def __init__(self, iface):
        super().__init__(iface.mapCanvas())
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.points = []
        self.count = 0
        self.band = None
        self.setCursor(CROSS_CURSOR)

    # --- lifecycle -------------------------------------------------------
    def activate(self):
        super().activate()
        self._reset()
        self.iface.messageBar().pushInfo(
            "Trace field",
            "Click each corner of the field. Right-click or press Enter to finish. "
            "Backspace removes the last corner, Esc starts over.")

    def deactivate(self):
        self._clear_band()
        self.points = []
        super().deactivate()

    def _reset(self):
        self._clear_band()
        self.points = []
        self.band = QgsRubberBand(self.canvas, POLYGON_TYPE)
        self.band.setColor(QColor(255, 214, 0, 220))
        self.band.setFillColor(QColor(255, 214, 0, 50))
        self.band.setWidth(2)

    def _clear_band(self):
        if self.band is not None:
            self.band.reset(POLYGON_TYPE)
            self.canvas.scene().removeItem(self.band)
            self.band = None

    # --- mouse and keys ----------------------------------------------------
    def _map_point(self, event):
        match = self.canvas.snappingUtils().snapToMap(event.pos())
        if match.isValid():
            return QgsPointXY(match.point())
        return self.toMapCoordinates(event.pos())

    def canvasMoveEvent(self, event):
        if self.points and self.band is not None:
            self._redraw(self._map_point(event))

    def canvasReleaseEvent(self, event):
        if self.band is None:
            self._reset()
        if event.button() == LEFT:
            point = self._map_point(event)
            if not self.points or point != self.points[-1]:
                self.points.append(point)
            self._redraw()
        elif event.button() == RIGHT:
            self._finish()

    def keyPressEvent(self, event):
        key = event.key()
        if key == KEY_ESC:
            self._reset()
        elif key in (KEY_BACKSPACE, KEY_DELETE):
            if self.points:
                self.points.pop()
                self._redraw()
        elif key in (KEY_RETURN, KEY_ENTER):
            self._finish()

    def _redraw(self, cursor=None):
        pts = list(self.points) + ([cursor] if cursor is not None else [])
        self.band.reset(POLYGON_TYPE)
        for i, p in enumerate(pts):
            self.band.addPoint(p, i == len(pts) - 1)

    # --- finishing -----------------------------------------------------------
    def _finish(self):
        if len(self.points) < 3:
            self.iface.messageBar().pushWarning(
                "Trace field", "A field needs at least three corners. Keep clicking, or press Esc to start over.")
            return
        geom = QgsGeometry.fromPolygonXY([self.points + [self.points[0]]])
        if not geom.isGeosValid():
            geom = geom.makeValid()
        crs = self.canvas.mapSettings().destinationCrs()

        area = QgsDistanceArea()
        area.setSourceCrs(crs, QgsProject.instance().transformContext())
        area.setEllipsoid(QgsProject.instance().ellipsoid() or "EPSG:7030")
        hectares = area.measureArea(geom) / SQ_M_PER_HA

        self.count += 1
        layer = QgsVectorLayer("MultiPolygon", f"Traced field {self.count}", "memory")
        layer.setCrs(crs)
        from qgis.core import QgsField
        try:
            from qgis.PyQt.QtCore import QMetaType
            double_type = QMetaType.Type.Double
        except (ImportError, AttributeError):
            from qgis.PyQt.QtCore import QVariant
            double_type = QVariant.Double
        layer.dataProvider().addAttributes([QgsField("hectares", double_type, len=12, prec=2)])
        layer.updateFields()
        feat = QgsFeature(layer.fields())
        geom.convertToMultiType()
        feat.setGeometry(geom)
        feat.setAttributes([round(hectares, 2)])
        layer.dataProvider().addFeatures([feat])
        layer.updateExtents()

        from .style import apply_boundary_style
        apply_boundary_style(layer)
        QgsProject.instance().addMapLayer(layer)

        self._reset()
        self.iface.messageBar().pushSuccess("Trace field", f"Traced {hectares:,.2f} ha. Getting the soils…")
        self.traced.emit(layer)
