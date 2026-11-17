import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .drainage_algorithm import DrainageReportAlgorithm
from .subirrigation_algorithm import SubirrigationReportAlgorithm


class DesignReportsProvider(QgsProcessingProvider):
    def id(self):
        return "designreports"

    def name(self):
        return "Design Reports"

    def longName(self):
        return "Drainage and subirrigation design reports"

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))

    def loadAlgorithms(self):
        self.addAlgorithm(DrainageReportAlgorithm())
        self.addAlgorithm(SubirrigationReportAlgorithm())
