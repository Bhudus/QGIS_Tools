# -*- coding: utf-8 -*-
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsProject)
from qgis.gui import QgsMapToolEmitPoint
from qgis.PyQt.QtCore import Qt, pyqtSignal


class PourPointTool(QgsMapToolEmitPoint):
    """Left-click on the map -> emits (lat, lon, point_in_canvas_crs)."""

    picked = pyqtSignal(float, float, object)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.canvasClicked.connect(self._clicked)

    def _clicked(self, point, button):
        if button != Qt.MouseButton.LeftButton:
            return
        src = self.canvas.mapSettings().destinationCrs()
        xform = QgsCoordinateTransform(src, QgsCoordinateReferenceSystem("EPSG:4326"),
                                       QgsProject.instance())
        ll = xform.transform(point)
        self.picked.emit(ll.y(), ll.x(), point)
