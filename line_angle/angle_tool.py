"""Map tool: click a line to get the angle of the segment under the cursor."""
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor

from .angle_utils import segment_angle, undirected_angle


class LineAngleMapTool(QgsMapTool):
    """Click near a line feature; emits the angle of the nearest segment."""

    angleMeasured = pyqtSignal(float, float, QgsPointXY, QgsPointXY, str)
    message = pyqtSignal(str)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.setCursor(Qt.CrossCursor)
        self.band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.band.setColor(QColor(255, 0, 120, 220))
        self.band.setWidth(4)

    # ------------------------------------------------------------------ helpers
    def _line_layers(self):
        """Active layer first (if it is a line layer), then all other visible line layers."""
        layers = []
        active = self.canvas.currentLayer()
        if isinstance(active, QgsVectorLayer) and active.geometryType() == QgsWkbTypes.LineGeometry:
            layers.append(active)
        for lyr in self.canvas.layers():
            if (
                isinstance(lyr, QgsVectorLayer)
                and lyr.geometryType() == QgsWkbTypes.LineGeometry
                and lyr not in layers
            ):
                layers.append(lyr)
        return layers

    def _find_segment(self, map_point):
        """Return (p1, p2, layer_name) of the closest segment, in canvas CRS, or None."""
        radius = QgsMapTool.searchRadiusMU(self.canvas)
        search_rect = QgsRectangle(
            map_point.x() - radius, map_point.y() - radius,
            map_point.x() + radius, map_point.y() + radius,
        )
        dest_crs = self.canvas.mapSettings().destinationCrs()
        best = None  # (sqr_dist, p1, p2, layer_name)

        for layer in self._line_layers():
            layer_rect = self.toLayerCoordinates(layer, search_rect)
            xform = QgsCoordinateTransform(layer.crs(), dest_crs, QgsProject.instance())
            request = QgsFeatureRequest().setFilterRect(layer_rect)
            for feat in layer.getFeatures(request):
                geom = QgsGeometry(feat.geometry())
                if geom.isEmpty():
                    continue
                # Work in canvas CRS so "horizontal" means horizontal on screen
                if layer.crs() != dest_crs:
                    geom.transform(xform)
                sqr_dist, _closest, next_idx, _left = geom.closestSegmentWithContext(map_point)
                if next_idx <= 0 or sqr_dist > radius * radius:
                    continue
                if best is None or sqr_dist < best[0]:
                    p1 = QgsPointXY(geom.vertexAt(next_idx - 1))
                    p2 = QgsPointXY(geom.vertexAt(next_idx))
                    best = (sqr_dist, p1, p2, layer.name())
            if best is not None and layer is self.canvas.currentLayer():
                break  # prefer a hit on the active layer
        return None if best is None else best[1:]

    # ------------------------------------------------------------------ events
    def canvasReleaseEvent(self, event):
        map_point = self.toMapCoordinates(event.pos())
        found = self._find_segment(map_point)
        self.band.reset(QgsWkbTypes.LineGeometry)
        if not found:
            self.message.emit("No line segment found under the cursor.")
            return
        p1, p2, layer_name = found
        angle = segment_angle(p1.x(), p1.y(), p2.x(), p2.y())
        if angle is None:
            self.message.emit("The segment has zero length; angle is undefined.")
            return
        self.band.addPoint(p1, False)
        self.band.addPoint(p2, True)
        self.angleMeasured.emit(angle, undirected_angle(angle), p1, p2, layer_name)

    def deactivate(self):
        self.band.reset(QgsWkbTypes.LineGeometry)
        super().deactivate()
