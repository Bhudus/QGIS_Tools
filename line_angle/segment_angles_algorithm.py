"""Processing algorithm: split lines into segments and write each segment's angle to a field."""
from qgis.core import (
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterString,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QVariant

from .angle_utils import segment_angle, undirected_angle


class SegmentAnglesAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    ANGLE_FIELD = "ANGLE_FIELD"
    ORIENT_FIELD = "ORIENT_FIELD"
    SKIP_ZERO = "SKIP_ZERO"
    OUTPUT = "OUTPUT"

    def tr(self, text):
        return QCoreApplication.translate("SegmentAnglesAlgorithm", text)

    def createInstance(self):
        return SegmentAnglesAlgorithm()

    def name(self):
        return "segmentangles"

    def displayName(self):
        return self.tr("Segment angles")

    def group(self):
        return self.tr("Line Angle")

    def groupId(self):
        return "lineangle"

    def shortHelpString(self):
        return self.tr(
            "Splits every line into its individual segments and writes each segment's "
            "angle to a new field.\n\n"
            "Angle: degrees counter-clockwise from the positive horizontal (east) axis, "
            "0-360, following the digitizing direction (vertex i to i+1).\n"
            "Orientation: the same angle ignoring direction, 0-180.\n\n"
            "Angles are computed in the input layer's CRS. Reproject first if you want them "
            "in another CRS. Curved lines are converted to straight segments; Z and M values "
            "are dropped.\n\n"
            "Extra output fields: part (multipart part index), seg_index (segment number "
            "within the part, starting at 0)."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT, self.tr("Input line layer"), [QgsProcessing.TypeVectorLine]
            )
        )
        self.addParameter(
            QgsProcessingParameterString(self.ANGLE_FIELD, self.tr("Angle field name"), "angle")
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.ORIENT_FIELD,
                self.tr("Orientation (0-180) field name - leave empty to skip"),
                "orient",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.SKIP_ZERO, self.tr("Skip zero-length segments"), True
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT, self.tr("Segments with angles"), QgsProcessing.TypeVectorLine
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, self.INPUT))

        angle_name = self.parameterAsString(parameters, self.ANGLE_FIELD, context).strip()
        orient_name = self.parameterAsString(parameters, self.ORIENT_FIELD, context).strip()
        skip_zero = self.parameterAsBoolean(parameters, self.SKIP_ZERO, context)
        if not angle_name:
            raise QgsProcessingException(self.tr("The angle field name cannot be empty."))

        src_fields = source.fields()
        new_names = ["part", "seg_index", angle_name] + ([orient_name] if orient_name else [])
        lower_existing = {f.name().lower() for f in src_fields}
        clashes = [n for n in new_names if n.lower() in lower_existing]
        if clashes:
            raise QgsProcessingException(
                self.tr("The input layer already has field(s): {}. Choose other names.").format(
                    ", ".join(clashes)
                )
            )
        if len({n.lower() for n in new_names}) != len(new_names):
            raise QgsProcessingException(self.tr("Output field names must be different."))

        fields = QgsFields(src_fields)
        fields.append(QgsField("part", QVariant.Int))
        fields.append(QgsField("seg_index", QVariant.Int))
        fields.append(QgsField(angle_name, QVariant.Double, len=12, prec=6))
        if orient_name:
            fields.append(QgsField(orient_name, QVariant.Double, len=12, prec=6))

        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, QgsWkbTypes.LineString, source.sourceCrs()
        )
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        total = 100.0 / source.featureCount() if source.featureCount() else 0
        written = 0
        for current, feat in enumerate(source.getFeatures()):
            if feedback.isCanceled():
                break
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                feedback.setProgress(int(current * total))
                continue

            g = QgsGeometry(geom)
            if QgsWkbTypes.isCurvedType(g.wkbType()):
                g.convertToStraightSegment()
            parts = g.asMultiPolyline() if g.isMultipart() else [g.asPolyline()]

            for part_idx, pts in enumerate(parts):
                seg_idx = 0
                for p1, p2 in zip(pts, pts[1:]):
                    angle = segment_angle(p1.x(), p1.y(), p2.x(), p2.y())
                    if angle is None and skip_zero:
                        continue
                    out = QgsFeature(fields)
                    out.setGeometry(QgsGeometry.fromPolylineXY([p1, p2]))
                    attrs = list(feat.attributes()) + [part_idx, seg_idx, angle]
                    if orient_name:
                        attrs.append(undirected_angle(angle))
                    out.setAttributes(attrs)
                    sink.addFeature(out, QgsFeatureSink.FastInsert)
                    seg_idx += 1
                    written += 1

            feedback.setProgress(int(current * total))

        feedback.pushInfo(self.tr("Wrote {} segments.").format(written))
        return {self.OUTPUT: dest_id}
