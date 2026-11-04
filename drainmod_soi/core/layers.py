"""Turn SSURGO horizons into at most N DRAINMOD layers."""
import math

from .texture import classify, class_from_texdesc, BEDROCK_WORDS


class Layer:
    def __init__(self, top, bottom, sand, silt, clay, ksat_cmhr=None,
                 db=None, om=None, names=None, note="", w33=None, w1500=None):
        self.top, self.bottom = float(top), float(bottom)
        self.sand, self.silt, self.clay = sand, silt, clay
        self.ksat_cmhr, self.db, self.om = ksat_cmhr, db, om
        # volumetric water content (cm3/cm3) at 33 and 1500 kPa
        self.w33, self.w1500 = w33, w1500
        self.names = list(names or [])
        self.note = note
        self.texture = classify(sand, silt, clay)

    @property
    def thickness(self):
        return self.bottom - self.top

    @property
    def label(self):
        return "+".join(n for n in self.names if n) or "-"

    def __repr__(self):
        return ("Layer(%s %g-%g cm, %s, S/Si/C=%.0f/%.0f/%.0f, K=%s)"
                % (self.label, self.top, self.bottom, self.texture,
                   self.sand, self.silt, self.clay,
                   "%.3f" % self.ksat_cmhr if self.ksat_cmhr is not None else "NA"))


def _frac(pct):
    """SSURGO volumetric water (percent) -> cm3/cm3."""
    return pct / 100.0 if pct is not None and pct > 0 else None


def _is_bedrock(h):
    name = (h.get("hzname") or "").strip().upper()
    tex = (h.get("texdesc") or "").lower()
    # strip leading lithologic discontinuity numbers, e.g. '2Cr'
    name = name.lstrip("0123456789")
    return (name.startswith("R") or name.startswith("CR")
            or any(w in tex for w in BEDROCK_WORDS))


def _class_fractions(cls):
    """Representative sand/silt/clay for a texture class (used only when a
    horizon gives a texture name but no particle-size data)."""
    centroids = {
        "sand": (92, 5, 3), "loamy sand": (82, 12, 6),
        "sandy loam": (65, 25, 10), "loam": (40, 40, 20),
        "silt loam": (20, 65, 15), "silt": (7, 88, 5),
        "sandy clay loam": (60, 13, 27), "clay loam": (32, 34, 34),
        "silty clay loam": (10, 56, 34), "sandy clay": (52, 6, 42),
        "silty clay": (6, 47, 47), "clay": (22, 20, 58),
    }
    return centroids[cls]


def clean_horizons(horizons, warnings):
    """Convert raw SSURGO horizon dicts to Layer objects.

    * horizons are sorted and gaps/overlaps closed;
    * bedrock / cemented horizons and everything below them are dropped;
    * horizons without particle-size data use their texture name, or else
      borrow properties from the nearest mineral horizon (with a warning).
    """
    hz = sorted((h for h in horizons
                 if h.get("top") is not None and h.get("bottom") is not None
                 and h["bottom"] > h["top"]), key=lambda h: h["top"])
    if not hz:
        raise ValueError("Component has no horizon data in SSURGO.")

    kept = []
    for h in hz:
        if _is_bedrock(h) and kept:
            warnings.append("Profile truncated at %g cm: horizon %s (%s) is "
                            "bedrock/cemented." % (h["top"], h["hzname"] or "?",
                                                   h["texdesc"] or "no texture"))
            break
        kept.append(h)

    layers, pending = [], []   # pending = horizons we could not parameterise
    for h in kept:
        s, si, c = h.get("sand"), h.get("silt"), h.get("clay")
        note = ""
        if None in (s, si, c) or (s or 0) + (si or 0) + (c or 0) <= 0:
            cls = class_from_texdesc(h.get("texdesc"))
            if cls:
                s, si, c = _class_fractions(cls)
                note = "fractions from texture name"
            else:
                pending.append(h)
                continue
        elif si is None:
            si = max(0.0, 100 - s - c)
        L = Layer(h["top"], h["bottom"], s, si, c, h.get("ksat_cmhr"),
                  h.get("db"), h.get("om"), [h.get("hzname")], note,
                  _frac(h.get("w33")), _frac(h.get("w1500")))
        layers.append(L)

    if not layers:
        raise ValueError("No horizon in this component has mineral texture "
                         "data (organic or bedrock component?).")

    # Fold unparameterisable horizons into the nearest mineral neighbour.
    for h in pending:
        target = min(layers, key=lambda L: min(abs(L.top - h["bottom"]),
                                                abs(L.bottom - h["top"])))
        warnings.append("Horizon %s (%g-%g cm, '%s') has no mineral texture "
                        "data; given the properties of horizon %s."
                        % (h["hzname"] or "?", h["top"], h["bottom"],
                           h["texdesc"] or "none", target.label))
        if h["top"] < target.top:
            target.names.insert(0, h.get("hzname"))
        else:
            target.names.append(h.get("hzname"))
        target.top = min(target.top, h["top"])
        target.bottom = max(target.bottom, h["bottom"])

    layers.sort(key=lambda L: L.top)
    # close gaps / overlaps; profile starts at the surface
    layers[0].top = 0.0
    for a, b in zip(layers, layers[1:]):
        if abs(b.top - a.bottom) > 1e-6:
            b.top = a.bottom
    return [L for L in layers if L.thickness > 0]


def merge_pair(a, b, ksat_mean="arithmetic"):
    ta, tb = a.thickness, b.thickness
    t = ta + tb

    def wavg(x, y):
        if x is None and y is None:
            return None
        if x is None:
            return y
        if y is None:
            return x
        return (x * ta + y * tb) / t

    if a.ksat_cmhr is None or b.ksat_cmhr is None:
        k = a.ksat_cmhr if b.ksat_cmhr is None else b.ksat_cmhr
    elif ksat_mean == "harmonic":
        k = t / (ta / a.ksat_cmhr + tb / b.ksat_cmhr) if a.ksat_cmhr > 0 and b.ksat_cmhr > 0 else 0.0
    else:
        k = wavg(a.ksat_cmhr, b.ksat_cmhr)

    return Layer(a.top, b.bottom, wavg(a.sand, b.sand), wavg(a.silt, b.silt),
                 wavg(a.clay, b.clay), k, wavg(a.db, b.db), wavg(a.om, b.om),
                 a.names + b.names, "merged",
                 wavg(a.w33, b.w33), wavg(a.w1500, b.w1500))


def dissimilarity(a, b):
    """0 = identical.  One unit ~ 10 percentage points of texture difference
    or a factor of ~3 in Ksat; +0.5 if the texture classes differ."""
    dtex = math.sqrt((a.sand - b.sand) ** 2 + (a.silt - b.silt) ** 2
                     + (a.clay - b.clay) ** 2) / 10.0
    if a.ksat_cmhr and b.ksat_cmhr and a.ksat_cmhr > 0 and b.ksat_cmhr > 0:
        dk = abs(math.log10(a.ksat_cmhr / b.ksat_cmhr)) / 0.5
    else:
        dk = 0.0
    return dtex + dk + (0.5 if a.texture != b.texture else 0.0)


def merge_score(a, b):
    """Cost of merging two adjacent layers: dissimilarity scaled so that a
    thin layer is absorbed before two thick, moderately different ones."""
    thin = min(a.thickness, b.thickness)
    return dissimilarity(a, b) * math.sqrt(thin / 30.0)


def reduce_layers(layers, max_layers=5, merge_similar=True,
                  similar_threshold=0.6, ksat_mean="arithmetic"):
    """Merge adjacent layers until there are at most `max_layers`.

    If merge_similar is True, adjacent layers with dissimilarity below
    `similar_threshold` (same texture class, similar Ksat) are merged first
    even if the layer count is already within the limit."""
    layers = list(layers)
    if merge_similar:
        changed = True
        while changed and len(layers) > 1:
            changed = False
            best = min(range(len(layers) - 1),
                       key=lambda i: dissimilarity(layers[i], layers[i + 1]))
            if dissimilarity(layers[best], layers[best + 1]) < similar_threshold:
                layers[best:best + 2] = [merge_pair(layers[best], layers[best + 1], ksat_mean)]
                changed = True
    while len(layers) > max_layers:
        best = min(range(len(layers) - 1),
                   key=lambda i: merge_score(layers[i], layers[i + 1]))
        layers[best:best + 2] = [merge_pair(layers[best], layers[best + 1], ksat_mean)]
    return layers
