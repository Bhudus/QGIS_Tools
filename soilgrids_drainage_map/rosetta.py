"""Ksat from Rosetta 3 (USDA-ARS web service), with class averages as fallback."""
import math

from .texture import class_ksat_m_day, classify

ROSETTA3_URL = "https://www.handbook60.org/api/v1/rosetta/3"


def ksat_for_layers(rows, post_json):
    """Saturated hydraulic conductivity (m/day) for each layer.

    rows: list of (sand, silt, clay, bulk_density) in %, %, %, g/cm3.
    post_json: function(url, payload) -> parsed JSON, so the network call can
        go through QGIS (proxy settings) or be replaced in tests.
    Returns (Ksat, low, high, source text), the first three being lists in
    m/day. Uses the Rosetta 3 SSC+BD model in one request. Low and high are
    the mean of log10(Ksat) minus and plus one standard deviation of Rosetta's
    bootstrap estimate, when the service returns it (otherwise None). If the
    service fails, uses Rosetta class averages by texture, with no range.
    """
    if not rows:
        return [], [], [], "none"
    payload = {"soildata": [[round(s, 2), round(si, 2), round(c, 2), round(bd, 3)]
                            for s, si, c, bd in rows]}
    try:
        resp = post_json(ROSETTA3_URL, payload)
        vg = resp.get("van_genuchten_params") if isinstance(resp, dict) else None
        if not vg or len(vg) != len(rows):
            raise RuntimeError("unexpected Rosetta 3 response")
        sd = resp.get("stdev")
        if not isinstance(sd, list) or len(sd) != len(rows):
            sd = [None] * len(rows)
        out, low, high = [], [], []
        for p, s in zip(vg, sd):
            log_ks = float(p[4])  # log10(Ks, cm/day)
            if math.isnan(log_ks):
                raise RuntimeError("Rosetta 3 returned NaN")
            out.append(10 ** log_ks / 100.0)
            try:
                s_ks = float(s[4])  # standard deviation of log10(Ks)
                ok = not math.isnan(s_ks) and s_ks >= 0
            except (TypeError, ValueError, IndexError):
                ok = False
            low.append(10 ** (log_ks - s_ks) / 100.0 if ok else None)
            high.append(10 ** (log_ks + s_ks) / 100.0 if ok else None)
        return out, low, high, "Rosetta 3 (sand, silt, clay, bulk density)"
    except Exception as e:  # network, service or format problem
        out = [class_ksat_m_day(classify(s, si, c)) for s, si, c, _ in rows]
        none = [None] * len(rows)
        return out, none, list(none), f"Rosetta class averages (Rosetta 3 unavailable: {str(e)[:120]})"
