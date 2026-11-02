# -*- coding: utf-8 -*-
"""Map tool for tracing a field boundary polygon on the map canvas."""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.core import QgsGeometry, QgsPointXY, QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand


class TraceFieldTool(QgsMapTool):
    """Left-click adds a corner (snapping honoured), right-click or Enter finishes,
    Backspace removes the last corner, Esc cancels."""

    finished = pyqtSignal(object)   # QgsGeometry in canvas CRS
    canceled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self.points = []
        self.band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        self.band.setColor(QColor(47, 107, 79, 200))
        self.band.setFillColor(QColor(47, 107, 79, 50))
        self.band.setWidth(2)
        self.setCursor(Qt.CrossCursor)

    # ------------------------------------------------------------------
    def _point(self, e):
        try:
            return QgsPointXY(e.snapPoint())        # honours project snapping
        except Exception:
            return self.toMapCoordinates(e.pos())

    def _redraw(self, hover=None):
        self.band.reset(QgsWkbTypes.PolygonGeometry)
        pts = self.points + ([hover] if hover is not None else [])
        for i, p in enumerate(pts):
            self.band.addPoint(p, i == len(pts) - 1)

    def canvasMoveEvent(self, e):  # noqa: N802
        if self.points:
            self._redraw(self._point(e))

    def canvasReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.points.append(self._point(e))
            self._redraw()
        elif e.button() == Qt.RightButton:
            self._finish()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key_Backspace, Qt.Key_Delete) and self.points:
            self.points.pop()
            self._redraw()
            e.accept()
        elif e.key() == Qt.Key_Escape:
            self.clear()
            self.canceled.emit()
            e.accept()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._finish()
            e.accept()

    def _finish(self):
        if len(self.points) < 3:
            return                                 # keep tracing; need a polygon
        geom = QgsGeometry.fromPolygonXY([self.points + [self.points[0]]])
        if not geom.isGeosValid():
            fixed = geom.makeValid()
            if fixed and not fixed.isEmpty():
                geom = fixed
        self.clear()
        self.finished.emit(geom)

    def clear(self):
        self.points = []
        self.band.reset(QgsWkbTypes.PolygonGeometry)

    def deactivate(self):
        self.band.reset(QgsWkbTypes.PolygonGeometry)
        super().deactivate()
