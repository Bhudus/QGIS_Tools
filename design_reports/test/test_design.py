import os, sys, time
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.design import *

# pipe schedule
a = np.array([[0, 0, 100.0, 104], [455, 0, 99.5, 104.2]])
r = pipe_schedule([dict(id=1, kind="lateral", size="4 in", a=a), dict(id=2, kind="main", size="8 in", a=a)])
assert r[0]["kind"] == "main" and r[1]["length"] == 460 and abs(r[1]["slope"] - 0.10) < 1e-9, r
assert r[1]["label"] == "4 in 460 ft 0.10%", r[1]["label"]
assert depth_range([a]) == (4.0, 4.7) or np.allclose(depth_range([a]), (4.0, 4.7))

d, why = irrigated_buffer(60, 100); assert d == 30 and "half" in why
d, why = irrigated_buffer(80, 100); assert abs(d - 100 / 3) < 1e-9 and "third" in why

g = np.add.outer(np.arange(10.), np.zeros(10))
zm = np.ones((10, 10), bool)
m = impacted_mask(zm, zm, g, 2.0, 1.5); assert m.sum() == 40
m2 = impacted_mask(zm, zm, g, 2.0, 0.8); assert m2.sum() == 100

# terrain: field in the middle of a bowl-shaped slope with a pit and a hill outside draining in
n = 120
y, x = np.mgrid[0:n, 0:n].astype(float)
z = 100 + 0.02 * x + 0.0 * y           # rises to the east; water flows west
z += -1.5 * np.exp(-((x - 50) ** 2 + (y - 60) ** 2) / 30.0)    # pit inside field
field = (x >= 30) & (x < 80) & (y >= 30) & (y < 90)
t = time.time()
res = depressions_and_runon(z, field, 3.0, depth_thr=0.5, min_dep_ac=0.0, min_runon_ac=0.1)
print("terrain %.2fs" % (time.time() - t), [round(s["area_ac"], 3) for s in res["deps"]],
      [(round(s["area_ac"], 2), s["edge"]) for s in res["runs"]])
assert len(res["deps"]) == 1 and res["deps"][0]["max_depth"] > 1.0
# run-on: everything east of the field in rows 30-89 drains west into it
assert res["runs"] and res["runs"][0]["edge"]
rl = res["run_labels"] > 0
assert rl[60, 100] and not rl[60, 10] and not rl[5, 100]

assert coarsen(np.arange(16.).reshape(4, 4), 2).tolist() == [[2.5, 4.5], [10.5, 12.5]]
c = sheet_centers(0, 0, 100, 50, 60, 60)
assert len(c) == 2, c
assert len(sheet_centers(0, 0, 50, 50, 60, 60)) == 1
print("design ok")

# page limit: a 3000 x 5000 ft field on 6.5 x 7.4 in sheets
import math
def pages(s):
    w, h = s * 6.5 / 12 * 0.95, s * 7.4 / 12 * 0.95
    return math.ceil(3000 / w) * math.ceil(5000 / h)
fit = 9600
assert sheet_scale_for_pages(pages, 2400, fit, 0) == (2400, pages(2400))      # no limit
assert pages(2400) > 4
s, n = sheet_scale_for_pages(pages, 2400, fit, 4)
assert n <= 4 and s > 2400 and pages(s) == n, (s, n)
prev = [x for x in (3000, 3600, 4800, 6000, 7200) if x < s]
assert all(pages(x) > 4 for x in prev if x > 2400)                            # largest scale that fits
assert sheet_scale_for_pages(pages, 2400, fit, 1) == (fit, None)              # one page
assert sheet_scale_for_pages(pages, 2400, fit, 100) == (2400, pages(2400))    # already fits
print("page limit ok", s, n, pages(2400))
