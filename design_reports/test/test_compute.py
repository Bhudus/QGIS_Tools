import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.compute import *

assert fmt_latlon(40.12345678, -88.5) == "40.12346° N / 88.50000° W"
assert nice_scale(3500) == 3600 and nice_scale(3600) == 3600 and nice_scale(3610) == 4800
assert scale_text(3600) == "1:3,600 (1 in = 300 ft)", scale_text(3600)
assert scalebar_length(3600, 6.5) == 250

# smooth keeps a plane
z = np.add.outer(np.arange(20.), np.arange(30.))
assert np.allclose(smooth(z, 2)[5:-5, 5:-5], z[5:-5, 5:-5])
z2 = z.copy(); z2[3, 3] = np.nan
assert np.isfinite(smooth(z2, 1)).sum() == z2.size - 1

# polygon mask: square with a hole
outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
hole = [(4, 4), (6, 4), (6, 6), (4, 6)]
m = polygon_mask([outer, hole], 0, 10, 1, -1, 10, 10)
assert m.sum() == 100 - 4, m.sum()
m2 = polygon_mask([[(0, 0), (10, 0), (0, 10)]], 0, 10, 1, -1, 10, 10)
assert 40 <= m2.sum() <= 55

# lowest cell inside the polygon only
zz = np.full((10, 10), 100.0); zz[0, 0] = 50; zz[7, 2] = 90
low = lowest_cell(zz, m2, 0, 10, 1, -1)
assert low[2] == 90 and abs(low[0] - 2.5) < 1e-9 and abs(low[1] - 2.5) < 1e-9, low

# depth stats
line = np.array([[0, 0, 96, 100], [100, 0, 95, 100]])
mean, lo, hi = depth_stats([line])
assert abs(mean - 4.5) < 1e-9 and lo == 4 and hi == 5

# spacing of parallel laterals 30 apart
lats = [np.array([[0, y], [300, y]]) for y in (0, 30, 60, 90)]
sp = lateral_spacing(lats)
assert abs(sp - 30) < 1e-6, sp
assert lateral_spacing(lats[:1]) is None

rows = component_rows([("lateral", "4 in", 100), ("main", "8 in", 50), ("main", "6 in", 20),
                       ("lateral", "4 in", 30)], "HDPE")
assert [r[0] for r in rows] == ["Main (8 in)", "Main (6 in)", "Laterals (4 in)"], rows
assert rows[2][2] == 130

sr = soil_rows([dict(musym="152A", muname="Drummer", acres=10, spacing=90),
                dict(musym="152A", muname="Drummer", acres=30, spacing=100),
                dict(musym="154A", muname="Flanagan", acres=5, spacing=None)])
assert sr[0][0] == "152A" and abs(sr[0][3] - 97.5) < 1e-9 and sr[1][3] is None
assert series_name("Drummer silty clay loam, 0 to 2 percent slopes") == "Drummer"

assert release_date("Soybeans") == "April 1" and release_date("corn") == "March 1"
assert parse_schedule("June 15, 2027, 1, 703.1; Aug 1, 2, 704") == [
    ["June 15, 2027", "1", "703.10 ft"], ["Aug 1", "2", "704.00 ft"]]
try:
    parse_schedule("June 15, 1"); raise SystemExit("no error")
except ValueError:
    pass
assert crop_text([dict(field="F3", rotation="Corn-soybean", sequence="C S C", years="2023-2025")]) \
    == "F3: Corn-soybean (2023-2025: C S C)"
print("compute ok")
