"""Writers for machine-control and field files (pure Python, no QGIS).

Every writer takes the same prepared records, already converted to the output
units and coordinate systems by the caller:

run = {
  "name", "kind", "size", "install_from" ("outlet end" or "upstream end"),
  "xyz":    [(easting, northing, control elev), ...] in install order,
  "lonlat": [(lon, lat, control elev in metres), ...] in install order,
  "stats":  {"length", "inv_ds", "inv_us", "control_ds", "control_us", "ground_ds",
             "ground_us", "min_grade", "max_grade", "min_depth", "max_depth",
             "mean_depth", "sections": [(start, end, grade %, z0, z1)], "flags": [...]},
  "stations": [{"label", "chain", "lon", "lat", "e", "n", "ground", "tile",
                "control", "depth", "grade", "note"}],
}
meta = {"field", "len_unit", "elev_unit", "crs", "offset", "control_offset",
        "control_note", "generated", "plugin", "min_grade", "min_cover", "max_depth"}
"""
import csv
import html
import io
import math
import zipfile
from xml.sax.saxutils import escape

KIND_COLORS_KML = {"main": "ff1c1cb9", "submain": "ff1c1cb9", "lateral": "ffeb6325"}  # aabbggrr
KIND_WIDTH_KML = {"main": 4, "submain": 3, "lateral": 2}
DXF_LAYERS = {"main": ("TILE_MAIN", 1), "submain": ("TILE_SUBMAIN", 6),
              "lateral": ("TILE_LATERAL", 5)}


def _f(v, nd=3):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return ""
    return "{:.{nd}f}".format(v, nd=nd)


# ---------------------------------------------------------------- CSV
STATION_COLUMNS = ["run", "kind", "size", "station", "chain", "lat", "lon", "northing",
                   "easting", "ground", "tile_invert", "control_elev", "depth",
                   "grade_pct", "note"]


def write_stations_csv(path, runs, meta):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(STATION_COLUMNS)
        for r in runs:
            for s in r["stations"]:
                w.writerow([r["name"], r["kind"], r["size"] or "", s["label"],
                            _f(s["chain"], 2), _f(s["lat"], 8), _f(s["lon"], 8),
                            _f(s["n"], 3), _f(s["e"], 3), _f(s["ground"]), _f(s["tile"]),
                            _f(s["control"]), _f(s["depth"]), _f(s["grade"], 4), s["note"]])


def write_pnezd(path, runs, meta, start_number=1):
    """Point number, northing, easting, elevation (control), description."""
    n = start_number
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for r in runs:
            for s in r["stations"]:
                desc = "{} STA {}{}".format(r["name"], s["label"],
                                             " " + s["note"].upper() if s["note"] else "")
                w.writerow([n, _f(s["n"], 3), _f(s["e"], 3), _f(s["control"], 3),
                            desc.replace(",", ";")])
                n += 1
    return n - start_number


# ---------------------------------------------------------------- KML / KMZ
def kml_text(runs, meta):
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n'
              '<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>\n')
    out.write("<name>{}</name>\n".format(escape(meta["field"] + " tile plan")))
    out.write("<description>{}</description>\n".format(escape(
        "Control elevations in metres ({}), offset {} applied. {}".format(
            meta["elev_unit_long"], meta["offset_text"], meta["control_note"]))))
    for kind, col in KIND_COLORS_KML.items():
        out.write('<Style id="{k}"><LineStyle><color>{c}</color><width>{w}</width></LineStyle>'
                  '</Style>\n'.format(k=kind, c=col, w=KIND_WIDTH_KML[kind]))
    out.write('<Style id="sta"><IconStyle><scale>0.5</scale></IconStyle>'
              '<LabelStyle><scale>0.6</scale></LabelStyle></Style>\n')
    for kind, title in (("main", "Mains"), ("submain", "Submains"), ("lateral", "Laterals")):
        rs = [r for r in runs if r["kind"] == kind]
        if not rs:
            continue
        out.write("<Folder><name>{}</name>\n".format(title))
        for r in rs:
            st = r["stats"]
            desc = ("Size: {size}<br/>Length: {L} {lu}<br/>Install from the {fr}<br/>"
                    "Invert (downstream / upstream): {i0} / {i1} {eu}<br/>"
                    "Grade: {g0} to {g1} %<br/>Depth: {d0} to {d1} {eu}{fl}").format(
                size=escape(str(r["size"] or "-")), L=_f(st["length"], 1), lu=meta["len_unit"],
                fr=r["install_from"], i0=_f(st["inv_ds"]), i1=_f(st["inv_us"]),
                eu=meta["elev_unit"], g0=_f(st["min_grade"], 3), g1=_f(st["max_grade"], 3),
                d0=_f(st["min_depth"], 2), d1=_f(st["max_depth"], 2),
                fl=("<br/>CHECK: " + escape("; ".join(st["flags"]))) if st["flags"] else "")
            coords = " ".join("{:.8f},{:.8f},{:.3f}".format(lo, la, z) for lo, la, z in r["lonlat"])
            out.write("<Placemark><name>{}</name><description><![CDATA[{}]]></description>"
                      "<styleUrl>#{}</styleUrl><LineString><altitudeMode>absolute</altitudeMode>"
                      "<coordinates>{}</coordinates></LineString></Placemark>\n".format(
                          escape(r["name"]), desc, kind, coords))
        out.write("</Folder>\n")
    out.write("</Document>\n</kml>\n")
    return out.getvalue()


def write_kmz(path, runs, meta):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml_text(runs, meta))


# ---------------------------------------------------------------- DXF (R12 ASCII)
def _dxf_pairs(*pairs):
    return "".join("{}\n{}\n".format(c, v) for c, v in pairs)


def write_dxf(path, runs, meta, text_height=2.0):
    """3D polylines (one per run) on a layer per kind, plus run-name text at the start."""
    body = io.StringIO()
    body.write(_dxf_pairs((0, "SECTION"), (2, "HEADER"), (9, "$ACADVER"), (1, "AC1009"),
                          (0, "ENDSEC")))
    body.write(_dxf_pairs((0, "SECTION"), (2, "TABLES"), (0, "TABLE"), (2, "LAYER"),
                          (70, len(DXF_LAYERS) + 1)))
    for name, color in list(DXF_LAYERS.values()) + [("TILE_LABELS", 7)]:
        body.write(_dxf_pairs((0, "LAYER"), (2, name), (70, 0), (62, color),
                              (6, "CONTINUOUS")))
    body.write(_dxf_pairs((0, "ENDTAB"), (0, "ENDSEC")))
    body.write(_dxf_pairs((0, "SECTION"), (2, "ENTITIES")))
    for r in runs:
        layer = DXF_LAYERS.get(r["kind"], DXF_LAYERS["lateral"])[0]
        body.write(_dxf_pairs((0, "POLYLINE"), (8, layer), (66, 1), (10, 0.0), (20, 0.0),
                              (30, 0.0), (70, 8)))
        for x, y, z in r["xyz"]:
            body.write(_dxf_pairs((0, "VERTEX"), (8, layer), (10, "{:.4f}".format(x)),
                                  (20, "{:.4f}".format(y)), (30, "{:.4f}".format(z)),
                                  (70, 32)))
        body.write(_dxf_pairs((0, "SEQEND"), (8, layer)))
        x, y, z = r["xyz"][0]
        body.write(_dxf_pairs((0, "TEXT"), (8, "TILE_LABELS"), (10, "{:.4f}".format(x)),
                              (20, "{:.4f}".format(y)), (30, "{:.4f}".format(z)),
                              (40, text_height), (1, r["name"])))
    body.write(_dxf_pairs((0, "ENDSEC"), (0, "EOF")))
    with open(path, "w", encoding="ascii", errors="replace", newline="\r\n") as fh:
        fh.write(body.getvalue())


# ---------------------------------------------------------------- run sheet (HTML)
CSS = """
body{font-family:'Segoe UI',Arial,sans-serif;font-size:11pt;color:#222;margin:24px}
h1{font-size:17pt;margin:0 0 4px;color:#13294B} h2{font-size:13pt;margin:22px 0 6px;color:#13294B}
.meta{color:#555;font-size:9.5pt;margin-bottom:10px} table{border-collapse:collapse;margin:6px 0 12px}
th,td{border:1px solid #c8ccd2;padding:3px 7px;text-align:right;font-size:9.5pt}
th{background:#eef1f5;text-align:center} td.l{text-align:left}
.flag{color:#b42318;font-weight:600} .run{page-break-inside:avoid;border-top:3px solid #FF5F05;
padding-top:4px;margin-top:18px} .set td{text-align:left} .note{font-size:9pt;color:#555}
@media print{body{margin:10mm}.run{page-break-inside:avoid}}
"""


def run_sheet_html(runs, meta, max_station_rows=400):
    e = html.escape
    lu, eu = meta["len_unit"], meta["elev_unit"]
    o = io.StringIO()
    o.write("<!DOCTYPE html><html><head><meta charset='utf-8'><title>{} run sheet</title>"
            "<style>{}</style></head><body>".format(e(meta["field"]), CSS))
    o.write("<h1>{} &ndash; tile installation run sheet</h1>".format(e(meta["field"])))
    o.write("<div class='meta'>Generated {} by {}. Lengths in {}, elevations in {} ({}). "
            "Coordinates: {}. Elevation offset applied: {}. {}</div>".format(
                e(meta["generated"]), e(meta["plugin"]), lu, eu, e(meta["elev_unit_long"]),
                e(meta["crs"]), e(meta["offset_text"]), e(meta["control_note"])))
    total = sum(r["stats"]["length"] for r in runs)
    flagged = [r for r in runs if r["stats"]["flags"]]
    o.write("<h2>Summary</h2><table><tr><th>Run</th><th>Type</th><th>Size</th>"
            "<th>Length ({lu})</th><th>Install from</th><th>Invert DS / US ({eu})</th>"
            "<th>Min / max grade (%)</th><th>Min / max depth ({eu})</th><th>Check</th></tr>"
            .format(lu=lu, eu=eu))
    for r in runs:
        st = r["stats"]
        o.write("<tr><td class='l'>{}</td><td class='l'>{}</td><td>{}</td><td>{}</td>"
                "<td class='l'>{}</td><td>{} / {}</td><td>{} / {}</td><td>{} / {}</td>"
                "<td class='l flag'>{}</td></tr>".format(
                    e(r["name"]), r["kind"], e(str(r["size"] or "")), _f(st["length"], 1),
                    r["install_from"], _f(st["inv_ds"], 2), _f(st["inv_us"], 2),
                    _f(st["min_grade"], 3), _f(st["max_grade"], 3), _f(st["min_depth"], 2),
                    _f(st["max_depth"], 2), e("; ".join(st["flags"]))))
    o.write("</table><div class='note'>{} runs, {} {} of tile; {} to check.</div>".format(
        len(runs), "{:,.0f}".format(total), lu, len(flagged)))
    for r in runs:
        st = r["stats"]
        o.write("<div class='run'><h2>{} &ndash; {}{}</h2>".format(
            e(r["name"]), r["kind"], (", " + e(str(r["size"]))) if r["size"] else ""))
        s0, s1 = r["stations"][0], r["stations"][-1]
        o.write("<table class='set'><tr><td>Length</td><td>{} {}</td><td>Install from</td>"
                "<td>{}</td></tr>".format(_f(st["length"], 1), lu, r["install_from"]))
        o.write("<tr><td>Downstream end</td><td>{}, {} &nbsp; invert {} {}</td>"
                "<td>Upstream end</td><td>{}, {} &nbsp; invert {} {}</td></tr>".format(
                    _f(s0["lat"], 7), _f(s0["lon"], 7), _f(st["inv_ds"]), eu,
                    _f(s1["lat"], 7), _f(s1["lon"], 7), _f(st["inv_us"]), eu))
        o.write("<tr><td>Display settings</td><td colspan='3'>target depth {} {}, minimum depth "
                "{} {}, maximum depth {} {}, minimum grade {} %</td></tr>".format(
                    _f(st["mean_depth"], 2), eu, _f(st["min_depth"], 2), eu,
                    _f(st["max_depth"], 2), eu, _f(max(st["min_grade"], 0.0), 3)))
        if st["flags"]:
            o.write("<tr><td>Check</td><td colspan='3' class='flag'>{}</td></tr>".format(
                e("; ".join(st["flags"]))))
        o.write("</table>")
        o.write("<table><tr><th>From sta</th><th>To sta</th><th>Length ({lu})</th>"
                "<th>Grade (%)</th><th>Invert from / to ({eu})</th></tr>".format(lu=lu, eu=eu))
        for a, b, g, z0, z1 in st["sections"]:
            o.write("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{} / {}</td></tr>"
                    .format(meta["label"](a), meta["label"](b), _f(b - a, 1), _f(g, 3),
                            _f(z0), _f(z1)))
        o.write("</table>")
        rows = r["stations"]
        o.write("<table><tr><th>Station</th><th>Ground</th><th>Tile invert</th>"
                "<th>Control elev</th><th>Depth</th><th>Grade (%)</th><th>Note</th></tr>")
        for s in rows[:max_station_rows]:
            o.write("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
                    "<td class='l'>{}</td></tr>".format(
                        s["label"], _f(s["ground"], 2), _f(s["tile"]), _f(s["control"]),
                        _f(s["depth"], 2), _f(s["grade"], 3), e(s["note"])))
        if len(rows) > max_station_rows:
            o.write("<tr><td colspan='7' class='l'>... {} more stations in the CSV</td></tr>"
                    .format(len(rows) - max_station_rows))
        o.write("</table></div>")
    o.write("</body></html>")
    return o.getvalue()


def write_run_sheet(path, runs, meta):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(run_sheet_html(runs, meta))
