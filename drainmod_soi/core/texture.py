"""USDA soil texture classification and van Genuchten parameter tables.

Two class-average parameter sets are provided:

* ROSETTA  - Schaap, Leij & van Genuchten (2001), J. Hydrology 251:163-176,
             Rosetta class-average (H1) parameters.
* CARSEL   - Carsel & Parrish (1988), Water Resour. Res. 24:755-769.

Each entry: (theta_r, theta_s, alpha [1/cm], n [-], Ks [cm/hr])
"""

TEXTURE_CLASSES = [
    "sand", "loamy sand", "sandy loam", "loam", "silt loam", "silt",
    "sandy clay loam", "clay loam", "silty clay loam",
    "sandy clay", "silty clay", "clay",
]

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

ROSETTA = {
    k: (tr, ts, 10 ** la, 10 ** ln, 10 ** lk / 24.0)
    for k, (tr, ts, la, ln, lk) in _ROSETTA_RAW.items()
}

# Carsel & Parrish (1988): theta_r, theta_s, alpha (1/cm), n, Ks (cm/day)
_CARSEL_RAW = {
    "sand":            (0.045, 0.43, 0.145, 2.68, 712.8),
    "loamy sand":      (0.057, 0.41, 0.124, 2.28, 350.2),
    "sandy loam":      (0.065, 0.41, 0.075, 1.89, 106.1),
    "loam":            (0.078, 0.43, 0.036, 1.56, 24.96),
    "silt":            (0.034, 0.46, 0.016, 1.37, 6.00),
    "silt loam":       (0.067, 0.45, 0.020, 1.41, 10.80),
    "sandy clay loam": (0.100, 0.39, 0.059, 1.48, 31.44),
    "clay loam":       (0.095, 0.41, 0.019, 1.31, 6.24),
    "silty clay loam": (0.089, 0.43, 0.010, 1.23, 1.68),
    "sandy clay":      (0.100, 0.38, 0.027, 1.23, 2.88),
    "silty clay":      (0.070, 0.36, 0.005, 1.09, 0.48),
    "clay":            (0.068, 0.38, 0.008, 1.09, 4.80),
}

CARSEL = {k: (tr, ts, a, n, ks / 24.0) for k, (tr, ts, a, n, ks) in _CARSEL_RAW.items()}

PARAM_SETS = {"rosetta": ROSETTA, "carsel": CARSEL}


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


# Words in SSURGO texture descriptions that indicate there is no mineral
# fine-earth texture we can parameterise.
NON_MINERAL_WORDS = ("bedrock", "muck", "peat", "material", "water",
                     "cemented", "indurated", "fragmental", "duripan",
                     "petrocalcic", "ortstein", "variable", "unknown")
BEDROCK_WORDS = ("bedrock", "cemented", "indurated", "duripan",
                 "petrocalcic", "ortstein")


def class_from_texdesc(texdesc):
    """Map an SSURGO texture description (e.g. 'gravelly fine sandy loam')
    to one of the 12 USDA classes, or None if it is not a mineral texture."""
    if not texdesc:
        return None
    t = texdesc.lower()
    if any(w in t for w in NON_MINERAL_WORDS):
        return None
    # SSURGO lists e.g. 'loam, silt loam' - take the first named texture
    t = t.split(",")[0]
    for mod in ("very fine ", "fine ", "coarse ", "very coarse "):
        t = t.replace(mod, "")
    # longest class names first so 'silty clay loam' wins over 'clay loam'
    for cls in sorted(TEXTURE_CLASSES, key=len, reverse=True):
        if t.endswith(cls):
            return cls
    return None
