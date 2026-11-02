# -*- coding: utf-8 -*-
"""Processing algorithm: list the LiDAR collections available for a field."""

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsWkbTypes,
    QgsCoordinateReferenceSystem,
)

from .collections import describe, field_to_wgs, list_collections


class ListLidarCollections(QgsProcessingAlgorithm):

    INPUT = "INPUT"
    OUTPUT = "OUTPUT"

    def tr(self, s):
        return QCoreApplication.translate("ListLidarCollections", s)

    def createInstance(self):
        return ListLidarCollections()

    def name(self):
        return "listlidarcollections"

    def displayName(self):
        return self.tr("Available LiDAR collections for a field")

    def group(self):
        return self.tr("LiDAR")

    def groupId(self):
        return "lidar"

    def shortHelpString(self):
        return self.tr(
            "Lists every ISGS (Illinois) and USGS 3DEP LiDAR collection that covers the "
            "field, newest first, with collection year, native DEM cell size, quality level "
            "and the share of the field each one covers.\n\n"
            "Use a year from this list in 'Collection year' of the DEM tool.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Field boundary"), [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Available LiDAR collections"), QgsProcessing.TypeVector))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        geoms = [f.geometry() for f in source.getFeatures() if f.hasGeometry()]
        field_wgs = field_to_wgs(geoms, source.sourceCrs(), context.transformContext())

        rows = list_collections(field_wgs, feedback)

        fields = QgsFields()
        fields.append(QgsField("source", QVariant.String))
        fields.append(QgsField("collection", QVariant.String))
        fields.append(QgsField("year", QVariant.String))
        fields.append(QgsField("status", QVariant.String))
        fields.append(QgsField("quality_lvl", QVariant.Int))
        fields.append(QgsField("cell_ft", QVariant.Double))
        fields.append(QgsField("cover_pct", QVariant.Double))
        fields.append(QgsField("usable", QVariant.Bool))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields,
                                          QgsWkbTypes.NoGeometry, QgsCoordinateReferenceSystem())

        if not rows:
            feedback.pushInfo("No LiDAR collections found for this field.")
        else:
            feedback.pushInfo("LiDAR collections covering the field (newest first):")
        for r in rows:
            feedback.pushInfo("  " + describe(r))
            f = QgsFeature(fields)
            f["source"] = r["source"]
            f["collection"] = r["collection"]
            f["year"] = r["year"]
            f["status"] = r["status"]
            f["quality_lvl"] = r["quality"] if isinstance(r["quality"], int) else None
            f["cell_ft"] = r["cell_ft"]
            f["cover_pct"] = None if r["coverage"] is None else round(r["coverage"], 1)
            f["usable"] = r["usable"]
            sink.addFeature(f, QgsFeatureSink.FastInsert)
        return {self.OUTPUT: dest}
