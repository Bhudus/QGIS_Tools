"""Processing algorithm: Subirrigation design report (Illinois template)."""
import numpy as np
from qgis.core import (
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QVariant

from . import maps
from .common import ReportAlgorithm, num, rings, sval
from .core.compute import lowest_cell, polygon_mask, scale_text
from .core.design import impacted_mask, irrigated_buffer
from .core.reports import build_subirrigation


class SubirrigationReportAlgorithm(ReportAlgorithm):
    KEY = "subirrigation"
    TEMPLATE_FILE = "Subirrigation_Design_Template.dotx"
    MAX_SCALE_DEFAULT = 2400
    CONTOUR_DEFAULT = 0.5

    def createInstance(self):
        return SubirrigationReportAlgorithm()

    def icon(self):
        import os
        from qgis.PyQt.QtGui import QIcon
        return QIcon(os.path.join(os.path.dirname(__file__), "icon_subirrigation.png"))

    def name(self):
        return "subirrigationreport"

    def displayName(self):
        return self.tr("Subirrigation design report")

    def shortHelpString(self):
        return self.tr(
            "<p>Writes the subirrigation design report (Word .docx) from the Illinois "
            "Subirrigation Design template, filled from the design layers.</p>"
            "<p>Site, background, watershed (with the outlet coordinates), soil map, "
            "depressions and run-on areas, as in the drainage report.</p>"
            "<p><b>Subirrigation zones</b>: the zones of Controlled Drainage Zones, labeled "
            "with acres and minimum elevation (lowest ground, DEM).</p>"
            "<p><b>General design information</b>: depth limits, lateral spacing, irrigation "
            "coefficient (entered) and drainage coefficient, with a check that the drainage "
            "coefficient is at least the irrigation coefficient.</p>"
            "<p><b>Subirrigation system maps</b>: pipes labeled with size, length and slope, "
            "components and pipe schedule. <b>Irrigated area</b>: tile buffered by half the "
            "lateral spacing, or by a third of the recommended spacing when the laterals are "
            "more than two thirds of the recommended spacing apart. <b>Topographic map</b>: "
            "6-inch contours at 1:2,400 or closer, the same sheets as the system map.</p>"
            "<p><b>Zonal overlay maps</b>, one per structure: the impacted area is the "
            "structure's zone within the irrigated area, limited to the ground within 1 ft "
            "above the control elevation when the next structure upstream is set more than "
            "1 ft higher; the control elevation is the lowest ground in it.</p>")

    def extra_layers(self):
        self.addParameter(QgsProcessingParameterFeatureSource(
            "ZONES", self.tr("Controlled Drainage Zones: zones"),
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            "STRUCTURES", self.tr("Controlled Drainage Zones: structures"),
            [QgsProcessing.TypeVectorPoint], optional=True))

    def extra_text(self):
        self.addParameter(QgsProcessingParameterNumber(
            "IC", self.tr("Irrigation coefficient (in/day)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.25, minValue=0.0))

    # ------------------------------------------------------------
    def build(self, c):
        W = c["warnings"]
        crs, ft, ac = c["crs"], c["ft"], c["ac"]
        z, x0, y0, dx, dy = c["z"], c["x0"], c["y0"], c["dx"], c["dy"]
        img, cap = self.common_maps(c, "Subirrigation system map")
        LF = ("line", "#d00000", "Field boundary")

        # ---- irrigated area
        irrigated = {}
        irr_geom = None
        if c["pipes"]:
            dist, why = irrigated_buffer(c["spacing"], c["rec_spacing"])
            if dist:
                irr_geom = QgsGeometry.unaryUnion([p["geom"].buffer(dist / ft, 8)
                                                   for p in c["pipes"]])
                irrigated = {"dominant_soil": c["dominant"],
                             "spacing": c["rec_spacing"],
                             "area": irr_geom.area() * ac,
                             "note": "Irrigated area: {}.".format(why) +
                             ("" if c["rec_spacing"] else " No recommended spacing in the "
                              "soils layer; the lateral spacing was used.")}
                lyr = maps.memory_layer("MultiPolygon", crs, [("n", QVariant.Int)],
                                        [(irr_geom, [1])], "Irrigated area")
                maps.style_fill(lyr, "#4caf50", outline="#1b5e20", width=0.5, alpha=90)
                png, sc = self.render(c, [c["field_lyr"], c["plain_lyr"], lyr],
                                      "Delineation of irrigated area",
                                      [LF, ("line", "#1d4ed8", "Tile"),
                                       ("fill", "#a5d6a7", "Irrigated area")], height=5.6)
                img["irrigated"] = png
                cap["irrigated"] = "Scale {}.".format(scale_text(sc))
        irr_mask = polygon_mask(rings(irr_geom), x0, y0, dx, dy, *z.shape) \
            if irr_geom is not None else c["field_mask"]

        # ---- zones and structures (Controlled Drainage Zones)
        zones, structs = {}, []
        zsrc = self.parameterAsSource(self.P, "ZONES", self.context)
        ssrc = self.parameterAsSource(self.P, "STRUCTURES", self.context)
        tctx = c["tctx"]
        if zsrc is not None:
            zl = {f.name().lower(): f.name() for f in zsrc.fields()}
            t = QgsCoordinateTransform(zsrc.sourceCrs(), crs, tctx)
            for f in zsrc.getFeatures():
                key = num(f[zl["struct_id"]]) if "struct_id" in zl else None
                if key is None and "zone" in zl:
                    key = num(f[zl["zone"]])
                g = QgsGeometry(f.geometry())
                if key is None or g.isNull():
                    continue
                g.transform(t)
                k = int(key)
                zones[k] = g if k not in zones else QgsGeometry.unaryUnion([zones[k], g])
        if ssrc is not None:
            sl = {f.name().lower(): f.name() for f in ssrc.fields()}
            t = QgsCoordinateTransform(ssrc.sourceCrs(), crs, tctx)
            if "struct_id" not in sl:
                W.append("The structures layer has no struct_id: is it the Controlled "
                         "Drainage Zones structures output?")
            else:
                for f in ssrc.getFeatures():
                    sid = num(f[sl["struct_id"]])
                    g = QgsGeometry(f.geometry())
                    if sid is None or g.isNull():
                        continue
                    g.transform(t)
                    pt = g.asPoint() if not g.isMultipart() else g.asMultiPoint()[0]
                    structs.append({"id": int(sid), "pt": pt,
                                    "zone": int(num(f[sl["zone"]])) if "zone" in sl and
                                    num(f[sl["zone"]]) is not None else int(sid),
                                    "ds": int(num(f[sl["ds_struct"]])) if "ds_struct" in sl
                                    and num(f[sl["ds_struct"]]) is not None else None,
                                    "pipe": sval(f[sl["pipe_size"]]) if "pipe_size" in sl
                                    else ""})
        zone_rows, zone_feats = [], []
        zmasks = {}
        for k in sorted(zones):
            g = zones[k]
            m = polygon_mask(rings(g), x0, y0, dx, dy, *z.shape)
            zmasks[k] = m
            low = lowest_cell(z, m, x0, y0, dx, dy, smooth_radius=1)
            a = g.area() * ac
            st = next((s["id"] for s in structs if s["zone"] == k), "")
            zone_rows.append([str(k), "{:.1f}".format(a),
                              "" if not low else "{:.2f}".format(low[2]), str(st)])
            zone_feats.append((g, ["Zone {}\n{:.1f} ac\nmin {:.2f} ft".format(
                k, a, low[2]) if low else "Zone {}\n{:.1f} ac".format(k, a)]))
        if zone_feats:
            zl_ = maps.memory_layer("MultiPolygon", crs, [("lbl", QVariant.String)],
                                    zone_feats, "Subirrigation zones")
            maps.style_labeled_fill(zl_, "#90a4ae", "#263238", 60, '"lbl"', "#0d47a1", 8)
            png, sc = self.render(c, [c["field_lyr"], zl_, c["faint_lyr"]],
                                  "Subirrigation zones", [LF, ("fill", "#cfd8dc", "Zone")],
                                  height=5.4)
            img["zones"] = png
            cap["zones"] = "Scale {}. Zones from Controlled Drainage Zones; minimum " \
                           "elevation = lowest ground in the zone (LiDAR DEM).".format(
                               scale_text(sc))
        elif zsrc is None:
            W.append("No zones layer: the subirrigation zones and overlay maps are left "
                     "empty.")
        self.feedback.setProgress(92)

        # ---- structures: control elevation, impacted area, overlay maps
        systems = []
        ctrl = {}
        for s in structs:
            m = zmasks.get(s["zone"])
            if m is None:
                W.append("Structure {} has no zone; skipped.".format(s["id"]))
                continue
            low = lowest_cell(z, m & irr_mask, x0, y0, dx, dy, smooth_radius=1) or \
                lowest_cell(z, m, x0, y0, dx, dy, smooth_radius=1)
            if low:
                ctrl[s["id"]] = low
        for s in sorted(structs, key=lambda s: s["id"]):
            if s["id"] not in ctrl:
                continue
            low = ctrl[s["id"]]
            up = [ctrl[u["id"]][2] for u in structs if u["ds"] == s["id"] and u["id"] in ctrl]
            interval = (min(up) - low[2]) if up else None
            m = impacted_mask(zmasks[s["zone"]], irr_mask, z, low[2], interval)
            area = m.sum() * abs(dx * dy) * ac
            polys = self.polygonize(m.astype(np.int32), x0, y0, dx, dy)
            g = polys.get(1)
            note = ""
            if interval is not None and interval > 1.0:
                note = ("Impacted area: zone {} within the irrigated area, ground up to 1 ft "
                        "above the control elevation (next structure upstream set {:.1f} ft "
                        "higher).".format(s["zone"], interval))
            lays = []
            st = maps.memory_layer("Point", crs, [("lbl", QVariant.String)],
                                   [(QgsGeometry.fromPointXY(s["pt"]),
                                     ["Structure {}".format(s["id"])])], "Structure")
            maps.style_points(st, "square", "#000000", 3.4, '"lbl"')
            lo = maps.memory_layer("Point", crs, [("lbl", QVariant.String)],
                                   [(QgsGeometry.fromPointXY(QgsPointXY(low[0], low[1])),
                                     ["Control elevation {:.2f} ft".format(low[2])])],
                                   "Control elevation")
            maps.style_points(lo, "triangle", "#c62828", 3.6, '"lbl"', "#b71c1c")
            lays = [st, lo, c["field_lyr"], c["faint_lyr"], c["contour_lyr"]]
            zg = zones[s["zone"]]
            zl_ = maps.memory_layer("MultiPolygon", crs, [("n", QVariant.Int)], [(zg, [1])],
                                    "Zone")
            maps.style_fill(zl_, "#ffffff", outline="#555555", width=0.5, alpha=0)
            lays.append(zl_)
            if g is not None:
                il = maps.memory_layer("MultiPolygon", crs, [("n", QVariant.Int)], [(g, [1])],
                                       "Impacted area")
                maps.style_fill(il, "#ffd54f", outline="#e65100", width=0.8, alpha=120)
                lays.append(il)
            r = QgsRectangle(zg.boundingBox())
            r.combineExtentWith(QgsRectangle(s["pt"], s["pt"]))
            r.grow(max(r.width(), r.height()) * 0.06 + 30.0 / ft)
            png, sc = self.render(c, lays, "Overlay map - System {}".format(s["id"]),
                                  [LF, ("fill", "#ffd54f", "Impacted area"),
                                   ("point", "#000000", "Control structure"),
                                   ("point", "#c62828", "Control elevation")], r, 5.6)
            systems.append({"id": s["id"], "struct_latlon": c["latlon"](s["pt"].x(),
                                                                        s["pt"].y()),
                            "low_latlon": c["latlon"](low[0], low[1]),
                            "control_elev": low[2], "area": area, "map": png,
                            "caption": "Scale {}. Contours {:g} ft.".format(
                                scale_text(sc), c["interval"]), "note": note})

        # ---- design values and the DC >= IC check
        dv = self.design_values(c, unit="")
        ic = self.d("IC")
        dcv = dv.pop("_dc_value")
        dv["ic"] = "{:g}".format(ic)
        dv["dc"] = (dv["dc"] or "").replace(" in/day", "")
        note = ""
        if dcv is not None:
            if dcv + 1e-9 < ic:
                note = ("The drainage coefficient ({:g} in/day) is less than the irrigation "
                        "coefficient ({:g} in/day): it should be greater than or "
                        "equal.".format(dcv, ic))
                W.append(note)
            else:
                note = "Drainage coefficient {:g} in/day ≥ irrigation coefficient {:g} " \
                       "in/day.".format(dcv, ic)
        dv["note"] = note
        return {
            "site": self.site(c),
            "narrative": self.s("NARRATIVE"),
            "existing_tile_note": self.s("EXISTING_NOTE"),
            "soils": [[r[0], r[1], r[2]] for r in c["soil_rows"]],
            "outlet_coords": c["outlet_coords"],
            "dep_table": c["dep_rows"],
            "dep_note": "Depressions: deeper than {:g} ft below the filled surface. Run-on: "
                        "land outside the field draining into it (LiDAR DEM).".format(
                            self.d("DEP_DEPTH")),
            "zones_table": zone_rows,
            "design": dv,
            "components": [r[:2] for r in c["components"]],
            "schedule": self.schedule_rows(c),
            "irrigated": irrigated,
            "systems": systems,
            "images": img, "captions": cap,
        }

    def write(self, tpl, data, out):
        build_subirrigation(tpl, data, out)
