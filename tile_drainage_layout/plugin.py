import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication, QgsVectorLayer, QgsWkbTypes

from .provider import TileDrainageProvider

ALG_ID = 'tiledrainage:tiledrainagelayout'
MENU = '&Tile Drainage'


class TileDrainageLayoutPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):  # noqa: N802 (QGIS API name)
        # QGIS calls this itself because metadata.txt has hasProcessingProvider=yes.
        # Guard so a second call can't register a duplicate provider (QGIS would
        # reject and delete it, leaving a dead reference behind).
        if self.provider is not None:
            return
        registry = QgsApplication.processingRegistry()
        stale = registry.providerById('tiledrainage')
        if stale is not None:
            registry.removeProvider(stale)  # left over from a failed earlier load
        provider = TileDrainageProvider()
        if registry.addProvider(provider):
            self.provider = provider

    def initGui(self):  # noqa: N802 (QGIS API name)
        self.initProcessing()
        icon = QIcon(os.path.join(os.path.dirname(__file__), 'icon.png'))
        self.action = QAction(icon, 'Tile Drainage Layout…', self.iface.mainWindow())
        self.action.setToolTip('Setback guide, laterals and main connections for tile drainage')
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToVectorMenu(MENU, self.action)

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginVectorMenu(MENU, self.action)
            self.action = None
        if self.provider is not None:
            try:
                QgsApplication.processingRegistry().removeProvider(self.provider)
            except RuntimeError:
                pass  # already deleted by QGIS
            self.provider = None

    def run(self):
        import processing
        params = {}
        layer = self.iface.activeLayer()
        if isinstance(layer, QgsVectorLayer) and layer.geometryType() == QgsWkbTypes.PolygonGeometry:
            params['INPUT'] = layer.id()
        processing.execAlgorithmDialog(ALG_ID, params)
