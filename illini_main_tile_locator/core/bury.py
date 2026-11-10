"""Tile burying, after Routine J (Tile Burying System) of the Illini Drainage Tools.

Port of BuryAlgorithm.bury_pass from the Illini Drainage Tools QGIS plugin
(A. Falasy and R. Cooke, University of Illinois Urbana-Champaign, GPL v3),
adapted to run on a network whose flow direction is already known.

Segments are buried in BURY_ORDER, from the outlet upstream. For each segment
the tile elevation at its downstream end is already known: the junction it
shares with a segment buried earlier or, for the outlet segment, the mid target
depth below the ground plus the offset. The upstream end aims for the mid
target depth; the grade from the downstream tile is held between the minimum
and maximum slope (or at the constant slope); and the result is kept between
the absolute upper and lower depths below the ground at that end.

Low ground: a segment that cannot keep the minimum grade without coming
shallower than the absolute upper depth needs a deeper tile at its downstream
junction. With "lower the outlet", that requirement is passed downstream
junction by junction to the outlet, which may go down to the lower tile depth,
and the network is buried again until nothing changes.

All values are in metres and slopes are fractions (m/m).
"""
from dataclasses import dataclass

HELD_NOTES = ("Held at absolute upper depth", "Held at absolute lower depth",
              "Deepened for low ground upstream")


@dataclass
class BuryParams:
    upper_depth: float = 3.25 * 0.3048      # target band, upper
    lower_depth: float = 4.25 * 0.3048      # target band, lower
    abs_upper: float = 3.00 * 0.3048        # never shallower than this
    abs_lower: float = 6.00 * 0.3048        # never deeper than this
    max_slope: float = 0.05
    offset: float = 0.0                     # + lowers the outlet
    use_constant: bool = False
    constant_slope: float = 0.005
    lower_outlet: bool = False


@dataclass
class Segment:
    down_key: object
    up_key: object
    down_ground: float
    up_ground: float
    length: float
    min_slope: float
    outlet: bool = False                     # True for the segment at the outlet


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _pct(v):
    return "{0:.2f}%".format(v * 100.0)


def bury_pass(rows, caps, bp):
    """Bury the whole network once (rows in BURY_ORDER).

    Returns (results, needs, floors, flagged, outlet_depth); each result is
    (down_tile, up_tile, in_slope, out_slope, notes).
    """
    mid_depth = (bp.upper_depth + bp.lower_depth) / 2.0
    visited = {}      # junction -> tile elevation there
    floors = {}       # junction -> lowest tile elevation the network downstream can still drain
    needs = {}        # junction -> tile elevation needed there by the segment upstream of it
    results = []
    flagged = 0
    outlet_depth = None
    for i, r in enumerate(rows):
        min_slope = r.min_slope
        if r.length is None or r.length <= 0:
            results.append((None, None, None, None, ["Missing elevation or length"]))
            flagged += 1
            continue
        notes = []
        if r.down_key in visited:
            down_tile = visited[r.down_key]
        else:
            if not r.outlet:
                notes.append("Not connected to a buried segment; started at target depth")
            down_tile = r.down_ground - mid_depth - bp.offset
            # lowest the outlet may go: the lower tile depth (or the offset, if deeper)
            floors[r.down_key] = min(down_tile, r.down_ground - bp.lower_depth)
            if r.outlet:
                if r.down_key in caps and caps[r.down_key] < down_tile:
                    down_tile = max(caps[r.down_key], floors[r.down_key])
                    notes.append("Outlet lowered for low ground upstream")
                outlet_depth = r.down_ground - down_tile

        loop = r.up_key in visited
        if loop:
            # both ends already buried (the segment closes a loop): keep both
            up_tile = visited[r.up_key]
            in_slope = _clamp((up_tile - down_tile) / r.length, min_slope, bp.max_slope)
        else:
            target = r.up_ground - mid_depth
            target_slope = (target - down_tile) / r.length
            if bp.use_constant and not r.outlet:
                in_slope = bp.constant_slope
            else:
                in_slope = _clamp(target_slope, min_slope, bp.max_slope)
            up_tile = down_tile + r.length * in_slope
            down_floor = floors.get(r.down_key, down_tile)
            floors[r.up_key] = max(down_floor + r.length * min_slope, r.up_ground - bp.abs_lower)
            if r.up_key in caps and caps[r.up_key] < up_tile:
                up_tile = caps[r.up_key]
                notes.append(HELD_NOTES[2])
            # cover limits at the upstream end
            if up_tile > r.up_ground - bp.abs_upper:
                up_tile = r.up_ground - bp.abs_upper
                notes.append(HELD_NOTES[0])
            elif up_tile < r.up_ground - bp.abs_lower:
                up_tile = r.up_ground - bp.abs_lower
                notes.append(HELD_NOTES[1])
            visited[r.up_key] = up_tile
        visited.setdefault(r.down_key, down_tile)

        actual = (up_tile - down_tile) / r.length
        if actual < min_slope - 1e-9:
            if actual <= 0:
                notes.append("Reverse or zero grade ({0}): tile cannot drain".format(_pct(actual)))
            else:
                notes.append("Grade {0} below minimum".format(_pct(actual)))
            if not loop:
                need = up_tile - r.length * min_slope
                needs[r.down_key] = min(need, needs.get(r.down_key, need))
        elif actual > bp.max_slope + 1e-9:
            notes.append("Grade {0} above maximum".format(_pct(actual)))
        out_slope = _clamp(actual, min_slope, bp.max_slope)
        if any(n not in HELD_NOTES and not n.startswith("Outlet lowered") for n in notes):
            flagged += 1
        results.append((down_tile, up_tile, in_slope, out_slope, notes))
    return results, needs, floors, flagged, outlet_depth


def bury_network(rows, bp, cancel=None):
    """Bury in BURY_ORDER, iterating the outlet lowering when asked.

    Returns (results, flagged, outlet_depth, start_depth, tiles) where tiles maps
    each junction key to its tile elevation.
    """
    caps = {}
    results, needs, floors, flagged, outlet_depth = bury_pass(rows, caps, bp)
    start_depth = outlet_depth
    if bp.lower_outlet:
        for _ in range(4 * len(rows) + 10):
            changed = False
            for junction, need in needs.items():
                # never deeper than the segments downstream can carry at the minimum grade
                need = max(need, floors.get(junction, need))
                if need < caps.get(junction, float("inf")) - 1e-6:
                    caps[junction] = need
                    changed = True
            if not changed or (cancel is not None and cancel()):
                break
            results, needs, floors, flagged, outlet_depth = bury_pass(rows, caps, bp)
    tiles = {}
    for r, res in zip(rows, results):
        if res[0] is None:
            continue
        tiles.setdefault(r.down_key, res[0])
        tiles[r.up_key] = res[1]
    return results, flagged, outlet_depth, start_depth, tiles
