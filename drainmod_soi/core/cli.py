"""Command-line use outside QGIS:

    python -m drainmod_soi.core.cli --lat 40.10 --lon -88.24 \
        --max-root 45 --avg-root 30 -o drummer.soi
"""
import argparse
import sys

from .pipeline import Options, build_from_point, METHODS


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build a DRAINMOD .soi file from SSURGO")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--max-root", type=float, required=True, help="cm")
    ap.add_argument("--avg-root", type=float, required=True, help="cm")
    ap.add_argument("--method", choices=sorted(METHODS), default="rosetta")
    ap.add_argument("--ksat", choices=["ssurgo", "texture"], default="ssurgo")
    ap.add_argument("--max-layers", type=int, default=5, choices=range(1, 6))
    ap.add_argument("--no-merge-similar", action="store_true")
    ap.add_argument("--harmonic-ksat", action="store_true")
    ap.add_argument("--extend-to", type=float, help="extend bottom layer to this depth (cm)")
    ap.add_argument("--cokey", help="use this component instead of the dominant one")
    ap.add_argument("-o", "--output", help=".soi path (default: print)")
    a = ap.parse_args(argv)

    opt = Options(a.max_root, a.avg_root, a.method, a.ksat, a.max_layers,
                  not a.no_merge_similar,
                  "harmonic" if a.harmonic_ksat else "arithmetic", a.extend_to)
    mu, comp, res = build_from_point(a.lon, a.lat, opt, a.cokey)
    print("Map unit %s: %s (%s)" % (mu["mukey"], mu["muname"], mu["areasymbol"]),
          file=sys.stderr)
    print("Component %s: %s %s%%" % (comp["cokey"], comp["compname"],
                                     comp["comppct_r"]), file=sys.stderr)
    print(res.summary(), file=sys.stderr)
    for w in res.warnings:
        print("WARNING: " + w, file=sys.stderr)
    if a.output:
        with open(a.output, "w", newline="\n") as f:
            f.write(res.text)
        print("Wrote " + a.output, file=sys.stderr)
    else:
        sys.stdout.write(res.text)


if __name__ == "__main__":
    main()
