"""van Genuchten soil water characteristic and parameter providers."""
import math

from . import http
from .texture import PARAM_SETS

DEFAULT_SUCTIONS = [0, 20, 40, 60, 80, 100, 150, 200, 250, 330,
                    500, 1000, 2500, 5000, 10000, 15000]

ROSETTA3_URL = "https://www.handbook60.org/api/v1/rosetta/3"
_ROSETTA3_CACHE = {}


class VGParams:
    __slots__ = ("theta_r", "theta_s", "alpha", "n", "ks_cmhr", "source")

    def __init__(self, theta_r, theta_s, alpha, n, ks_cmhr, source):
        self.theta_r, self.theta_s = theta_r, theta_s
        self.alpha, self.n, self.ks_cmhr = alpha, n, ks_cmhr
        self.source = source

    def theta(self, h_cm):
        """Volumetric water content at suction h (cm, positive)."""
        if h_cm <= 0:
            return self.theta_s
        m = 1.0 - 1.0 / self.n
        se = (1.0 + (self.alpha * h_cm) ** self.n) ** (-m)
        return self.theta_r + (self.theta_s - self.theta_r) * se

    def __repr__(self):
        return ("VG(tr=%.3f ts=%.3f a=%.4f n=%.3f Ks=%.3f cm/h, %s)"
                % (self.theta_r, self.theta_s, self.alpha, self.n,
                   self.ks_cmhr, self.source))


def class_params(texture_class, method="rosetta"):
    table = PARAM_SETS[method]
    tr, ts, a, n, ks = table[texture_class]
    label = "Rosetta class avg" if method == "rosetta" else "Carsel & Parrish"
    return VGParams(tr, ts, a, n, ks, "%s (%s)" % (label, texture_class))


# Rosetta model inputs, richest first.  Each extra column must be present
# for every layer for that model to be used.
_ROSETTA_MODELS = [
    ("SSC+BD+TH33+TH1500", ("db", "w33", "w1500")),
    ("SSC+BD+TH33", ("db", "w33")),
    ("SSC+BD", ("db",)),
    ("SSC", ()),
]


def rosetta3_params(layers, use_water=False):
    """Rosetta 3 (USDA-ARS web service) predictions.

    Inputs per layer: sand, silt, clay (%), bulk density at 1/3 bar (g/cm3)
    and, with use_water=True, the two optional water contents at 33 kPa and
    1500 kPa (cm3/cm3).  If some layer lacks a value, the richest model that
    every layer can supply is used, and a note says so.

    Returns (list of VGParams, list of notes).  Raises on any network or
    response problem so the caller can fall back."""
    wanted = 0 if use_water else 2
    model_idx = wanted
    for i in range(wanted, len(_ROSETTA_MODELS)):
        cols = _ROSETTA_MODELS[i][1]
        if all(getattr(L, c) for L in layers for c in cols):
            model_idx = i
            break
    model, cols = _ROSETTA_MODELS[model_idx]

    notes = []
    if model_idx != wanted:
        missing = sorted({c for L in layers for c in _ROSETTA_MODELS[wanted][1]
                          if not getattr(L, c)})
        names = {"db": "bulk density", "w33": "33 kPa water content",
                 "w1500": "1500 kPa water content"}
        notes.append("Rosetta 3: SSURGO lacks %s for at least one layer; used "
                     "the %s model instead of %s."
                     % (", ".join(names[m] for m in missing), model,
                        _ROSETTA_MODELS[wanted][0]))

    rows = []
    for L in layers:
        row = [round(L.sand, 2), round(L.silt, 2), round(L.clay, 2)]
        for c in cols:
            row.append(round(getattr(L, c), 4))
        rows.append(row)

    key = tuple(tuple(r) for r in rows)
    if key in _ROSETTA3_CACHE:
        return _ROSETTA3_CACHE[key], notes
    resp = http.post_json(ROSETTA3_URL, {"soildata": rows})
    vg = resp.get("van_genuchten_params") if isinstance(resp, dict) else None
    if not vg or len(vg) != len(layers):
        raise RuntimeError("Unexpected Rosetta 3 response: %r" % (resp,))
    out = []
    for p in vg:
        tr, ts, la, ln, lk = (float(x) for x in p[:5])
        if any(math.isnan(v) for v in (tr, ts, la, ln, lk)):
            raise RuntimeError("Rosetta 3 returned NaN parameters")
        out.append(VGParams(tr, ts, 10 ** la, 10 ** ln, 10 ** lk / 24.0,
                            "Rosetta 3 (%s)" % model))
    _ROSETTA3_CACHE[key] = out
    return out, notes
