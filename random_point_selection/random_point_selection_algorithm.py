# -*- coding: utf-8 -*-
"""
Random Point Selection - Processing algorithm.

Randomly selects a specified NUMBER or PERCENTAGE of the features of a point
layer and writes them to a new layer (optionally also writing the points that
were not selected).  Every feature is treated as one unit, so a multipoint
feature is selected or left out as a whole.
"""
import os
import random

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.core import (
    Qgis,
    QgsFeatureRequest,
    QgsFeatureSink,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
)


# Enum compatibility: new-style names (QGIS >= 3.36) with a fallback to the
# old-style names used by earlier 3.x releases.
def _pick(*getters):
    for getter in getters:
        try:
            return getter()
        except AttributeError:
            continue
    raise AttributeError('No compatible QGIS enum found')


_TYPE_POINT = _pick(
    lambda: Qgis.ProcessingSourceType.VectorPoint,
    lambda: QgsProcessing.TypeVectorPoint)
_NUM_INTEGER = _pick(
    lambda: Qgis.ProcessingNumberParameterType.Integer,
    lambda: QgsProcessingParameterNumber.Integer)
_NUM_DOUBLE = _pick(
    lambda: Qgis.ProcessingNumberParameterType.Double,
    lambda: QgsProcessingParameterNumber.Double)

_NO_GEOMETRY = _pick(
    lambda: Qgis.FeatureRequestFlag.NoGeometry,
    lambda: QgsFeatureRequest.NoGeometry)

BY_NUMBER = 0
BY_PERCENTAGE = 1


class RandomPointSelection(QgsProcessingAlgorithm):

    INPUT = 'INPUT'
    METHOD = 'METHOD'
    NUMBER = 'NUMBER'
    PERCENTAGE = 'PERCENTAGE'
    SEED = 'SEED'
    OUTPUT = 'OUTPUT'
    OUTPUT_UNSELECTED = 'OUTPUT_UNSELECTED'
    SELECTED_COUNT = 'SELECTED_COUNT'

    def tr(self, string):
        return QCoreApplication.translate('RandomPointSelection', string)

    def createInstance(self):  # noqa: N802
        return RandomPointSelection()

    def name(self):
        return 'randompointselection'

    def displayName(self):  # noqa: N802
        return self.tr('Random Point Selection')

    def group(self):
        return self.tr('Random selection')

    def groupId(self):  # noqa: N802
        return 'randomselection'

    def icon(self):
        return QIcon(os.path.join(os.path.dirname(__file__), 'icon.svg'))

    def shortDescription(self):  # noqa: N802
        return self.tr('Randomly selects a number or percentage of the points of a layer.')

    def shortHelpString(self):  # noqa: N802
        return self.tr(
            '<p>Randomly selects points from a point layer, either a '
            'specified <b>number</b> of points or a specified '
            '<b>percentage</b> of them, and saves them to a new layer with '
            'all of the original attributes. Each point is selected at most '
            'once (sampling without replacement) and every point has the '
            'same chance of being chosen.</p>'
            '<h3>Parameters</h3>'
            '<p><b>Input points</b> - point layer. To sample only some of '
            'its features, select them first and tick '
            '"Selected features only".</p>'
            '<p><b>Selection method</b> - choose whether to select a fixed '
            'number of points or a percentage of them.</p>'
            '<p><b>Number of points</b> - used when the method is "Number of '
            'points". If it exceeds the number of available points, all '
            'points are selected.</p>'
            '<p><b>Percentage of points</b> - used when the method is '
            '"Percentage of points". The count is rounded to the nearest '
            'whole point, but at least one point is selected whenever the '
            'percentage is above 0.</p>'
            '<p><b>Random seed</b> - optional. Using the same seed on the '
            'same data gives the same selection again.</p>'
            '<p><b>Selected points</b> - the output layer. '
            '<b>Not selected points</b> is an optional second output that '
            'holds the remaining points (for example as a validation '
            'set).</p>')

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr('Input points'), [_TYPE_POINT]))

        self.addParameter(QgsProcessingParameterEnum(
            self.METHOD, self.tr('Selection method'),
            options=[self.tr('Number of points'), self.tr('Percentage of points')],
            allowMultiple=False, defaultValue=BY_NUMBER))

        self.addParameter(QgsProcessingParameterNumber(
            self.NUMBER, self.tr('Number of points (used by "Number of points")'),
            type=_NUM_INTEGER, defaultValue=100, minValue=0))

        self.addParameter(QgsProcessingParameterNumber(
            self.PERCENTAGE, self.tr('Percentage of points (used by "Percentage of points")'),
            type=_NUM_DOUBLE, defaultValue=10.0, minValue=0.0, maxValue=100.0))

        self.addParameter(QgsProcessingParameterNumber(
            self.SEED, self.tr('Random seed (optional - same seed gives the same selection)'),
            type=_NUM_INTEGER, optional=True, minValue=0))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr('Selected points'), type=_TYPE_POINT))

        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_UNSELECTED, self.tr('Not selected points'), type=_TYPE_POINT,
            optional=True, createByDefault=False))

        self.addOutput(QgsProcessingOutputNumber(
            self.SELECTED_COUNT, self.tr('Number of selected points')))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))

        method = self.parameterAsEnum(parameters, self.METHOD, context)
        number = self.parameterAsInt(parameters, self.NUMBER, context)
        percentage = self.parameterAsDouble(parameters, self.PERCENTAGE, context)

        rng = random.Random()
        if parameters.get(self.SEED) not in (None, ''):
            rng.seed(self.parameterAsInt(parameters, self.SEED, context))

        sink, sink_id = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            source.fields(), source.wkbType(), source.sourceCrs())
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        rest_sink = rest_id = None
        if parameters.get(self.OUTPUT_UNSELECTED) not in (None, ''):
            rest_sink, rest_id = self.parameterAsSink(
                parameters, self.OUTPUT_UNSELECTED, context,
                source.fields(), source.wkbType(), source.sourceCrs())

        # ---- pass 1: collect feature ids (no geometry, no attributes) -------------
        id_request = QgsFeatureRequest().setFlags(_NO_GEOMETRY)
        id_request.setSubsetOfAttributes([])
        ids = []
        for feat in source.getFeatures(id_request):
            if feedback.isCanceled():
                return {}
            ids.append(feat.id())
        total = len(ids)
        if total == 0:
            raise QgsProcessingException(self.tr('The input layer contains no points.'))

        # ---- how many? --------------------------------------------------------------
        if method == BY_PERCENTAGE:
            count = int(round(total * percentage / 100.0))
            if percentage > 0.0 and count == 0:
                count = 1
            feedback.pushInfo(self.tr('{}% of {} point(s) = {} point(s)').format(
                percentage, total, count))
        else:
            count = number
            if count > total:
                feedback.pushWarning(self.tr(
                    'Requested {} points but only {} are available - selecting all '
                    'of them.').format(count, total))
                count = total
        count = max(0, min(count, total))

        chosen = set(rng.sample(ids, count))

        # ---- pass 2: write ------------------------------------------------------------
        for i, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                return {}
            if feat.id() in chosen:
                sink.addFeature(feat, QgsFeatureSink.FastInsert)
            elif rest_sink is not None:
                rest_sink.addFeature(feat, QgsFeatureSink.FastInsert)
            feedback.setProgress(100.0 * (i + 1) / total)

        feedback.pushInfo(self.tr('Selected {} of {} point(s)').format(count, total))

        results = {self.OUTPUT: sink_id, self.SELECTED_COUNT: count}
        if rest_id is not None:
            results[self.OUTPUT_UNSELECTED] = rest_id
        return results
