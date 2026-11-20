"""Fill a Word template (.docx/.dotx) at the XML level (pure Python, no extra packages).

The document body is split into its top-level blocks (paragraphs, tables, ...),
which can be found by their text, removed, replaced or added to. Table cells are
filled in place so the template's formatting is kept. Images are added as new
media parts with inline drawings.
"""
import re
import struct
import zipfile
from xml.sax.saxutils import escape

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
IMG_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
EMU_PER_INCH = 914400

_TAG = re.compile(r"<(/?)(w:p|w:tbl|w:sdt|w:sectPr|w:bookmarkStart|w:bookmarkEnd)\b[^>]*?(/?)>")
_T = re.compile(r"<w:t(?: [^>]*)?>([^<]*)</w:t>")


def text_of(xml):
    from xml.sax.saxutils import unescape
    return unescape("".join(_T.findall(xml)))


def split_body(xml):
    b0 = xml.index("<w:body>") + len("<w:body>")
    b1 = xml.rindex("</w:body>")
    body = xml[b0:b1]
    blocks, depth, start, top = [], 0, None, None
    for m in _TAG.finditer(body):
        close, name, selfc = m.group(1), m.group(2), m.group(3)
        if depth == 0 and not close:
            if selfc:
                blocks.append(body[m.start():m.end()])
                continue
            start, top, depth = m.start(), name, 1
            continue
        if name == top:
            if close:
                depth -= 1
            elif not selfc:
                depth += 1
            if depth == 0:
                blocks.append(body[start:m.end()])
    return xml[:b0], blocks, xml[b1:]


def png_size(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    w, h = struct.unpack(">II", data[16:24])
    return w, h


# ---------------------------------------------------------------- paragraphs
def para(text, bold=False, italic=False, size=None, align=None, font=None, color=None,
         style=None, keep_next=False, space_after=None):
    ppr = []
    if style:
        ppr.append('<w:pStyle w:val="{}"/>'.format(style))
    if keep_next:
        ppr.append("<w:keepNext/>")
    if space_after is not None:
        ppr.append('<w:spacing w:after="{}"/>'.format(int(space_after)))
    if align:
        ppr.append('<w:jc w:val="{}"/>'.format(align))
    rpr = []
    if font:
        rpr.append('<w:rFonts w:ascii="{0}" w:hAnsi="{0}" w:cs="{0}"/>'.format(font))
    if bold:
        rpr.append("<w:b/>")
    if italic:
        rpr.append("<w:i/>")
    if color:
        rpr.append('<w:color w:val="{}"/>'.format(color))
    if size:
        rpr.append('<w:sz w:val="{0}"/><w:szCs w:val="{0}"/>'.format(int(size * 2)))
    runs = []
    for k, line in enumerate(str(text).split("\n")):
        if k:
            runs.append("<w:r><w:br/></w:r>")
        runs.append('<w:r>{}<w:t xml:space="preserve">{}</w:t></w:r>'.format(
            "<w:rPr>{}</w:rPr>".format("".join(rpr)) if rpr else "", escape(line)))
    return "<w:p>{}{}</w:p>".format("<w:pPr>{}</w:pPr>".format("".join(ppr)) if ppr else "",
                                    "".join(runs))


def page_break():
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def image_para(rid, cx, cy, pid, name, align="center"):
    return (
        '<w:p><w:pPr><w:jc w:val="{al}"/></w:pPr><w:r><w:rPr><w:noProof/></w:rPr><w:drawing>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
        '<wp:docPr id="{pid}" name="{nm}"/><wp:cNvGraphicFramePr><a:graphicFrameLocks '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/>'
        '</wp:cNvGraphicFramePr><a:graphic xmlns:a="http://schemas.openxmlformats.org/'
        'drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/'
        '2006/picture"><pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/'
        'picture"><pic:nvPicPr><pic:cNvPr id="{pid}" name="{nm}"/><pic:cNvPicPr><a:picLocks '
        'noChangeAspect="1"/></pic:cNvPicPr></pic:nvPicPr><pic:blipFill><a:blip r:embed="{rid}"/>'
        '<a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/>'
        '<a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        '</pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
    ).format(al=align, cx=int(cx), cy=int(cy), pid=pid, nm=escape(name), rid=rid)


def has_page_break(block):
    return 'w:type="page"' in block


def strip_text_keep_breaks(block):
    """A paragraph with its text removed but its page breaks kept (None if no break)."""
    if not block.startswith("<w:p") or not has_page_break(block):
        return None
    ppr = re.search(r"<w:pPr>.*?</w:pPr>", block, re.S)
    n = block.count('w:type="page"')
    return "<w:p>{}{}</w:p>".format(ppr.group(0) if ppr else "",
                                    '<w:r><w:br w:type="page"/></w:r>' * n)


def replace_text(block, old, new, count=1):
    """Replace text inside <w:t> elements (each occurrence must sit in one element)."""
    done = [0]

    def sub(m):
        t = m.group(0)
        if count and done[0] >= count:
            return t
        inner = m.group(1)
        if old in inner:
            k = inner.count(old) if not count else min(inner.count(old), count - done[0])
            done[0] += k
            return t.replace(">" + inner + "<", ">" + inner.replace(old, escape(new), k) + "<")
        return t
    return re.sub(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", sub, block)


def fill_blank(block, value, count=1):
    """Replace the first run(s) of underscores with value."""
    done = [0]

    def sub(m):
        if done[0] >= count:
            return m.group(0)
        inner = m.group(1)
        new, k = re.subn(r"_{3,}", escape(str(value)), inner, count=count - done[0])
        done[0] += k
        return m.group(0).replace(">" + inner + "<", ">" + new + "<") if k else m.group(0)
    out = re.sub(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", sub, block)
    return out.replace("<w:t>", '<w:t xml:space="preserve">')


def has_drawing(block):
    return "<w:drawing>" in block or "<mc:AlternateContent>" in block or "<w:pict>" in block


_RUN_TAG = re.compile(r"<w:r[ >]|</w:r>")


def top_level_runs(par):
    """(start, end) of each top-level <w:r> in a paragraph (runs nested in text boxes
    are part of their outer run)."""
    out, depth, start = [], 0, None
    for m in _RUN_TAG.finditer(par):
        if m.group(0) == "</w:r>":
            depth -= 1
            if depth == 0:
                out.append((start, m.end()))
        else:
            if depth == 0:
                start = m.start()
            depth += 1
    return out


def strip_drawings(block):
    """The paragraph without its pictures/shapes; None if nothing else is left."""
    out = block
    for a, b in reversed(top_level_runs(block)):
        if has_drawing(block[a:b]):
            out = out[:a] + out[b:]
    if not text_of(out).strip() and 'w:type="page"' not in out:
        return None
    return out


def simple_table(headers, rows, widths, size=10, font="Arial Narrow", head_color="0000FF",
                 align=None, bold_last=False):
    """A bordered table: blue bold header row, plain rows. widths in twips (dxa)."""
    align = align or ["center"] * len(headers)

    def cell(text, w, bold=False, color=None, jc="center"):
        rpr = '<w:rFonts w:ascii="{0}" w:hAnsi="{0}" w:cs="{0}"/>'.format(font)
        if bold:
            rpr += "<w:b/>"
        if color:
            rpr += '<w:color w:val="{}"/>'.format(color)
        rpr += '<w:sz w:val="{0}"/><w:szCs w:val="{0}"/>'.format(int(size * 2))
        return ('<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/><w:vAlign w:val="center"/>'
                '</w:tcPr><w:p><w:pPr><w:spacing w:before="0" w:after="0"/><w:jc w:val="{jc}"/>'
                '</w:pPr><w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{t}</w:t></w:r></w:p>'
                '</w:tc>').format(w=w, jc=jc, rpr=rpr, t=escape("" if text is None else str(text)))
    b = '<w:{0} w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
    borders = "".join(b.format(k) for k in ("top", "left", "bottom", "right", "insideH",
                                            "insideV"))
    out = ['<w:tbl><w:tblPr><w:tblW w:w="{}" w:type="dxa"/><w:jc w:val="center"/>'
           '<w:tblBorders>{}</w:tblBorders><w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid>'
           .format(sum(widths), borders)]
    out.append("".join('<w:gridCol w:w="{}"/>'.format(w) for w in widths) + "</w:tblGrid>")
    out.append('<w:tr><w:trPr><w:tblHeader/></w:trPr>' + "".join(
        cell(h, w, True, head_color) for h, w in zip(headers, widths)) + "</w:tr>")
    for k, r in enumerate(rows):
        b = bold_last and k == len(rows) - 1
        out.append("<w:tr><w:trPr><w:cantSplit/></w:trPr>" + "".join(
            cell(v, w, bold=b, jc=a) for v, w, a in zip(r, widths, align)) + "</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


# ---------------------------------------------------------------- tables
_TR = re.compile(r"<w:tr\b.*?</w:tr>", re.S)
_TC = re.compile(r"<w:tc>.*?</w:tc>|<w:tc .*?</w:tc>", re.S)


def table_rows(tbl):
    return _TR.findall(tbl)


def row_cells(tr):
    return _TC.findall(tr)


def set_cell_text(tc, value, black=True, rpr_override=None):
    """Set a cell's text, keeping its paragraph properties. Uses the paragraph-mark
    run properties (or the first run's) for the new run."""
    value = "" if value is None else str(value)
    p = re.search(r"<w:p\b.*?</w:p>|<w:p\b[^>]*/>", tc, re.S)
    if p is None:
        return tc
    ptxt = p.group(0)
    ppr = re.search(r"<w:pPr>.*?</w:pPr>", ptxt, re.S)
    ppr_xml = ppr.group(0) if ppr else ""
    rpr = None
    cands = re.findall(r"<w:r\b[^>]*>(<w:rPr>.*?</w:rPr>)", ptxt, re.S)
    plain = [c for c in cands if 'w:val="FF0000"' not in c]
    if plain or cands:
        rpr = (plain or cands)[0]
    elif ppr:
        m2 = re.search(r"<w:rPr>.*?</w:rPr>", ppr_xml, re.S)
        rpr = m2.group(0) if m2 else None
    if rpr_override is not None:
        rpr = rpr_override
    elif rpr and black:
        rpr = re.sub(r'<w:color w:val="[0-9A-Fa-f]+"/>', "", rpr)
        rpr = re.sub(r"<w:highlight [^>]*/>", "", rpr)
    runs = []
    for k, line in enumerate(value.split("\n")):
        if k:
            runs.append("<w:r>{}<w:br/></w:r>".format(rpr or ""))
        runs.append('<w:r>{}<w:t xml:space="preserve">{}</w:t></w:r>'.format(
            rpr or "", escape(line)))
    p_open = re.match(r"<w:p\b[^>]*>", ptxt)
    p_open = p_open.group(0) if p_open and not ptxt.endswith("/>") else "<w:p>"
    new_p = p_open + ppr_xml + "".join(runs) + "</w:p>"
    # keep only the first paragraph of the cell
    tcpr = re.search(r"<w:tcPr>.*?</w:tcPr>", tc, re.S)
    tc_open = re.match(r"<w:tc\b[^>]*>", tc).group(0)
    return tc_open + (tcpr.group(0) if tcpr else "") + new_p + "</w:tc>"


def set_row(tr, values, start_col=0, black=True, rpr=None):
    cells = row_cells(tr)
    out = tr
    for k, v in enumerate(values):
        ci = start_col + k
        if ci >= len(cells) or v is None:
            continue
        new = set_cell_text(cells[ci], v, black, rpr)
        out = out.replace(cells[ci], new, 1)
        cells = row_cells(out)
    return out


def no_bookmarks(xml):
    """Copies of template blocks must not repeat bookmark ids."""
    return re.sub(r"<w:bookmark(?:Start|End)\b[^>]*/>", "", xml)


def set_rows(tbl, header_rows, template_row, data, rpr=None):
    """Keep the first `header_rows` rows, then one row per item of `data` (lists of cell
    values) built from row index `template_row`."""
    rows = table_rows(tbl)
    keep = rows[:header_rows]
    tpl = rows[template_row]
    new_rows = [no_bookmarks(set_row(tpl, vals, rpr=rpr)) if k else set_row(tpl, vals, rpr=rpr)
                for k, vals in enumerate(data)]
    head = tbl[:tbl.index(rows[0])]
    tail = tbl[tbl.rindex(rows[-1]) + len(rows[-1]):]
    return head + "".join(keep + new_rows) + tail


def fill_labels(tbl, values, col=1, match_col=0, rpr=None):
    """Fill column `col` of the rows whose label (column `match_col`) starts with a key."""
    out = tbl
    for tr in table_rows(tbl):
        cells = row_cells(tr)
        if len(cells) <= max(col, match_col):
            continue
        label = " ".join(text_of(cells[match_col]).split()).lower()
        for key, v in values.items():
            if label.startswith(" ".join(key.split()).lower()):
                out = out.replace(tr, set_row(tr, [v], start_col=col, rpr=rpr), 1)
                break
    return out


def fill_label_suffix(tbl, values):
    """For cells holding 'Label: ' (label and value in one cell), append the value."""
    out = tbl
    for tc in _TC.findall(tbl):
        t = text_of(tc).strip()
        for key, v in values.items():
            if t.lower().startswith(key.lower()) and v:
                new = replace_text(tc, t if t in tc else key, "{} {}".format(t, v))
                out = out.replace(tc, new, 1)
                break
    return out


# ---------------------------------------------------------------- package
class Template:
    def __init__(self, path):
        z = zipfile.ZipFile(path)
        self.parts = {n: z.read(n) for n in z.namelist()}
        self.infos = {i.filename: i for i in z.infolist()}
        xml = self.parts["word/document.xml"].decode("utf-8")
        self.pre, self.blocks, self.post = split_body(xml)
        self.rels = self.parts["word/_rels/document.xml.rels"].decode("utf-8")
        self._img = 0
        self._pid = 5000

    def find(self, needle, start=0, kind=None):
        """Index of the first block at or after `start` whose text contains `needle`."""
        nl = " ".join(needle.split()).lower()
        for i in range(start, len(self.blocks)):
            b = self.blocks[i]
            if kind and not b.startswith("<" + kind):
                continue
            if nl in " ".join(text_of(b).split()).lower():
                return i
        return None

    def text(self, i):
        return text_of(self.blocks[i])

    def add_png(self, data, width_in, max_height_in=None, name="Map"):
        """Add a PNG and return the paragraph XML showing it `width_in` inches wide."""
        w, h = png_size(data)
        cx = width_in * EMU_PER_INCH
        cy = cx * h / float(w)
        if max_height_in and cy > max_height_in * EMU_PER_INCH:
            cy = max_height_in * EMU_PER_INCH
            cx = cy * w / float(h)
        self._img += 1
        fname = "media/dwm_map{}.png".format(self._img)
        self.parts["word/" + fname] = data
        rid = "rIdDwm{}".format(self._img)
        self.rels = self.rels.replace(
            "</Relationships>", '<Relationship Id="{}" Type="{}" Target="{}"/></Relationships>'
            .format(rid, IMG_REL, fname))
        self._pid += 1
        return image_para(rid, cx, cy, self._pid, "{} {}".format(name, self._img))

    def save(self, path):
        ct = self.parts["[Content_Types].xml"].decode("utf-8")
        ct = ct.replace("wordprocessingml.template.main+xml",
                        "wordprocessingml.document.main+xml")
        if 'Extension="png"' not in ct:
            ct = ct.replace("<Default ", '<Default Extension="png" ContentType="image/png"/>'
                            "<Default ", 1)
        self.parts["[Content_Types].xml"] = ct.encode("utf-8")
        doc = self.pre + "".join(self.blocks) + self.post
        # drop pictures no longer used (the template's example maps)
        used = set(re.findall(r'r:(?:embed|id|link)="([^"]+)"', doc))
        rels = self.rels
        for m in list(re.finditer(r'<Relationship [^>]*/>', rels)):
            rel = m.group(0)
            if 'relationships/image"' not in rel:
                continue
            rid = re.search(r'Id="([^"]+)"', rel).group(1)
            if rid in used:
                continue
            target = re.search(r'Target="([^"]+)"', rel).group(1)
            rels = rels.replace(rel, "")
            if target.count("Target") == 0 and ('Target="{}"'.format(target) not in rels):
                self.parts.pop("word/" + target, None)
        self.rels = rels
        self.parts["word/_rels/document.xml.rels"] = self.rels.encode("utf-8")
        self.parts["word/document.xml"] = doc.encode("utf-8")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in self.parts.items():
                if name.endswith("/"):
                    continue
                info = self.infos.get(name)
                if info is not None:
                    zi = zipfile.ZipInfo(name, date_time=info.date_time)
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    z.writestr(zi, data)
                else:
                    z.writestr(name, data)
