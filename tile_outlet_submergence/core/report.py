"""HTML report with inline SVG charts for the backwater analysis (pure Python)."""
import base64
import csv
import html
import io
import math

CSS = """
body{font-family:'Segoe UI',Arial,sans-serif;font-size:11pt;color:#222;margin:24px;max-width:1050px}
h1{font-size:17pt;color:#13294B;margin:0 0 4px} h2{font-size:13pt;color:#13294B;margin:22px 0 6px}
.meta{color:#555;font-size:9.5pt} table{border-collapse:collapse;margin:6px 0 12px}
th,td{border:1px solid #c8ccd2;padding:3px 7px;font-size:9.5pt;text-align:right}
th{background:#eef1f5;text-align:center} td.l{text-align:left}
.box{border-left:6px solid #FF5F05;background:#f4f4f4;padding:8px 12px;margin:12px 0;font-size:9.5pt}
svg text{font-family:'Segoe UI',Arial,sans-serif}
.tbar{margin:4px 0 2px} a.btn{display:inline-block;padding:4px 12px;border:1px solid #13294B;border-radius:3px;color:#13294B;text-decoration:none;font-size:9.5pt}
a.btn:hover{background:#13294B;color:#fff} @media print{.tbar{display:none}}
"""


def _nice(lo, hi, n=6):
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        hi = lo + 1.0
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / n))
    for m in (1, 2, 2.5, 5, 10):
        if span / (step * m) <= n:
            step *= m
            break
    a = math.floor(lo / step) * step
    b = math.ceil(hi / step) * step
    ticks = []
    t = a
    while t <= b + step * 1e-9:
        ticks.append(round(t, 10))
        t += step
    return a, b, ticks


def line_chart(series, xlabel, ylabel, width=900, height=380, vlines=None, ylim=None,
               hook=None, svg_id=None):
    """series: [(label, color, [(x, y)], dash)]; vlines: [(x, label, color)];
    hook(sx, sy, y0, y1) -> extra SVG drawn over the series (sx, sy: data -> pixels)."""
    pad_l, pad_r, pad_t, pad_b = 70, 20, 20, 50
    xs = [x for _, _, pts, _ in series for x, _ in pts]
    ys = [y for _, _, pts, _ in series for _, y in pts if y is not None and math.isfinite(y)]
    if not xs or not ys:
        return ""
    x0, x1, xt = _nice(min(xs), max(xs))
    y0, y1, yt = _nice(*(ylim if ylim else (min(ys), max(ys))))
    W, H = width - pad_l - pad_r, height - pad_t - pad_b

    def sx(x):
        return pad_l + (x - x0) / (x1 - x0) * W

    def sy(y):
        return pad_t + (1 - (y - y0) / (y1 - y0)) * H
    o = ['<svg{} width="{}" height="{}" viewBox="0 0 {} {}" xmlns="http://www.w3.org/2000/svg">'
         .format(' id="{}"'.format(svg_id) if svg_id else "", width, height + 24, width,
                 height + 24)]
    for t in yt:
        o.append('<line x1="{:.1f}" x2="{:.1f}" y1="{:.1f}" y2="{:.1f}" stroke="#e3e6ea"/>'
                 .format(pad_l, pad_l + W, sy(t), sy(t)))
        o.append('<text x="{:.1f}" y="{:.1f}" font-size="11" text-anchor="end" fill="#555">{:g}'
                 '</text>'.format(pad_l - 6, sy(t) + 4, t))
    for t in xt:
        o.append('<line x1="{:.1f}" x2="{:.1f}" y1="{:.1f}" y2="{:.1f}" stroke="#eef0f2"/>'
                 .format(sx(t), sx(t), pad_t, pad_t + H))
        o.append('<text x="{:.1f}" y="{:.1f}" font-size="11" text-anchor="middle" fill="#555">'
                 '{:g}</text>'.format(sx(t), pad_t + H + 16, t))
    o.append('<rect x="{}" y="{}" width="{}" height="{}" fill="none" stroke="#999"/>'.format(
        pad_l, pad_t, W, H))
    for k, (x, lab, col) in enumerate(sorted(vlines or [], key=lambda v: v[0])):
        if x0 <= x <= x1:
            o.append('<line x1="{0:.1f}" x2="{0:.1f}" y1="{1}" y2="{2}" stroke="{3}" '
                     'stroke-dasharray="4 3"/>'.format(sx(x), pad_t, pad_t + H, col))
            o.append('<text x="{:.1f}" y="{:.1f}" font-size="10.5" fill="{}">{}</text>'.format(
                sx(x) + 4, pad_t + 12 + 14 * (k % 3), col, html.escape(lab)))
    for lab, col, pts, dash in series:
        pts = [(x, y) for x, y in pts if y is not None and math.isfinite(y)]
        if len(pts) < 2:
            continue
        d = " ".join("{:.1f},{:.1f}".format(sx(x), sy(min(max(y, y0), y1))) for x, y in pts)
        o.append('<polyline points="{}" fill="none" stroke="{}" stroke-width="2"{}/>'.format(
            d, col, ' stroke-dasharray="6 4"' if dash else ""))
    if hook:
        o.append(hook(sx, sy, y0, y1))
    o.append('<text x="{:.1f}" y="{:.1f}" font-size="12" text-anchor="middle">{}</text>'.format(
        pad_l + W / 2, height + 6, html.escape(xlabel)))
    o.append('<text transform="translate(16,{:.1f}) rotate(-90)" font-size="12" '
             'text-anchor="middle">{}</text>'.format(pad_t + H / 2, html.escape(ylabel)))
    lx = pad_l + 10
    for lab, col, _, dash in series:
        o.append('<line x1="{0}" x2="{1}" y1="{2}" y2="{2}" stroke="{3}" stroke-width="2"{4}/>'
                 '<text x="{5}" y="{6}" font-size="11">{7}</text>'.format(
                     lx, lx + 22, height + 20, col, ' stroke-dasharray="6 4"' if dash else "",
                     lx + 26, height + 24, html.escape(lab)))
        lx += 34 + 7 * len(lab)
    o.append("</svg>")
    return "".join(o)


RATING_COLUMNS = (
    # (csv header, key, decimals, report header)
    ("level_above_invert_{l}", "level", 3, "Ditch level above invert ({l})"),
    ("ditch_elev_{l}", "elev", 3, "Ditch elevation ({l})"),
    ("outflow_{q}", "outflow", 4, "Outflow ({q})"),
    ("pct_of_free_outlet", "pct_free", 2, "% of free outlet"),
    ("pct_area_reduced_gt10", "area_10", 2, "Area reduced &gt;10% (%)"),
    ("pct_area_reduced_gt50", "area_50", 2, "Area reduced &gt;50% (%)"),
    ("pct_area_not_draining", "area_none", 2, "Area not draining (%)"),
    ("pct_area_ditch_above_wt", "backflow", 2, "Ditch above water table (%)"),
    ("max_hgl_rise_{l}", "max_rise", 3, "Max HGL rise ({l})"),
    ("surcharged_pipe_{l}", "surch_len", 1, "Surcharged pipe ({l})"),
)


def rating_csv(rating, lu, qu):
    """The rating curve as CSV text: one row for the free outlet (level 'free'), then one
    per ditch level. Units are in the column names."""
    q = qu.replace("L/s", "Lps")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([c[0].format(l=lu, q=q) for c in RATING_COLUMNS])
    for r in rating:
        row = []
        for _, key, nd, _ in RATING_COLUMNS:
            v = r.get(key)
            if v is None:
                row.append("free" if key == "level" else "")
            else:
                row.append("{:.{}f}".format(v, nd))
        w.writerow(row)
    return buf.getvalue()


def report_html(meta, rating, profile, map_summary, explorer=None):
    """meta: title, generated, plugin, units (l, q), inputs [(label, value)], notes [..];
    rating: list of dicts (level, elev, outflow, pct_free, area_10, area_50, area_none,
    backflow, max_rise, surch_len); profile: dict with dist, invert, crown, ground,
    hgl_free, hgl_map, map_level_elev (or None); map_summary: dict for the mapped level."""
    e = html.escape
    lu, qu = meta["units"]["l"], meta["units"]["q"]
    o = ["<!DOCTYPE html><html><head><meta charset='utf-8'><title>{}</title><style>{}</style>"
         "</head><body>".format(e(meta["title"]), CSS)]
    o.append("<h1>{}</h1><div class='meta'>Generated {} by {}.</div>".format(
        e(meta["title"]), e(meta["generated"]), e(meta["plugin"])))
    o.append("<h2>Inputs</h2><table>{}</table>".format("".join(
        "<tr><td class='l'>{}</td><td class='l'>{}</td></tr>".format(e(a), e(str(b)))
        for a, b in meta["inputs"])))
    pts = [(r["level"], r["pct_free"]) for r in rating if r["level"] is not None]
    pts10 = [(r["level"], 100 - r["area_10"]) for r in rating if r["level"] is not None]
    vl = [(meta["outlet_d"], "outlet crown", "#b45309")]
    if meta.get("bank") is not None:
        vl.append((meta["bank"], "ground at outlet", "#166534"))
    if meta.get("map_level") is not None:
        vl.append((meta["map_level"], "mapped level", "#1d4ed8"))
    o.append("<h2>Rating curve</h2>")
    data = base64.b64encode(rating_csv(rating, lu, qu).encode("utf-8-sig")).decode("ascii")
    o.append("<div class='tbar'><a class='btn' download='{}' href='data:text/csv;base64,{}'>"
             "Export CSV</a></div>".format(e(meta.get("csv_name", "rating_curve.csv")), data))
    o.append("<table><tr>{}</tr>".format("".join(
        "<th>{}</th>".format(c[3].format(l=e(lu), q=e(qu))) for c in RATING_COLUMNS)))
    for ri, r in enumerate(rating):
        o.append("<tr id='rr{}'>{}</tr>".format(ri, "".join("<td>{}</td>".format(v) for v in (
            "free" if r["level"] is None else "{:.2f}".format(r["level"]),
            "-" if r["elev"] is None else "{:.2f}".format(r["elev"]),
            "{:.3f}".format(r["outflow"]), "{:.1f}".format(r["pct_free"]),
            "{:.1f}".format(r["area_10"]), "{:.1f}".format(r["area_50"]),
            "{:.1f}".format(r["area_none"]), "{:.1f}".format(r["backflow"]),
            "{:.2f}".format(r["max_rise"]), "{:,.0f}".format(r["surch_len"])))))
    o.append("</table>")
    o.append(line_chart([("System outflow, % of free outlet", "#13294B", pts, False),
                         ("Drained area within 10% of free-outlet rate, %", "#FF5F05", pts10,
                          True)],
                        "Ditch water level above the outlet invert ({})".format(lu),
                        "% of free outlet", vlines=vl, ylim=(0, 100)))
    if explorer:
        o.append(explorer_html(explorer))
    if profile and profile.get("dist"):
        d = profile["dist"]
        ser = [("Ground", "#166534", list(zip(d, profile["ground"])), False),
               ("Pipe crown", "#9ca3af", list(zip(d, profile["crown"])), True),
               ("Invert", "#4b5563", list(zip(d, profile["invert"])), False),
               ("HGL, free outlet", "#0ea5e9", list(zip(d, profile["hgl_free"])), False)]
        if profile.get("hgl_map"):
            ser.append(("HGL, mapped ditch level", "#1d4ed8", list(zip(d, profile["hgl_map"])),
                        False))
            ser.append(("Design water table", "#a16207", list(zip(d, profile["wt"])), True))
        o.append("<h2>Main profile</h2>")
        o.append(line_chart(ser, "Distance up the main from the outlet ({})".format(lu),
                            "Elevation ({})".format(lu)))
    if map_summary:
        o.append("<h2>At the mapped ditch level</h2><div class='meta'>{}</div>".format(
            e(map_summary)))
    o.append("<h2>Method and assumptions</h2><ul>{}</ul>".format(
        "".join("<li>{}</li>".format(e(n)) for n in meta["notes"])))
    o.append("</body></html>")
    return "".join(o)


# ------------------------------------------------------------------ ditch-level explorer
XP_CSS = """
.xp{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap;outline:none}
.xp-side{width:290px;flex:none;font-size:10pt} .xp-side h3{font-size:10.5pt;color:#13294B;margin:12px 0 3px}
.xp-arrow{display:block;width:100%;font-size:15pt;line-height:1.2;padding:2px 0;border:1px solid #13294B;
 background:#fff;color:#13294B;border-radius:3px;cursor:pointer}
.xp-arrow:hover:not(:disabled){background:#13294B;color:#fff} .xp-arrow:disabled{opacity:.35;cursor:default}
.xp-lvl{text-align:center;padding:6px 0} .xp-big{font-size:13pt;font-weight:600;color:#13294B}
.xp-q{font-size:11.5pt;font-weight:600} .xp-sm{color:#555;font-size:9.5pt}
.xp-flap{margin-top:10px;padding:6px 8px;font-size:9.5pt;border-left:5px solid #16a34a;background:#f0fdf4}
.xp-flap.warn{border-left-color:#b91c1c;background:#fef2f2}
.xp-map{position:relative;border:1px solid #c8ccd2;background:#fafafa}
.xp-map canvas{position:absolute;left:0;top:0;image-rendering:pixelated}
.xp-map svg{position:absolute;left:0;top:0;cursor:crosshair}
.xp-leg{font-size:9pt;color:#444;margin-top:4px;display:flex;gap:18px;flex-wrap:wrap;align-items:center}
.xp-leg i{display:inline-block;width:18px;height:4px;margin:0 4px 2px 8px;vertical-align:middle}
.xp-ramp{display:inline-block;width:120px;height:10px;vertical-align:middle;margin:0 4px}
.xp-charts{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px}
tr.cur td{background:#fff3cd}
@media print{.xp-arrow{display:none}}
"""

XP_JS = r"""
(function(){
var X=JSON.parse(document.getElementById('xp-data').textContent);
var S=X.steps,M=X.map,U=X.units,N=S.level.length,i=X.map_index,show='both',pick=null;
var stops=[[0,[185,28,28]],[50,[245,158,11]],[90,[253,230,138]],[100,[22,163,74]]];
var lut=[];for(var v=0;v<256;v++){if(v>100){lut.push([0,0,0,0]);continue;}
 for(var k=1;k<stops.length;k++)if(v<=stops[k][0])break;
 var a=stops[k-1],b=stops[k],t=(v-a[0])/(b[0]-a[0]),c=[0,0,0,215];
 for(var j=0;j<3;j++)c[j]=Math.round(a[1][j]+t*(b[1][j]-a[1][j]));lut.push(c);}
var RB=[0.0001,0.25,1.0],RC=['#16a34a','#a3e635','#f59e0b','#b91c1c'];
function rc(v){for(var k=0;k<RB.length;k++)if(v<RB[k])return RC[k];return RC[RB.length];}
function $(id){return document.getElementById(id);}
var cv=$('xp-img'),vals=[];
function load(k,cb){if(vals[k])return cb(vals[k]);var im=new Image();im.onload=function(){
 var c=document.createElement('canvas');c.width=M.iw;c.height=M.ih;var g=c.getContext('2d');
 g.drawImage(im,0,0);var d=g.getImageData(0,0,M.iw,M.ih).data,a=new Uint8Array(M.iw*M.ih);
 for(var p=0;p<a.length;p++)a[p]=d[4*p];vals[k]=a;cb(a);};im.src='data:image/png;base64,'+M.png[k];}
function raster(){var k=i;load(k,function(a){if(k!==i)return;var g=cv.getContext('2d'),
 im=g.createImageData(M.iw,M.ih);for(var p=0;p<a.length;p++)im.data.set(lut[a[p]],4*p);
 g.putImageData(im,0,0);});}
var paths=document.querySelectorAll('#xp-pipes polyline');
function f2(v,n){return v.toFixed(n);}
function sg(v,n){var f=Math.pow(10,n);v=Math.round(v*f)/f;if(v===0)v=0;return (v>=0?'+':'')+v.toFixed(n);}
function levelText(){var lv=S.level[i],c=X.crown;
 if(lv===null)return['Free outlet','Ditch below the outlet invert','Outlet pipe flowing freely'];
 var t;if(lv>c+1e-9)t=f2(lv-c,2)+' '+U.l+' over the outlet crown ('+f2((lv-c)/c,1)+' D)';
 else if(lv<=1e-9)t='Ditch at the outlet invert';
 else t='Outlet pipe '+Math.round(100*lv/c)+'% submerged (crown at '+f2(c,2)+' '+U.l+')';
 return[f2(lv,2)+' '+U.l+' above the outlet invert','Ditch elevation '+f2(S.elev[i],2)+' '+U.l,t];}
function readout(){if(!pick){$('xp-read').innerHTML='Click a pipe or a field cell on the map.';return;}
 var h=[];if(pick.e>=0){var P=M.pipes[pick.e];
  h.push('<b>Pipe</b>: '+P.k+', '+P.d+' in<br>Flow '+f2(M.q[i][pick.e],4)+' '+U.q+
   ' (free outlet '+f2(M.q[0][pick.e],4)+')<br>HGL raised '+f2(M.rise[i][pick.e],2)+' '+U.l);}
 var v=pick.v[i];h.push('<b>Field</b>: '+(v>100?'not in the drained area':
  'drains at '+v+'% of the free-outlet rate'));$('xp-read').innerHTML=h.join('<br>');}
function update(){var t=levelText();$('xp-h1').textContent=t[0];$('xp-h2').textContent=t[1];
 $('xp-h3').textContent=t[2];$('xp-n').textContent='Step '+(i+1)+' of '+N;
 $('xp-up').disabled=i>=N-1;$('xp-dn').disabled=i<=0;
 $('xp-q').textContent=f2(S.outflow[i],3)+' '+U.q+', '+f2(S.pct[i],1)+'% of the free outlet';
 $('xp-dq').textContent=i===0?'Change from the step below: -':'Change from the step below: '+
  sg(S.outflow[i]-S.outflow[i-1],3)+' '+U.q+' ('+sg(S.pct[i]-S.pct[i-1],1)+' points)';
 var b=S.backflow[i],w=S.level[i]!==null&&b>0.05,fl=$('xp-flap');fl.className='xp-flap'+(w?' warn':'');
 fl.textContent=w?'The ditch stands above the design water table under '+f2(b,1)+
  '% of the drained area: without a flap gate, water could run back into the field.':
  'The ditch stays below the design water table across the drained area.';
 for(var e=0;e<paths.length;e++)paths[e].setAttribute('stroke',rc(M.rise[i][e]));
 $('xp-hgl').setAttribute('points',X.hglpts[i]);var r=X.rmark[i];
 ['xp-rv','xp-rc'].forEach(function(id){$(id).setAttribute('visibility',r?'visible':'hidden');});
 if(r){$('xp-rv').setAttribute('x1',r[0]);$('xp-rv').setAttribute('x2',r[0]);
  $('xp-rc').setAttribute('cx',r[0]);$('xp-rc').setAttribute('cy',r[1]);}
 var rows=document.querySelectorAll('tr.cur');for(var k=0;k<rows.length;k++)rows[k].className='';
 var tr=$('rr'+i);if(tr)tr.className='cur';
 if(show!=='pipes')raster();if(pick)loadAll(readout);else readout();}
function loadAll(cb){var n=0;for(var k=0;k<N;k++)load(k,function(){if(++n===N)cb();});}
function step(d){var j=Math.max(0,Math.min(N-1,i+d));if(j!==i){i=j;update();}}
$('xp-up').onclick=function(){step(1);};$('xp-dn').onclick=function(){step(-1);};
$('xp').addEventListener('keydown',function(ev){if(ev.key==='ArrowUp'){step(1);ev.preventDefault();}
 else if(ev.key==='ArrowDown'){step(-1);ev.preventDefault();}});
document.querySelectorAll('input[name=xp-show]').forEach(function(r){r.onchange=function(){
 show=r.value;cv.style.display=show==='pipes'?'none':'';
 $('xp-pipes').style.display=show==='raster'?'none':'';if(show!=='pipes')raster();};});
function segd(px,py,ax,ay,bx,by){var dx=bx-ax,dy=by-ay,L=dx*dx+dy*dy,t=L>0?((px-ax)*dx+(py-ay)*dy)/L:0;
 t=Math.max(0,Math.min(1,t));var x=ax+t*dx-px,y=ay+t*dy-py;return Math.sqrt(x*x+y*y);}
$('xp-svg').addEventListener('click',function(ev){var bb=this.getBoundingClientRect(),
 x=(ev.clientX-bb.left)*M.w/bb.width,y=(ev.clientY-bb.top)*M.h/bb.height,best=-1,bd=7;
 if(show!=='raster')for(var e=0;e<M.pipes.length;e++){var p=M.pipes[e].p;
  for(var k=0;k+3<p.length;k+=2){var d=segd(x,y,p[k],p[k+1],p[k+2],p[k+3]);if(d<bd){bd=d;best=e;}}}
 var ix=Math.min(M.iw-1,Math.floor(x/M.w*M.iw)),iy=Math.min(M.ih-1,Math.floor(y/M.h*M.ih)),
 o=iy*M.iw+ix,pv=[];var c=$('xp-pick');c.setAttribute('cx',x);c.setAttribute('cy',y);
 c.setAttribute('visibility','visible');
 loadAll(function(){for(var k=0;k<N;k++)pv.push(vals[k][o]);pick={e:best,v:pv};readout();});});
update();
})();
"""


def _rating_mini(ex):
    st = ex["steps"]
    pts = [(lv, p) for lv, p in zip(st["level"], st["pct"]) if lv is not None]
    marks = []

    def hook(sx, sy, y0, y1):
        for lv, p in zip(st["level"], st["pct"]):
            marks.append(None if lv is None else
                         [round(sx(lv), 1), round(sy(min(max(p, y0), y1)), 1)])
        return ('<line id="xp-rv" x1="0" x2="0" y1="{:.1f}" y2="{:.1f}" stroke="#1d4ed8" '
                'stroke-dasharray="4 3"/><circle id="xp-rc" r="5" fill="#1d4ed8" '
                'stroke="#fff" stroke-width="1.5"/>'.format(sy(y1), sy(y0)))
    svg = line_chart([("Outflow, % of free outlet", "#13294B", pts, False)],
                     "Ditch level above the outlet invert ({})".format(ex["units"]["l"]),
                     "% of free outlet", width=400, height=240, ylim=(0, 100), hook=hook)
    return svg, marks


def _profile_mini(ex):
    pr, lu = ex["profile"], ex["units"]["l"]
    d = pr["dist"]
    vals = [v for k in ("ground", "invert", "crown", "wt") for v in pr[k] if v is not None]
    vals += [v for h in pr["hgl"] for v in h if v is not None]
    lines = []

    def hook(sx, sy, y0, y1):
        for h in pr["hgl"]:
            lines.append(" ".join("{:.1f},{:.1f}".format(sx(x), sy(min(max(y, y0), y1)))
                                  for x, y in zip(d, h) if y is not None))
        return '<polyline id="xp-hgl" points="" fill="none" stroke="#1d4ed8" stroke-width="2.5"/>'
    ser = [("Ground", "#166534", list(zip(d, pr["ground"])), False),
           ("Design WT", "#a16207", list(zip(d, pr["wt"])), True),
           ("Crown", "#9ca3af", list(zip(d, pr["crown"])), True),
           ("Invert", "#4b5563", list(zip(d, pr["invert"])), False),
           ("HGL free", "#7dd3fc", list(zip(d, pr["hgl"][0])), False),
           ("HGL at level", "#1d4ed8", [], False)]
    svg = line_chart(ser, "Distance up the main from the outlet ({})".format(lu),
                     "Elevation ({})".format(lu), width=620, height=240,
                     ylim=(min(vals), max(vals)), hook=hook)
    return svg, lines


def explorer_html(ex):
    """Interactive section: step the ditch level up and down and watch the map, the
    main's profile and the outflow change. ex: explorer.report_payload()."""
    import json
    e = html.escape
    M, lu = ex["map"], ex["units"]["l"]
    rsvg, marks = _rating_mini(ex)
    psvg, lines = _profile_mini(ex)
    data = dict(ex)
    data["rmark"], data["hglpts"] = marks, lines
    paths = "".join(
        '<polyline points="{}" fill="none" stroke="#16a34a" stroke-width="{}" '
        'stroke-linecap="round" stroke-linejoin="round"/>'.format(
            " ".join("{:g},{:g}".format(p["p"][k], p["p"][k + 1])
                     for k in range(0, len(p["p"]), 2)),
            3.2 if p["k"] == "main" else (2.4 if p["k"] == "submain" else 1.5))
        for p in M["pipes"])
    ramp = "linear-gradient(90deg,#b91c1c 0%,#f59e0b 50%,#fde68a 90%,#16a34a 100%)"
    leg_pipes = "".join("<span><i style='background:{}'></i>{}</span>".format(c, e(t.format(u=lu)))
                        for c, t in zip(("#16a34a", "#a3e635", "#f59e0b", "#b91c1c"),
                                        ("no rise", "up to 0.25 {u}", "0.25 to 1 {u}",
                                         "more than 1 {u}")))
    o = ["<style>{}</style>".format(XP_CSS),
         "<h2>Ditch level explorer</h2><div class='meta'>Step the ditch level with the "
         "arrows (or the arrow keys once you have clicked the panel). Click the map for the "
         "values at a pipe or a field cell.</div>",
         "<div class='xp' id='xp' tabindex='0'><div class='xp-side'>",
         "<button class='xp-arrow' id='xp-up' title='Raise the ditch level'>&#9650;</button>",
         "<div class='xp-lvl'><div class='xp-big' id='xp-h1'></div><div id='xp-h2'></div>"
         "<div class='xp-sm' id='xp-h3'></div><div class='xp-sm' id='xp-n'></div></div>",
         "<button class='xp-arrow' id='xp-dn' title='Lower the ditch level'>&#9660;</button>",
         "<h3>System outflow</h3><div class='xp-q' id='xp-q'></div>"
         "<div class='xp-sm' id='xp-dq'></div><div class='xp-flap' id='xp-flap'></div>",
         "<h3>Show</h3><label><input type='radio' name='xp-show' value='raster'> Drainage "
         "rate</label> <label><input type='radio' name='xp-show' value='pipes'> Pipes</label> "
         "<label><input type='radio' name='xp-show' value='both' checked> Both</label>",
         "<h3>At the clicked point</h3><div class='xp-sm' id='xp-read'></div></div>",
         "<div style='width:{w}px;flex:none'><div class='xp-map' style='width:{w}px;height:{h}px'>"
         "<canvas id='xp-img' width='{iw}' height='{ih}' style='width:{w}px;height:{h}px'>"
         "</canvas><svg id='xp-svg' width='{w}' height='{h}' viewBox='0 0 {w} {h}' "
         "xmlns='http://www.w3.org/2000/svg'><g id='xp-pipes'>{paths}</g>"
         "<circle id='xp-pick' r='5' fill='none' stroke='#111' stroke-width='2' "
         "visibility='hidden'/></svg></div>".format(w=M["w"], h=M["h"], iw=M["iw"],
                                                    ih=M["ih"], paths=paths),
         "<div class='xp-leg'><span>Drainage rate, % of free outlet: 0<span class='xp-ramp' "
         "style='background:{}'></span>100</span><span>HGL rise:{}</span></div></div></div>"
         .format(ramp, leg_pipes),
         "<div class='xp-charts'>{}{}</div>".format(rsvg, psvg),
         "<script type='application/json' id='xp-data'>{}</script>".format(
             json.dumps(data, separators=(",", ":")).replace("</", "<\\/")),
         "<script>{}</script>".format(XP_JS)]
    return "".join(o)
