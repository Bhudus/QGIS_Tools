"""Qt widget that paints a ChartModel's drawing instructions."""
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF
from qgis.PyQt.QtWidgets import QSizePolicy, QWidget


def _color(c):
    return QColor(*c) if c is not None else None


class ProfileChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = None
        self.message = "Select a lateral or main in the network layer to see its profile."
        self.hover_x = None
        self.setMouseTracking(True)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._fonts = {}

    # ------------------------------------------------------------ state
    def set_model(self, model):
        self.model = model
        self.hover_x = None
        self.update()

    def set_message(self, text):
        self.model = None
        self.message = text
        self.update()

    # ------------------------------------------------------------ painting
    def _font(self, size, bold):
        key = (size, bold)
        if key not in self._fonts:
            f = QFont(self.font())
            f.setPixelSize(max(int(round(size)), 6))
            f.setBold(bool(bold))
            self._fonts[key] = f
        return self._fonts[key]

    def paint_to(self, painter, w, h, hover=True):
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self.model is None:
            painter.fillRect(QRectF(0, 0, w, h), QColor(255, 255, 255))
            painter.setPen(QColor(110, 110, 110))
            painter.setFont(self._font(13, False))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignCenter | Qt.TextWordWrap, self.message)
            return
        instrs = self.model.scene(w, h)
        if hover and self.hover_x is not None:
            instrs = instrs + self.model.hover(self.hover_x)
        for ins in instrs:
            self._draw(painter, ins)

    def _draw(self, p, ins):
        kind = ins[0]
        if kind == "poly":
            _, pts, color, width, dash = ins
            pen = QPen(_color(color))
            pen.setWidthF(width)
            pen.setCapStyle(Qt.FlatCap if dash else Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            if dash:
                pen.setDashPattern([float(dash[0]), float(dash[1])])
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in pts]))
        elif kind == "fill":
            _, pts, color = ins
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(_color(color)))
            p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
        elif kind == "rect":
            _, x, y, w, h, fill, outline = ins
            p.setPen(QPen(_color(outline)) if outline is not None else Qt.NoPen)
            p.setBrush(QBrush(_color(fill)) if fill is not None else Qt.NoBrush)
            p.drawRect(QRectF(x, y, w, h))
        elif kind == "dot":
            _, x, y, r, color = ins
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(_color(color)))
            p.drawEllipse(QPointF(x, y), r, r)
        elif kind == "tri":
            _, x, y, s, color = ins
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(_color(color)))
            p.drawPolygon(QPolygonF([QPointF(x - s / 2, y - s / 2), QPointF(x + s / 2, y - s / 2),
                                     QPointF(x, y + s / 2)]))
        elif kind == "text":
            _, x, y, text, color, size, align, bold = ins
            font = self._font(size, bold)
            fm = QFontMetricsF(font)
            try:
                tw = fm.horizontalAdvance(text)
            except AttributeError:          # Qt < 5.11
                tw = fm.width(text)
            h = fm.ascent() + fm.descent()
            if align[0] == "r":
                x -= tw
            elif align[0] == "c":
                x -= tw / 2
            v = align[1] if len(align) > 1 else "t"
            top = y - (h / 2 if v == "m" else h if v == "b" else 0)
            p.setFont(font)
            p.setPen(_color(color))
            p.drawText(QPointF(x, top + fm.ascent()), text)

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            self.paint_to(p, self.width(), self.height())
        finally:
            p.end()

    # ------------------------------------------------------------ hover
    def mouseMoveEvent(self, event):
        if self.model is None:
            return
        try:
            pos = event.position()          # Qt 6
        except AttributeError:
            pos = event.pos()
        x, y = pos.x(), pos.y()
        self.model.layout(self.width(), self.height())
        new = x if self.model.inside(x, y) else None
        if new != self.hover_x:
            self.hover_x = new
            self.update()

    def leaveEvent(self, event):
        if self.hover_x is not None:
            self.hover_x = None
            self.update()
