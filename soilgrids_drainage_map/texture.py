"""USDA texture classes and Rosetta class-average Ksat.

classify() and the Rosetta class-average table come from the DRAINMOD Soil
File plugin in this repository. Class averages are from Schaap, Leij and
van Genuchten (2001), J. Hydrology 251:163-176, and are the fallback when the
Rosetta 3 web service can't be reached.
"""

# Rosetta class averages: theta_r, theta_s, log10(alpha 1/cm), log10(n), log10(Ks cm/day)
_ROSETTA_RAW = {
    "clay":            (0.098, 0.459, -1.825, 0.098, 1.169),
    "clay loam":       (0.079, 0.442, -1.801, 0.151, 0.913),
    "loam":            (0.061, 0.399, -1.954, 0.168, 1.081),
    "loamy sand":      (0.049, 0.390, -1.459, 0.242, 2.022),
    "sand":            (0.053, 0.375, -1.453, 0.502, 2.808),
    "sandy clay":      (0.117, 0.385, -1.476, 0.082, 1.055),
    "sandy clay loam": (0.063, 0.384, -1.676, 0.124, 1.120),
    "sandy loam":      (0.039, 0.387, -1.574, 0.161, 1.583),
    "silt":            (0.050, 0.489, -2.182, 0.225, 1.641),
    "silt loam":       (0.065, 0.439, -2.296, 0.221, 1.261),
    "silty clay":      (0.111, 0.481, -1.790, 0.121, 0.983),
    "silty clay loam": (0.090, 0.482, -2.076, 0.182, 1.065),
}


def class_ksat_m_day(texture_class):
    """Rosetta class-average Ksat (m/day) for a USDA texture class."""
    return 10 ** _ROSETTA_RAW[texture_class][4] / 100.0


def classify(sand, silt, clay):
    """USDA texture class from sand/silt/clay percentages (normalised to 100)."""
    total = sand + silt + clay
    if total <= 0:
        raise ValueError("sand + silt + clay must be > 0")
    sand, silt, clay = (100.0 * v / total for v in (sand, silt, clay))

    if silt + 1.5 * clay < 15:
        return "sand"
    if silt + 2 * clay < 30:
        return "loamy sand"
    if (7 <= clay < 20 and sand > 52) or (clay < 7 and silt < 50):
        return "sandy loam"
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        return "loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if (silt >= 50 and 12 <= clay < 27) or (50 <= silt < 80 and clay < 12):
        return "silt loam"
    if 20 <= clay < 35 and silt < 28 and sand > 45:
        return "sandy clay loam"
    if 27 <= clay < 40 and 20 < sand <= 45:
        return "clay loam"
    if 27 <= clay < 40 and sand <= 20:
        return "silty clay loam"
    if clay >= 35 and sand > 45:
        return "sandy clay"
    if clay >= 40 and silt >= 40:
        return "silty clay"
    return "clay"
