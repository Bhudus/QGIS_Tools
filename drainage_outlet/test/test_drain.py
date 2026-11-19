"""Tests for the district-drain routing in hydro.py (numpy only, no QGIS)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import hydro as h  # noqa: E402

n = 120
y, x = np.mgrid[0:n, 0:n].astype(float)
# Two valleys separated by a ridge at x = 60: west half drains to x = 20, east half to x = 100,
# both sloping south.
dem = 0.05 * np.minimum(abs(x - 20), abs(x - 100)) + 0.02 * (n - y)
dem += 5.0 * np.exp(-((x - 60) / 6.0) ** 2)          # the ridge
dem[40:60, 85:95] = dem[40:60, 85:95].min()           # a flat in the east valley
valid = np.ones_like(dem, bool)
zf = h.fill_depressions(dem, valid)
rcv = h.flow_directions(zf, valid, 10.0)

# District drain runs along the east valley floor (x = 100), rows 30..110
drain = np.zeros_like(valid)
drain[30:110, 99:102] = True

# 1) A start in the east valley drains naturally to the drain
s_east = 20 * n + 80
cells, why = h.trace_path(s_east, rcv, drain)
assert why == "stream", why
assert drain.ravel()[cells[-1]]
print('east start reaches drain naturally in', len(cells), 'cells')

# 2) A start in the west valley never reaches it naturally ...
s_west = 20 * n + 30
cells, why = h.trace_path(s_west, rcv, drain)
assert why == "edge", why
# ... but a route over the ridge does
routes = h.route_to_targets(zf, valid, drain, [s_west, s_east], cellsize=10.0)
assert set(routes) == {s_west, s_east}
rw = routes[s_west]
assert rw[0] == s_west and drain.ravel()[rw[-1]]
assert not drain.ravel()[rw[:-1]].any()            # stops at the first drain cell
steps = np.diff(np.array([divmod(i, n) for i in rw]), axis=0)
assert np.abs(steps).max() == 1                     # 8-connected, no jumps
cols = [i % n for i in rw]
assert max(cols) >= 99 and min(cols) <= 30
print('west route', len(rw), 'cells, from', divmod(rw[0], n), 'to', divmod(rw[-1], n))

# The route should detour to cross the ridge where it is lowest: a saddle at row 50
dem2 = dem.copy()
dem2 -= 4.8 * np.exp(-((x - 60) / 6.0) ** 2) * np.exp(-((y - 50) / 5.0) ** 2)
zf2 = h.fill_depressions(dem2, valid)
r2 = h.route_to_targets(zf2, valid, drain, [s_west], cellsize=10.0)[s_west]
ridge_row = [divmod(i, n)[0] for i in r2 if divmod(i, n)[1] == 60][0]
print('ridge crossed at row', ridge_row)
assert 45 <= ridge_row <= 55

# Climb / cut stats
z = [zf.ravel()[i] for i in rw]
d = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(np.array([divmod(i, n) for i in rw]), axis=0).T) * 10)])
climb, cut = h.path_profile_stats(z, d)
print('climb %.2f m, max cut %.2f m' % (climb, cut))
assert climb > 1.0 and cut > 1.0
assert h.path_profile_stats([5.0, 4.0, 3.0], [0, 1, 2]) == (0.0, 0.0)

# No drain cells -> no routes; unreachable (invalid) start omitted
assert h.route_to_targets(zf, valid, np.zeros_like(valid), [s_west]) == {}
v2 = valid.copy(); v2[:, 60] = False                   # wall of nodata
r3 = h.route_to_targets(zf, v2, drain, [s_west, s_east], cellsize=10.0)
assert s_west not in r3 and s_east in r3
print('OK')
