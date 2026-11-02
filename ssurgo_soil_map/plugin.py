import os

from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QIcon

try:
    from qgis.PyQt.QtGui import QAction  # Qt6
except ImportError:
    from qgis.PyQt.QtWidgets import QAction  # Qt5

from .provider import SsurgoProvider
from .trace_tool import FieldTraceTool

MENU = "&SSURGO Soil Map"
ALG_ID = "ssurgosoilmap:soilmap"


class SsurgoSoilMapPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None
        self.trace_action = None
        self.trace_tool = None
        self.previous_tool = None

    def initProcessing(self):
        self.provider = SsurgoProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        here = os.path.dirname(__file__)

        self.action = QAction(QIcon(os.path.join(here, "icon.png")),
                              "Get Soil Map for Polygons…", self.iface.mainWindow())
        self.action.triggered.connect(self.run)

        self.trace_action = QAction(QIcon(os.path.join(here, "icon_trace.png")),
                                    "Trace Field and Get Soil Map", self.iface.mainWindow())
        self.trace_action.setCheckable(True)
        self.trace_action.triggered.connect(self.start_trace)

        self.trace_tool = FieldTraceTool(self.iface)
        self.trace_tool.setAction(self.trace_action)
        self.trace_tool.traced.connect(self.on_traced)

        for act in (self.action, self.trace_action):
            self.iface.addPluginToVectorMenu(MENU, act)
            self.iface.addToolBarIcon(act)

    def unload(self):
        canvas = self.iface.mapCanvas()
        if self.trace_tool is not None and canvas.mapTool() is self.trace_tool:
            canvas.unsetMapTool(self.trace_tool)
        for act in (self.action, self.trace_action):
            if act:
                self.iface.removePluginVectorMenu(MENU, act)
                self.iface.removeToolBarIcon(act)
        self.action = self.trace_action = self.trace_tool = None
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def run(self, _checked=False, params=None):
        import processing
        processing.execAlgorithmDialog(ALG_ID, params or {})

    def start_trace(self, checked=True):
        canvas = self.iface.mapCanvas()
        if checked:
            current = canvas.mapTool()
            if current is not self.trace_tool:
                self.previous_tool = current
            canvas.setMapTool(self.trace_tool)
        else:
            self._restore_tool()

    def _restore_tool(self):
        canvas = self.iface.mapCanvas()
        if self.previous_tool is not None:
            try:
                canvas.setMapTool(self.previous_tool)
            except RuntimeError:  # the previous tool was deleted
                canvas.unsetMapTool(self.trace_tool)
        else:
            canvas.unsetMapTool(self.trace_tool)
        self.previous_tool = None

    def on_traced(self, layer):
        self._restore_tool()
        self.run(params={"INPUT": layer.id(), "OUTLINE": True})
