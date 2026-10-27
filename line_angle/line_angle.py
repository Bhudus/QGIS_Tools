"""Main plugin class for Line Angle."""
import os

from qgis.core import Qgis, QgsApplication
from qgis.PyQt.QtGui import QIcon, QGuiApplication
from qgis.PyQt.QtWidgets import QAction

from .angle_tool import LineAngleMapTool
from .provider import LineAngleProvider
from .two_point_tool import TwoPointAngleMapTool

MENU = "&Line Angle"


class LineAnglePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.tool = None
        self.two_point_action = None
        self.two_point_tool = None
        self.provider = None

    def initProcessing(self):
        if self.provider is None:
            self.provider = LineAngleProvider()
            QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()

        # Tool 1: click an existing line segment
        self.action = QAction(
            QIcon(os.path.join(self.plugin_dir, "icon.png")),
            "Line Angle – Pick Segment", self.iface.mainWindow(),
        )
        self.action.setCheckable(True)
        self.action.setToolTip(
            "Line Angle: click a line segment to get its angle (degrees, "
            "counter-clockwise from the positive horizontal axis)"
        )
        self.action.triggered.connect(self.activate)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(MENU, self.action)

        self.tool = LineAngleMapTool(self.canvas)
        self.tool.setAction(self.action)
        self.tool.angleMeasured.connect(self.show_angle)
        self.tool.message.connect(self.show_warning)

        # Tool 2: click two points
        self.two_point_action = QAction(
            QIcon(os.path.join(self.plugin_dir, "icon_two_points.png")),
            "Line Angle – Two Points", self.iface.mainWindow(),
        )
        self.two_point_action.setCheckable(True)
        self.two_point_action.setToolTip(
            "Line Angle: click a start point and an end point to get the angle of the "
            "segment between them (snapping supported)"
        )
        self.two_point_action.triggered.connect(self.activate_two_point)
        self.iface.addToolBarIcon(self.two_point_action)
        self.iface.addPluginToMenu(MENU, self.two_point_action)

        self.two_point_tool = TwoPointAngleMapTool(self.canvas)
        self.two_point_tool.setAction(self.two_point_action)
        self.two_point_tool.angleMeasured.connect(self.show_two_point_angle)
        self.two_point_tool.liveAngle.connect(self.show_live_angle)
        self.two_point_tool.message.connect(self.show_info)

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
        for tool in (self.tool, self.two_point_tool):
            if tool is not None and self.canvas.mapTool() is tool:
                self.canvas.unsetMapTool(tool)
        for action in (self.action, self.two_point_action):
            if action is not None:
                self.iface.removePluginMenu(MENU, action)
                self.iface.removeToolBarIcon(action)
        self.iface.mainWindow().statusBar().clearMessage()
        self.tool = self.two_point_tool = None
        self.action = self.two_point_action = None

    # ------------------------------------------------------------- activation
    def activate(self, checked=True):
        self.canvas.setMapTool(self.tool)
        self.iface.messageBar().pushMessage(
            "Line Angle", "Click on a line segment to measure its angle.",
            level=Qgis.Info, duration=4,
        )

    def activate_two_point(self, checked=True):
        self.canvas.setMapTool(self.two_point_tool)
        self.iface.messageBar().pushMessage(
            "Line Angle",
            "Click the start point, then the end point. Right-click or Esc cancels.",
            level=Qgis.Info, duration=4,
        )

    # ---------------------------------------------------------------- output
    def _report(self, angle, undirected, p1, p2, source):
        text = (
            f"{angle:.4f}°  (orientation {undirected:.4f}°)  — {source} "
            f"({p1.x():.3f}, {p1.y():.3f}) → ({p2.x():.3f}, {p2.y():.3f})"
        )
        QGuiApplication.clipboard().setText(f"{angle:.4f}")
        self.iface.messageBar().pushMessage(
            "Line Angle", text + "  [angle copied to clipboard]",
            level=Qgis.Success, duration=0,
        )

    def show_angle(self, angle, undirected, p1, p2, layer_name):
        self._report(angle, undirected, p1, p2, f"layer '{layer_name}', segment")

    def show_two_point_angle(self, angle, undirected, p1, p2):
        self._report(angle, undirected, p1, p2, "points")

    def show_live_angle(self, text):
        bar = self.iface.mainWindow().statusBar()
        if text:
            bar.showMessage(text)
        else:
            bar.clearMessage()

    def show_info(self, msg):
        self.iface.messageBar().pushMessage("Line Angle", msg, level=Qgis.Info, duration=3)

    def show_warning(self, msg):
        self.iface.messageBar().pushMessage("Line Angle", msg, level=Qgis.Warning, duration=3)
