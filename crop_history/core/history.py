"""Crop history of fields from a stack of yearly CDL rasters (numpy only)."""
import numpy as np

from .classes import (CORN, CROP_BITS, HAY, NO_DATA_CODES, NONCROP, OTHER_CROP, SMALL_GRAIN,
                      SOY, group_table, letter, name)

_GROUPS = np.array(group_table(), dtype=np.uint8)
_VALID = np.ones(256, dtype=bool)
_VALID[list(NO_DATA_CODES)] = False


def rotation_from_counts(n, corn, soy, sg, hay, crop):
    """Rotation class (see classes.ROTATIONS) from counts of years; arrays allowed."""
    n = np.asarray(n, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        fc, fs, fw, fh, fcr = (np.asarray(v, float) / np.where(n > 0, n, 1)
                               for v in (corn, soy, sg, hay, crop))
    out = np.full(np.broadcast(n, fc).shape, 7, dtype=np.uint8)
    rules = [
        (fcr < 0.5, 8),
        (fc >= 0.75, 2),
        (fs >= 0.75, 3),
        (fh >= 0.5, 6),
        ((fh >= 0.2) & (fc + fs > 0), 5),
        ((fw >= 0.15) & (fc > 0) & (fs > 0) & (fc + fs + fw >= 0.75), 4),
        ((fc + fs >= 0.75) & (fc >= 0.2) & (fs >= 0.2), 1),
    ]
    done = np.zeros(out.shape, dtype=bool)
    for cond, code in rules:
        c = np.broadcast_to(cond, out.shape) & ~done
        out[c] = code
        done |= c
    out[np.broadcast_to(n == 0, out.shape)] = 0
    return out


def cell_rotation(stack):
    """Rotation class of every cell of a (years, rows, cols) CDL stack."""
    g = _GROUPS[stack]
    valid = _VALID[stack]
    n = valid.sum(0)
    cnt = {b: ((g & b) > 0).sum(0) for b in (CORN, SOY, SMALL_GRAIN, HAY)}
    crop = ((g & CROP_BITS) > 0).sum(0)
    return rotation_from_counts(n, cnt[CORN], cnt[SOY], cnt[SMALL_GRAIN], cnt[HAY], crop)


def field_history(stack, mask, years, cell_area, min_pct=1.0):
    """Crop history of one field.

    stack: (years, rows, cols) uint8 CDL codes on a common grid (0 = no data);
    mask: bool (rows, cols), the field; cell_area: area of one cell (output units).
    Returns a dict with per-year rows and a field summary.
    """
    cells = stack[:, mask]                       # (years, n)
    n_cells = int(mask.sum())
    rows = []
    dom_codes = []
    crop_share = []
    dom_share = []
    for k, yr in enumerate(years):
        v = cells[k]
        counts = np.bincount(v, minlength=256)
        valid = counts * _VALID
        tot = int(valid.sum())
        if tot == 0:
            rows.append({"year": int(yr), "crop": None, "code": 0, "pct": None, "area": None,
                         "second": None, "second_pct": None, "crops": "", "valid_pct": 0.0})
            dom_codes.append(0)
            continue
        order = np.argsort(-valid)
        top = [int(c) for c in order if valid[c] > 0]
        pct = {c: 100.0 * valid[c] / tot for c in top}
        c0 = top[0]
        c1 = top[1] if len(top) > 1 else None
        rows.append({
            "year": int(yr), "crop": name(c0), "code": c0, "pct": pct[c0],
            "area": valid[c0] * cell_area,
            "second": name(c1) if c1 is not None else None,
            "second_pct": pct[c1] if c1 is not None else None,
            "crops": "; ".join("{} {:.0f}%".format(name(c), pct[c]) for c in top
                               if pct[c] >= min_pct),
            "valid_pct": 100.0 * tot / max(n_cells, 1)})
        dom_codes.append(c0)
        cropped = sum(valid[c] for c in top if _GROUPS[c] & CROP_BITS)
        crop_share.append(100.0 * cropped / tot)
        dom_share.append(pct[c0])
    dom = np.array(dom_codes, dtype=np.int64)
    gd = _GROUPS[dom]
    valid_years = _VALID[dom]
    n = int(valid_years.sum())
    corn = int(((gd & CORN) > 0).sum())
    soy = int(((gd & SOY) > 0).sum())
    sg = int(((gd & SMALL_GRAIN) > 0).sum())
    hay = int(((gd & HAY) > 0).sum())
    crop = int(((gd & CROP_BITS) > 0).sum())
    other = int((((gd & OTHER_CROP) > 0)).sum())
    noncrop = int(((gd & NONCROP) > 0).sum())
    rot = int(rotation_from_counts(n, corn, soy, sg, hay, crop))
    # how much of the field follows the field's own rotation
    cell_rot = cell_rotation(cells[:, None, :])[0] if n_cells else np.zeros(0, np.uint8)
    ok = cell_rot > 0
    same = 100.0 * float((cell_rot[ok] == rot).sum()) / max(int(ok.sum()), 1)
    vals, cnts = np.unique(dom[valid_years], return_counts=True)
    main = name(int(vals[np.argmax(cnts)])) if len(vals) else None
    flags = []
    if n_cells and same < 70.0:
        flags.append("only {:.0f}% of the field follows this rotation".format(same))
    if dom_share and min(dom_share) < 70.0:
        yrs = [str(r["year"]) for r in rows if r["pct"] is not None and r["pct"] < 70.0]
        flags.append("split crops in " + ", ".join(yrs))
    missing = [str(r["year"]) for r in rows if r["crop"] is None]
    if missing:
        flags.append("no data for " + ", ".join(missing))
    return {
        "rows": rows,
        "summary": {
            "area": n_cells * cell_area, "years": n, "rotation": rot,
            "sequence": " ".join(letter(c) for c in dom_codes),
            "main_crop": main, "corn": corn, "soy": soy, "small_grain": sg, "hay": hay,
            "other": other, "noncrop": noncrop,
            "crop_pct": float(np.mean(crop_share)) if crop_share else None,
            "uniform_pct": float(np.mean(dom_share)) if dom_share else None,
            "rotation_pct": same, "flag": "; ".join(flags)},
    }
