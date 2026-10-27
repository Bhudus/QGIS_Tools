"""Map tool: click two points to get the angle of the segment they form."""
from qgis.core import QgsPointLocator, QgsPointXY, QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand, QgsSnapIndicator
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor

from .angle_utils import segment_angle, undirected_angle


class TwoPointAngleMapTool(QgsMapTool):
    """Click a start point, then an end point.

    Emits the angle of start -> end in degrees, counter-clockwise from the positive
    horizontal axis, in the map canvas CRS. Respects the project's snapping settings.
    Right-click or Esc cancels the current measurement.
    """

    angleMeasured = pyqtSignal(float, float, QgsPointXY, QgsPointXY)
    liveAngle = pyqtSignal(str)
    message = pyqtSignal(str)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.setCursor(Qt.CrossCursor)
        self.start = None
        self.band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.band.setColor(QColor(0, 120, 255, 220))
        self.band.setWidth(3)
        self.snap_indicator = QgsSnapIndicator(canvas)

    def _snapped_point(self, event):
        """Map point under the cursor, snapped if project snapping is enabled."""
        match = self.canvas.snappingUtils().snapToMap(event.pos())
        self.snap_indicator.setMatch(match)
        if match.isValid():
            return QgsPointXY(match.point())
        return self.toMapCoordinates(event.pos())

    def _reset(self):
        self.start = None
        self.band.reset(QgsWkbTypes.LineGeometry)
        self.liveAngle.emit("")

    def canvasMoveEvent(self, event):
        point = self._snapped_point(event)
        if self.start is None:
            return
        self.band.reset(QgsWkbTypes.LineGeometry)
        self.band.addPoint(self.start, False)
        self.band.addPoint(point, True)
        angle = segment_angle(self.start.x(), self.start.y(), point.x(), point.y())
        if angle is not None:
            self.liveAngle.emit(f"Line Angle: {angle:.2f}°")

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self._reset()
            return
        point = self._snapped_point(event)

        if self.start is None:
            self.start = point
            self.message.emit("Start point set — click the end point (right-click or Esc to cancel).")
            return

        angle = segment_angle(self.start.x(), self.start.y(), point.x(), point.y())
        if angle is None:
            self.message.emit("Both points are the same; click a different end point.")
            return
        start = self.start
        self.start = None  # keep the band visible until the next measurement starts
        self.band.reset(QgsWkbTypes.LineGeometry)
        self.band.addPoint(start, False)
        self.band.addPoint(point, True)
        self.liveAngle.emit("")
        self.angleMeasured.emit(angle, undirected_angle(angle), start, point)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._reset()
            event.accept()

    def deactivate(self):
        self._reset()
        self.snap_indicator.setMatch(QgsPointLocator.Match())
        super().deactivate()
