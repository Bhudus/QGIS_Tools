"""High-level: SSURGO horizons -> DRAINMOD .soi text."""
from .hydraulics import DEFAULT_SUCTIONS, class_params, rosetta3_params
from .layers import clean_horizons, reduce_layers
from .soifile import build_soi_text, root_layer
from .texture import NON_MINERAL_WORDS

METHODS = {
    "rosetta": "Rosetta class averages (Schaap et al. 2001)",
    "carsel": "Carsel & Parrish (1988) class averages",
    "rosetta3": "Rosetta 3 from sand/silt/clay/Db (USDA web service)",
    "rosetta3w": "Rosetta 3 from sand/silt/clay/Db + water at 33 & 1500 kPa (web service)",
}


class Options:
    def __init__(self, max_root=45, avg_root=30, method="rosetta",
                 ksat_source="ssurgo", max_layers=5, merge_similar=True,
                 ksat_mean="arithmetic", extend_to=None, suctions=None):
        self.max_root, self.avg_root = max_root, avg_root
        self.method, self.ksat_source = method, ksat_source
        self.max_layers, self.merge_similar = max_layers, merge_similar
        self.ksat_mean, self.extend_to = ksat_mean, extend_to
        self.suctions = list(suctions or DEFAULT_SUCTIONS)


class Result:
    def __init__(self, name, layers, params, text, warnings, n_horizons):
        self.name, self.layers, self.params = name, layers, params
        self.text, self.warnings = text, warnings
        self.n_horizons = n_horizons

    def summary(self):
        out = ["%d SSURGO horizon(s) -> %d DRAINMOD layer(s)"
               % (self.n_horizons, len(self.layers))]
        for i, (L, p) in enumerate(zip(self.layers, self.params), 1):
            out.append(
                "Layer %d: %5.0f-%5.0f cm  %-16s S/Si/C %3.0f/%3.0f/%3.0f  "
                "Ksat %.3f cm/h  [%s]\n         thr=%.3f ths=%.3f alpha=%.4f "
                "n=%.3f  (%s)"
                % (i, L.top, L.bottom, L.texture, L.sand, L.silt, L.clay,
                   L.ksat_cmhr, L.label, p.theta_r, p.theta_s, p.alpha, p.n,
                   p.source))
        return "\n".join(out)


def soil_name(compname, horizons):
    for h in sorted(horizons, key=lambda h: h.get("top") or 0):
        t = (h.get("texdesc") or "").strip()
        if t and not any(w in t.lower() for w in NON_MINERAL_WORDS):
            return ("%s %s" % (compname, t.lower())).strip()
    return compname


def build(compname, horizons, opt):
    warnings = []
    layers = clean_horizons(horizons, warnings)
    n_hz = len(layers)
    layers = reduce_layers(layers, opt.max_layers, opt.merge_similar,
                           ksat_mean=opt.ksat_mean)

    if opt.extend_to and opt.extend_to > layers[-1].bottom:
        warnings.append("Bottom layer extended from %g to %g cm."
                        % (layers[-1].bottom, opt.extend_to))
        layers[-1].bottom = float(opt.extend_to)

    if opt.method in ("rosetta3", "rosetta3w"):
        try:
            params, notes = rosetta3_params(layers, opt.method == "rosetta3w")
            warnings.extend(notes)
        except Exception as e:  # noqa: BLE001 - fall back on any failure
            warnings.append("Rosetta 3 service failed (%s); used Rosetta "
                            "class averages instead." % e)
            params = [class_params(L.texture, "rosetta") for L in layers]
    else:
        params = [class_params(L.texture, opt.method) for L in layers]

    for i, (L, p) in enumerate(zip(layers, params), 1):
        if opt.ksat_source == "texture" or L.ksat_cmhr is None:
            if opt.ksat_source != "texture":
                warnings.append("Layer %d has no SSURGO Ksat; used the "
                                "texture-based value." % i)
            L.ksat_cmhr = p.ks_cmhr

    if opt.max_root > layers[-1].bottom:
        warnings.append("Maximum root depth is below the bottom of the profile.")

    name = soil_name(compname, horizons)
    text = build_soi_text(name, layers, params, opt.suctions,
                          opt.max_root, opt.avg_root)
    return Result(name, layers, params, text, warnings, n_hz)


def build_from_point(lon, lat, opt, cokey=None):
    """Convenience for scripts: query SDA at a point and build the file.
    Returns (mapunit, component, Result)."""
    from . import sda
    mu = sda.mapunit_at(lon, lat)
    if not mu:
        raise LookupError("No SSURGO map unit at %.5f, %.5f" % (lat, lon))
    comps = sda.components(mu["mukey"])
    if not comps:
        raise LookupError("Map unit %s has no components" % mu["mukey"])
    comp = (next((c for c in comps if c["cokey"] == cokey), None) if cokey
            else None) or sda.default_component(comps)
    hz = sda.horizons(comp["cokey"])
    return mu, comp, build(comp["compname"], hz, opt)


__all__ = ["Options", "Result", "build", "build_from_point", "METHODS",
           "root_layer"]
