"""Build the DRAINMOD .soi text.

Column layout follows the sample file exactly (Fortran-style fixed fields):
  line 1 : soil name
  line 2 : I2,I2           number of layers, 0
  line 3 : I2,F9.4,F9.1    number of points, Ksat (cm/hr), layer bottom (cm)
  line 4 : F5.3,F8.1       water content, suction (cm)   [repeated]
  line 5 : 3I4             max root depth, avg root depth, layer of avg depth
Every numeric field carries an explicit decimal point, so the file reads
correctly with either fixed-format or list-directed Fortran input.
"""


def root_layer(layers, avg_root_depth):
    """1-based index of the layer containing the average root depth."""
    for i, L in enumerate(layers, 1):
        if avg_root_depth <= L.bottom:
            return i
    return len(layers)


def build_soi_text(name, layers, params, suctions, max_root, avg_root):
    if not 1 <= len(layers) <= 5:
        raise ValueError("DRAINMOD allows 1-5 layers (got %d)" % len(layers))
    if len(params) != len(layers):
        raise ValueError("one parameter set per layer required")
    if avg_root > max_root:
        raise ValueError("Average root depth cannot exceed maximum root depth")

    lines = [name[:80], "%2d%2d" % (len(layers), 0)]
    for L, p in zip(layers, params):
        lines.append("%2d%9.4f%9.1f" % (len(suctions), L.ksat_cmhr, L.bottom))
        for h in suctions:
            lines.append("%5.3f%8.1f" % (p.theta(h), float(h)))
    lines.append("%4d%4d%4d" % (int(round(max_root)), int(round(avg_root)),
                                root_layer(layers, avg_root)))
    return "\n".join(lines) + "\n"
