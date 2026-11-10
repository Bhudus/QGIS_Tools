import os

from qgis.core import QgsApplication, QgsProject, QgsRasterLayer, QgsVectorLayer, QgsWkbTypes
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .provider import MainTileLocatorProvider

ALGORITHM_ID = "illinimaintilelocator:illinimaintilelocator"
MENU = "&Illini Main Tile Locator"


def _is_raster(layer):
    return isinstance(layer, QgsRasterLayer)


def _is_polygon(layer):
    return (isinstance(layer, QgsVectorLayer)
            and layer.geometryType() == QgsWkbTypes.PolygonGeometry)


class MainTileLocatorPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):
        if self.provider is not None:
            return
        self.provider = MainTileLocatorProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        self.action = QAction(icon, "Illini main tile locator…", self.iface.mainWindow())
        self.action.setToolTip("Locate a tile main from an outlet, a DEM and a field boundary")
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(MENU, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu(MENU, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def _defaults(self):
        """Prefill the DEM and field with the active layer, else the first suitable layer."""
        params = {}
        active = self.iface.activeLayer()
        layers = list(QgsProject.instance().mapLayers().values())
        dem = active if _is_raster(active) else next((l for l in layers if _is_raster(l)), None)
        fld = active if _is_polygon(active) else next((l for l in layers if _is_polygon(l)),
                                                      None)
        if dem is not None:
            params["DEM"] = dem.id()
        if fld is not None:
            params["FIELD"] = fld.id()
        return params

    def run(self):
        import processing
        processing.execAlgorithmDialog(ALGORITHM_ID, self._defaults())
