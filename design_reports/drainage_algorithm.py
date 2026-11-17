"""Processing algorithm: Drainage system design report (Illinois template)."""
from qgis.core import QgsProcessingParameterFile, QgsProcessingParameterString

from .common import ReportAlgorithm
from .core.reports import build_drainage

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.gif *.bmp *.tif *.tiff)"
SIMS = (("SIM_CUR_CORN", "sim_current_corn", "current climate, corn"),
        ("SIM_CUR_SOY", "sim_current_soy", "current climate, soybeans"),
        ("SIM_PROJ_CORN", "sim_projected_corn", "projected climate, corn"),
        ("SIM_PROJ_SOY", "sim_projected_soy", "projected climate, soybeans"))


def image_as_png(path):
    """PNG bytes of an image file (converted with Qt when it is not a PNG)."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice
    from qgis.PyQt.QtGui import QImage
    img = QImage(path)
    if img.isNull():
        raise ValueError("Could not read the image " + path)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba.data())


class DrainageReportAlgorithm(ReportAlgorithm):
    KEY = "drainage"
    TEMPLATE_FILE = "Drainage_Design_Template.dotx"
    MAX_SCALE_DEFAULT = 3600
    CONTOUR_DEFAULT = 0.5

    def createInstance(self):
        return DrainageReportAlgorithm()

    def name(self):
        return "drainagereport"

    def displayName(self):
        return self.tr("Drainage system design report")

    def shortHelpString(self):
        return self.tr(
            "<p>Writes the drainage system design report (Word .docx) from the Illinois "
            "Drainage System Design template, filled from the design layers.</p>"
            "<p><b>Site</b>: county/township as entered; latitude/longitude and acres from "
            "the field; crops from Crop History or as typed. <b>Background</b>: your "
            "narrative and existing tile, with the field on aerial imagery.</p>"
            "<p><b>Soil map</b> from the SSURGO drainage soil map layer or Soil Data "
            "Access. <b>Depth/spacing simulations</b>: the charts you saved from the Illinois "
            "Drainage Guide county simulations (current and projected climate, corn and "
            "soybeans).</p>"
            "<p><b>Watershed map</b> from the StreamStats Delineator watershed, outlet and "
            "streams; benchmark as entered. <b>Depressions and run-on areas</b>: from the "
            "Depressions and Laylines layer or found from the DEM (Wang &amp; Liu fill; run-on "
            "= land outside the field whose flow enters it), labeled with acres.</p>"
            "<p><b>Topographic map</b>: LiDAR contours (0.5 ft) and laylines. <b>General "
            "design information</b>: depth limits (entered, or the shallowest and deepest "
            "tile), lateral spacing, drainage coefficient (D_COEFF), materials. "
            "<b>Drainage system maps</b>: every pipe labeled with size, length (nearest 10 "
            "ft) and slope (nearest 0.05 %), existing tile dashed, components and a pipe "
            "schedule. Large fields are split into sheets at the largest scale.</p>")

    def extra_text(self):
        for nm, _, label in SIMS:
            self.addParameter(QgsProcessingParameterFile(
                nm, self.tr("Depth/spacing simulation chart, {} (image)".format(label)),
                optional=True, fileFilter=IMAGE_FILTER))
        self.addParameter(QgsProcessingParameterString(
            "SIM_NOTE", self.tr("Depth/spacing simulation note (county, soil)"),
            defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterString(
            "BM_ELEV", self.tr("Benchmark elevation (ft)"), defaultValue="", optional=True))
        self.addParameter(QgsProcessingParameterString(
            "BM_DESC", self.tr("Benchmark description and coordinates"), defaultValue="",
            optional=True))

    def build(self, c):
        img, cap = self.common_maps(c, "Drainage system map")
        for nm, key, label in SIMS:
            path = self.parameterAsFile(self.P, nm, self.context)
            if path:
                try:
                    img[key] = image_as_png(path)
                except (OSError, ValueError) as e:
                    c["warnings"].append("Simulation chart ({}): {}".format(label, e))
        dv = self.design_values(c)
        dv.pop("_dc_value", None)
        dv["spacing"] = dv["spacing"] or ""
        return {
            "site": self.site(c),
            "narrative": self.s("NARRATIVE"),
            "existing_tile_note": self.s("EXISTING_NOTE"),
            "soils": [[r[0], r[1], r[2]] for r in c["soil_rows"]],
            "sim_note": self.s("SIM_NOTE"),
            "benchmark_elev": self.s("BM_ELEV"),
            "benchmark_desc": self.s("BM_DESC"),
            "dep_table": c["dep_rows"],
            "dep_note": "Depressions: deeper than {:g} ft below the filled surface. Run-on: "
                        "land outside the field draining into it (LiDAR DEM).".format(
                            self.d("DEP_DEPTH")),
            "design": dv,
            "components": [r[:2] for r in c["components"]],
            "schedule": self.schedule_rows(c),
            "images": img, "captions": cap,
        }

    def write(self, tpl, data, out):
        build_drainage(tpl, data, out)
