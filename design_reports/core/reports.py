"""Fill the Illinois drainage and subirrigation design templates.

`build_drainage(template, data, out)` and `build_subirrigation(template, data, out)` take
plain Python data (see test/sample_reports.py for the layout) and PNG bytes for the maps,
remove the template's red instructions and example pictures, fill the tables and blanks,
and keep the rest of the template as it is.
"""
import re

from .docfill import (Template, fill_blank, fill_labels, has_drawing, para, replace_text,
                      row_cells, set_rows, simple_table, strip_drawings,
                      strip_text_keep_breaks, table_rows, text_of, top_level_runs,
                      no_bookmarks)

BODY_FONT = "Arial"
MAP_WIDTH_IN = 6.5
MAP_MAX_HEIGHT_IN = 7.6
VALUE_RPR = ('<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/><w:b w:val="0"/>'
             '<w:bCs w:val="0"/><w:i w:val="0"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr>')


def _is_instruction(block):
    if not block.startswith("<w:p"):
        return False
    if not text_of(block).strip():
        return False
    runs = re.findall(r"<w:r\b.*?</w:r>", block, re.S)
    texty = [r for r in runs if re.search(r"<w:t[ >][^<]*\S", r)]
    return bool(texty) and all('w:color w:val="FF0000"' in r for r in texty)


def _caption(text):
    return para(text, italic=True, size=9, align="center", font=BODY_FONT)


def _body(text, align="left", bold=False, size=11):
    return para(text, size=size, align=align, font=BODY_FONT, bold=bold, space_after=120)


def _subhead(text):
    return para(text, bold=True, size=12, align="center", font=BODY_FONT, color="0000FF",
                keep_next=True)


def _fmt(v, nd=1, unit=""):
    if v is None or v == "":
        return ""
    s = "{:,.{}f}".format(v, nd) if isinstance(v, (int, float)) else str(v)
    return s + (" " + unit if unit else "")


PAGE = '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


class _Doc:
    def __init__(self, tpl):
        self.t = tpl
        self.out = {i: [b] for i, b in enumerate(tpl.blocks)}

    def idx(self, needle, start=0, kind=None):
        i = self.t.find(needle, start, kind)
        if i is None:
            raise ValueError("Template block not found: {!r}. Is this the right "
                             "template?".format(needle))
        return i

    def pic(self, start, stop=None):
        """Index of the next block with a picture at or after `start` (before `stop`)."""
        stop = stop or len(self.t.blocks)
        for i in range(start, stop):
            if has_drawing(self.t.blocks[i]):
                return i
        raise ValueError("No picture found after block {}".format(start))

    def set(self, i, *blocks):
        self.out[i] = [b for b in blocks if b]

    def drop(self, *idxs):
        for i in idxs:
            keep = strip_text_keep_breaks(self.t.blocks[i])
            self.out[i] = [keep] if keep else []

    def after(self, i, *blocks):
        self.out[i].extend(b for b in blocks if b)

    def images(self, items, note=None):
        """items: list of (png, caption); successive images on their own pages."""
        out = []
        items = [it for it in (items or []) if it and it[0]]
        if not items:
            return [_caption(note or "(map not available)")]
        for k, (png, cap) in enumerate(items):
            if k:
                out.append(PAGE)
            out.append(self.t.add_png(png, MAP_WIDTH_IN, MAP_MAX_HEIGHT_IN))
            if cap:
                out.append(_caption(cap))
        return out

    def replace_picture(self, i, items, note=None):
        """Put the images where the template's picture is, keeping the rest of that
        paragraph (page breaks, a heading that shares the paragraph)."""
        rest = strip_drawings(self.t.blocks[i])
        if rest is not None and _is_instruction(rest):
            rest = strip_text_keep_breaks(rest)
        self.set(i, *(self.images(items, note) + [rest]))

    def finish(self):
        blocks = []
        for i in range(len(self.t.blocks)):
            blocks.extend(self.out[i])
        self.t.blocks = [b for b in blocks if not _is_instruction(b)]


def _one(png, caption=None):
    return [(png, caption)] if png else []


def _label_value(block, value):
    """Heading-style label paragraph -> 'Label:  value' (value in plain text)."""
    if value in (None, ""):
        return block
    runs = top_level_runs(block)
    if not runs:
        return block
    a, b = runs[-1]
    last = block[a:b]
    last = re.sub(r"(<w:t(?: [^>]*)?>)([^<]*?)\s*(</w:t>)", lambda m: m.group(1) +
                  m.group(2).rstrip() + ":" + m.group(3), last, count=1)
    new = '<w:r>{}<w:t xml:space="preserve">  {}</w:t></w:r>'.format(
        VALUE_RPR, re.sub("&", "&amp;", str(value)).replace("<", "&lt;"))
    return block[:a] + last + new + block[b:]


def _break_before(block):
    """The paragraph starts a new page (no blank page if the previous one is full)."""
    if "<w:pageBreakBefore/>" in block:
        return block
    if "<w:pPr>" not in block:
        return block.replace(">", "><w:pPr><w:pageBreakBefore/></w:pPr>", 1)
    return re.sub(r"(<w:pPr>(?:<w:pStyle [^>]*/>)?(?:<w:keepNext/>)?(?:<w:keepLines/>)?)",
                  r"\1<w:pageBreakBefore/>", block, count=1)


def _site(d, data):
    t = d.t
    i_site = d.idx("County / Township", kind="w:tbl")
    s = data.get("site", {})
    d.set(i_site, fill_labels(t.blocks[i_site], {
        "County / Township": s.get("county_township", ""),
        "Latitude / Longitude": s.get("latlon", ""),
        "Crops Grown": s.get("crops", ""),
        "Total Acres": s.get("total_acres", "")}))
    return i_site


def _narrative(d, data, start):
    i = d.idx("Include a narrative statement", start)
    paras = [p for p in (data.get("narrative") or "").split("\n\n") if p.strip()]
    if data.get("existing_tile_note"):
        paras.append(data["existing_tile_note"])
    d.set(i, *[_body(p) for p in paras])
    return i


def _soils(d, data, start):
    t = d.t
    i_link = d.idx("websoilsurvey", start)
    d.drop(i_link)
    i_pic = d.pic(i_link)
    d.replace_picture(i_pic, _one(data["images"].get("soils"), data["captions"].get("soils")))
    i_ins = d.idx("Include field boundaries on the soil map", i_pic)
    d.drop(i_ins)
    i_tbl = d.idx("Soil Number", i_ins, kind="w:tbl")
    rows = data.get("soils") or [["", "", ""]]
    d.set(i_tbl, set_rows(t.blocks[i_tbl], 1, 1, [
        [r[0], r[1], _fmt(r[2], 1)] for r in rows]))
    return i_tbl


def _depressions(d, data, start):
    i_h = d.idx("Depressions and Run-on Area", start)
    i_ins = d.idx("Depressions and Run-on areas should be", i_h)
    d.drop(i_ins)
    i_pic = d.pic(i_ins)
    d.replace_picture(i_pic, _one(data["images"].get("depressions"),
                                  data["captions"].get("depressions")))
    rows = data.get("dep_table") or []
    if rows:
        d.after(i_pic, para(""), simple_table(
            ["Label", "Type", "Area (acres)", "Max depth (ft)", "Storage (cu yd)"], rows,
            [1200, 2200, 1500, 1600, 1600]))
    if data.get("dep_note"):
        d.after(i_pic, _caption(data["dep_note"]))
    return i_pic


def _system(d, data, start, heading):
    t = d.t
    i_h = d.idx(heading, start)
    i_add = d.idx("Add tile maps that include", i_h)
    i_also = d.idx("Also indicate any tiles already existing", i_add)
    for i in range(i_add, i_also + 1):
        d.drop(i)
    i_pic = d.pic(i_also)
    d.replace_picture(i_pic, data["images"].get("system") or [],
                      "(no drainage network supplied)")
    i_ctbl = d.idx("Component", i_pic, kind="w:tbl")
    comps = data.get("components") or [["", ""]]
    d.set(i_ctbl, set_rows(t.blocks[i_ctbl], 1, 1, comps))
    summ = data.get("pipe_summary") or []
    if summ:
        d.after(i_ctbl, para(""), _subhead("Pipe summary"), simple_table(
            ["Pipe size", "Material", "Total length (ft)", "Pipes"],
            [[r[0], r[1], "{:,.0f}".format(r[2]), "{:,d}".format(r[3])] for r in summ],
            [1500, 3700, 1900, 1100], align=["center", "left", "right", "center"],
            bold_last=True),
            _caption("Lengths measured along the pipes in the design layer, to the nearest "
                     "foot."))
    sched = data.get("schedule") or []
    if sched:
        d.after(i_ctbl, para(""), _subhead("Pipe schedule"), simple_table(
            ["Pipe", "Kind", "Size", "Length (ft)", "Slope (%)", "Depth (ft)"],
            [[r["id"], str(r["kind"]).capitalize(), r["size"], "{:,.0f}".format(r["length"]),
              "" if r["slope"] is None else "{:.2f}".format(r["slope"]),
              "" if r["depth_min"] is None else "{:.1f} - {:.1f}".format(r["depth_min"],
                                                                       r["depth_max"])]
             for r in sched], [1000, 1300, 1100, 1400, 1300, 1600]),
            _caption("Lengths to the nearest 10 ft, slopes to the nearest 0.05 %."))
    return i_ctbl


def _topo(d, data, start, instruction):
    i_h = d.idx("Topographic Map", start)
    i_ins = d.idx(instruction, i_h)
    d.drop(i_ins)
    i_pic = d.pic(i_ins)
    d.replace_picture(i_pic, data["images"].get("topo") or [])
    return i_pic


def _overlay_table(tbl, s):
    return fill_labels(tbl, {
        "Latitude / Longitude of the water control structure": s.get("struct_latlon", ""),
        "Latitude / Longitude of the lowest spot": s.get("low_latlon", ""),
        "Control Elevation": _fmt(s.get("control_elev"), 2, "ft"),
        "Control  Elevation": _fmt(s.get("control_elev"), 2, "ft"),
        "Flowline Elevation of tile at structure": _fmt(s.get("flowline"), 2, "ft"),
        "Area of Impact": _fmt(s.get("area"), 1, "acres")})


# ====================================================================== drainage
def build_drainage(template_path, data, out_path):
    t = Template(template_path)
    d = _Doc(t)
    data.setdefault("images", {})
    data.setdefault("captions", {})
    img, cap = data["images"], data["captions"]

    i_note = d.idx("Actual maps must be legible")
    d.drop(i_note)
    i_site = _site(d, data)
    d.replace_picture(d.pic(i_site), _one(img.get("location"), cap.get("location")))
    i_bg = d.idx("Background", i_site)
    i_nar = _narrative(d, data, i_bg)
    i_soilh = d.idx("Soil Map", i_nar)
    d.replace_picture(d.pic(i_nar, i_soilh + 1),
                      _one(img.get("background"), cap.get("background")))
    i = _soils(d, data, i_soilh)

    # depth / spacing simulations (Illinois Drainage Guide county simulations)
    for key, head in (("current", "Depth/Spacing Simulation for Current Climate"),
                      ("projected", "Depth/Spacing Simulation for Projected Climate")):
        i_h = d.idx(head, i)
        p1 = d.pic(i_h)
        p2 = d.pic(p1 + 1)
        note = ("Insert the {} depth/spacing simulation for {} from the Illinois Drainage "
                "Guide county simulations.")
        for p, crop in ((p1, "corn"), (p2, "soybeans")):
            k = "sim_{}_{}".format(key, "corn" if crop == "corn" else "soy")
            d.replace_picture(p, _one(img.get(k), cap.get(k)),
                              note.format(key + " climate", crop))
        if data.get("sim_note"):
            d.after(p2, _caption(data["sim_note"]))
        i = p2

    # watershed and benchmark
    i_ws = d.idx("Watershed Map", i)
    d.set(i_ws, _break_before(t.blocks[i_ws]))
    i_ins = d.idx("The watershed map should show", i_ws)
    d.drop(i_ins)
    i_pic = d.pic(i_ins)
    d.replace_picture(i_pic, _one(img.get("watershed"), cap.get("watershed")),
                      "(no watershed supplied)")
    i_be = d.idx("Benchmark Elevation", i_pic)
    d.set(i_be, fill_blank(t.blocks[i_be], data.get("benchmark_elev") or "_____________"))
    i_bd = d.idx("Benchmark Description", i_be)
    bdesc = data.get("benchmark_desc") or ""
    d.set(i_bd, fill_blank(t.blocks[i_bd], bdesc or "____________________"))
    if bdesc and not text_of(t.blocks[i_bd + 1]).strip("_ "):
        d.drop(i_bd + 1)

    i = _depressions(d, data, i_bd)
    i = _topo(d, data, i, "The topographic map should show")

    # general design information
    i_g = d.idx("General Drainage System Design Information", i)
    g = data.get("design", {})
    for label, key in (("Upper drain depth limit", "upper"),
                       ("Lower drain depth limit", "lower"),
                       ("Absolute upper drain depth", "abs_upper"),
                       ("Absolute lower drain depth", "abs_lower"),
                       ("Spacing of laterals", "spacing"),
                       ("Design drainage coefficient", "dc"),
                       ("Pipe Material", "materials"),
                       ("Other considerations", "other")):
        k = d.idx(label, i_g)
        d.set(k, _label_value(t.blocks[k], g.get(key)))

    _system(d, data, i_g, "Drainage System Maps")
    d.finish()
    t.save(out_path)
    return out_path


# ====================================================================== subirrigation
def build_subirrigation(template_path, data, out_path):
    t = Template(template_path)
    d = _Doc(t)
    data.setdefault("images", {})
    data.setdefault("captions", {})
    img, cap = data["images"], data["captions"]

    d.drop(d.idx("Actual maps must be legible"))
    i_site = _site(d, data)
    d.replace_picture(d.pic(i_site), _one(img.get("location"), cap.get("location")))
    i_bg = d.idx("Background", i_site)
    i_nar = _narrative(d, data, i_bg)
    i_ws = d.idx("Watershed Map", i_nar)
    d.replace_picture(d.pic(i_nar, i_ws), _one(img.get("background"), cap.get("background")))

    i_ins = d.idx("The watershed map should show", i_ws)
    d.drop(i_ins)
    i_pic = d.pic(i_ins)
    d.replace_picture(i_pic, _one(img.get("watershed"), cap.get("watershed")),
                      "(no watershed supplied)")
    i_out = d.idx("Watershed Outlet Coordinates", i_pic)
    d.set(i_out, fill_blank(t.blocks[i_out], data.get("outlet_coords") or
                            "____________________"))

    i = _soils(d, data, i_out)
    i = _depressions(d, data, i)

    # subirrigation zones
    i_z = d.idx("Subirrigation Zone(s)", i)
    i_ins = d.idx("Subirrigation  zones should be", i_z)
    d.drop(i_ins)
    i_pic = d.pic(i_ins)
    d.replace_picture(i_pic, _one(img.get("zones"), cap.get("zones")),
                      "(no zones supplied)")
    zrows = data.get("zones_table") or []
    if zrows:
        d.after(i_pic, para(""), simple_table(
            ["Zone", "Area (acres)", "Minimum elevation (ft)", "Structure"], zrows,
            [1200, 1800, 2400, 1500]))

    # general design information
    i_g = d.idx("General Drainage System Design Information", i_pic)
    i_dc = d.idx("Drainage Coefficient should be greater", i_g)
    d.drop(i_dc)
    i_gt = d.idx("Upper drain depth limit", i_dc, kind="w:tbl")
    g = data.get("design", {})
    d.set(i_gt, fill_labels(t.blocks[i_gt], {
        "Upper drain depth limit": g.get("upper", ""),
        "Lower drain depth limit": g.get("lower", ""),
        "Absolute upper drain depth": g.get("abs_upper", ""),
        "Absolute lower drain depth": g.get("abs_lower", ""),
        "Spacing of lateral": g.get("spacing", ""),
        "Irrigation Coefficient": g.get("ic", ""),
        "Design coefficient": g.get("dc", ""),
        "Pipe Material": g.get("materials", ""),
        "Other considerations": g.get("other", "")}, rpr=VALUE_RPR))
    if g.get("note"):
        d.after(i_gt, _caption(g["note"]))

    i = _system(d, data, i_gt, "Subirrigation System Maps")

    # irrigated area
    i_ia = d.idx("Delineation of Irrigated", i)
    i_ins = d.idx("Draw a line that is half the lateral spacing", i_ia)
    fn = None
    for a, b in top_level_runs(t.blocks[i_ins]):
        if "footnoteReference" in t.blocks[i_ins][a:b]:
            fn = re.sub(r'<w:color w:val="FF0000"/>', "", t.blocks[i_ins][a:b])
    d.drop(i_ins)
    i_tbl = d.idx("Dominant Soil", i_ins, kind="w:tbl")
    ia = data.get("irrigated", {})
    tbl = fill_labels(t.blocks[i_tbl], {
        "Dominant Soil": ia.get("dominant_soil", ""),
        "Recommend Spacing": _fmt(ia.get("spacing"), 0, "ft"),
        "Drained Area": _fmt(ia.get("area"), 1, "acres")})
    if fn:
        for tr in table_rows(tbl):
            if "Recommend Spacing" in text_of(tr):
                c0 = row_cells(tr)[0]
                c0n = c0[:c0.rindex("</w:p>")] + fn + c0[c0.rindex("</w:p>"):]
                tbl = tbl.replace(tr, tr.replace(c0, c0n, 1), 1)
                break
    d.set(i_tbl, tbl)
    i_pic = d.pic(i_tbl)
    d.replace_picture(i_pic, _one(img.get("irrigated"), cap.get("irrigated")))
    if ia.get("note"):
        d.after(i_pic, _caption(ia["note"]))

    i = _topo(d, data, i_pic, "The topographic map, on a maximum scale")

    # zonal overlay maps
    i_ov = d.idx("Zonal Overlay Maps", i)
    i_t1 = d.idx("Latitude / Longitude of the water control structure", i_ov, kind="w:tbl")
    ov_tbl = no_bookmarks(t.blocks[i_t1])
    i_sig = d.idx("Signature Page", i_t1)
    i_h2 = next(k for k in range(i_t1, i_sig) if "Overlay Map" in text_of(t.blocks[k]))
    head = strip_drawings(t.blocks[i_h2])
    head = no_bookmarks(head.replace('<w:r><w:br w:type="page"/></w:r>', ""))
    end = i_sig
    while end - 1 > i_ov and 'w:type="page"' in t.blocks[end - 1] and \
            not text_of(t.blocks[end - 1]).strip():
        end -= 1                                   # keep the break before the signatures
    for k in range(i_ov + 1, end):
        d.set(k)
    out = []
    systems = data.get("systems") or []
    for k, s in enumerate(systems):
        h = replace_text(head, "Overlay Map", "Overlay Map – System {}".format(
            s.get("id", k + 1)))
        if k:
            h = _break_before(h)
        out += [h, para(""), _overlay_table(ov_tbl, s), para("")]
        out += d.images(_one(s.get("map"), s.get("caption")))
        if s.get("note"):
            out.append(_caption(s["note"]))
    if not systems:
        out = [_body("No control structures were supplied.", align="center")]
    d.after(i_ov, *out)

    d.finish()
    t.save(out_path)
    return out_path
