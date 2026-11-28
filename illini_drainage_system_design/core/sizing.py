"""Pipe sizing, after Routine K (Network Pipe Sizing) of the Illini Drainage Tools,
with the Strahler ordering of Routine G (Tile Network Ordering).

Ported from BenefitsAlgorithm (benefits_algorithm.py) and the calculate_order
rule of NetworkFlowOrderingAlgorithm (network_flow_ordering_algorithm.py) in
the Illini Drainage Tools QGIS plugin (A. Falasy and R. Cooke, University of
Illinois Urbana-Champaign, GPL v3). The arithmetic is kept exactly as in the
original, including its behaviour in the cases noted below, so results match
the Illini tools, except for three deliberate changes (below).

Routine K works in US units: lengths and spacing in feet, drainage intensity
and coefficient in inches per day, flow in cubic feet per second, area in
acres and pipe diameters in inches.

Changes from the original Routines G and K:
- One drainage coefficient for every line (default 0.375 inch/day), in place
  of Routine K's intensity "by system" (x1.25 above order 1) or per-order
  list; the Tile Size Calculator plugin sets other coefficients afterwards.
- Single-wall pipe uses Manning's n by size, as the original roughness table
  intends: 0.015 up to 8 in, 0.017 over 8 in up to 12 in, 0.020 over 12 in.
  (The original always used 0.015, because it looked the roughness up for a
  1 in pipe.) Smooth wall is 0.011 and clay or concrete 0.013, as before.
- Drain spacing is rounded to the nearest whole foot instead of truncated.
- Strahler order uses the standard rule at every junction: the highest
  inflowing order, plus one if two or more inflows share it. (Routine G
  differed for three inflows of different orders, and for three equal
  inflows plus a higher fourth.)

Kept from the original: pi is taken as 3.142, and the nominal size is the
smallest listed size strictly larger than the computed diameter.
"""
import math
DEFAULT_COEFF = 0.375                                     # inch/day
AVAILABLE_NOMINAL_SIZES = [4, 5, 6, 8, 10, 12, 15, 18, 21, 24, 30, 36, 42]
MATERIALS = ["Single Wall", "Smooth Wall", "Clay or Concrete"]


def strahler_rule(orders):
    """Strahler order of a segment from the orders of the segments flowing into it:
    1 with no inflow, otherwise the highest inflowing order, plus one if two or
    more inflows share it."""
    if not orders:
        return 1
    top = max(orders)
    return top + 1 if orders.count(top) >= 2 else top


def strahler_orders(sources):
    """Orders for segments 0..n-1 given, for each, the list of segments flowing
    into it (in input order). Iterative, so long networks are fine."""
    n = len(sources)
    order = [None] * n
    for start in range(n):
        if order[start] is not None:
            continue
        stack = [(start, False)]
        while stack:
            k, ready = stack.pop()
            if order[k] is not None:
                continue
            if ready:
                order[k] = strahler_rule([order[j] for j in sources[k]])
                continue
            stack.append((k, True))
            for j in sources[k]:
                if order[j] is None:
                    stack.append((j, False))
    return order


def get_flow(length_ft, drain_space_ft, dcoeff):
    """Flow (cfs) from the tile length upstream, spacing and coefficient (in/day)."""
    return length_ft * drain_space_ft * dcoeff / 12 / 24 / 60 / 60


def get_area(length_ft, drain_space_ft):
    """Drained area (acres)."""
    return length_ft * drain_space_ft / 43560


def roughness(ptype, psize):
    """Manning's n for a pipe material and inside diameter (inches)."""
    if ptype == 0:                          # single wall (corrugated)
        if psize <= 8:
            return 0.015
        if psize <= 12:
            return 0.017
        return 0.020
    if ptype == 1:                          # smooth wall
        return 0.011
    if ptype == 2:                          # clay or concrete
        return 0.013
    raise ValueError("Unexpected ptype value {}".format(ptype))


def formula(flow, nn, slp):
    """Full-pipe Manning diameter (ft)."""
    return ((flow * nn * 4 ** (5 / 3)) / (1.49 * 3.142 * slp ** 0.5)) ** (3 / 8)


def get_pipe_size(flow, slope, ptype):
    """Required inside diameter (inches).

    Single wall: the roughness depends on the diameter, so start with the
    smallest-pipe value and step up the table until the diameter falls in the
    range the roughness was taken for. The diameter grows with n, so this
    settles within three steps.
    """
    if ptype == 0:
        for limit, nn in ((8, 0.015), (12, 0.017), (float("inf"), 0.020)):
            psize = formula(flow, nn, slope) * 12
            if psize <= limit:
                return psize
        return psize
    return formula(flow, roughness(ptype, 1), slope) * 12


def get_nominal_size(psize):
    for n_size in AVAILABLE_NOMINAL_SIZES:
        if n_size > psize:
            return n_size
    return AVAILABLE_NOMINAL_SIZES[-1]


def size_segment(flow_length_ft, slope, spacing_ft, material, dcoeff):
    """Routine K for one segment with drainage coefficient dcoeff (inch/day).
    Returns a dict of the Routine K fields."""
    drain_space = int(math.floor(spacing_ft + 0.5))        # nearest whole foot
    flow = get_flow(flow_length_ft, drain_space, dcoeff)
    area = get_area(flow_length_ft, drain_space)
    psize = get_pipe_size(flow, slope, material) if slope > 0 else float("inf")
    return {"D_COEFF": dcoeff, "D_AREA": area, "CUM_SEG_FLOW": flow,
            "ACTUAL_SIZE": psize, "NOMINAL": get_nominal_size(psize),
            "drain_space": drain_space}


HELD_FLAG = "sized up to {} in to match the pipe above"


def hold_sizes(sources, nominals):
    """No pipe smaller than a pipe draining into it.

    sources[i]: the lines that flow into line i; nominals[i]: its nominal size
    (None if not sized). Each line's size is raised to the largest size above it,
    so a pipe never gets smaller downstream (a short steep stretch near the
    outlet would otherwise size smaller than the flatter pipe above it).
    Returns the new sizes, as a list.
    """
    n = len(nominals)
    held = [None] * n
    state = [0] * n                     # 0 new, 1 on the stack, 2 done
    for i0 in range(n):
        if state[i0]:
            continue
        stack = [(i0, False)]
        while stack:
            i, done = stack.pop()
            if done:
                vals = [nominals[i]] + [held[j] for j in sources[i] if state[j] == 2]
                vals = [v for v in vals if v is not None]
                held[i] = max(vals) if vals else None
                state[i] = 2
                continue
            if state[i]:
                continue                # a loop: leave it as it is
            state[i] = 1
            stack.append((i, True))
            for j in sources[i]:
                if state[j] == 0:
                    stack.append((j, False))
    return [h if h is not None else v for h, v in zip(held, nominals)]
