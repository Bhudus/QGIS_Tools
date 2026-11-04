"""Offline tests: SDA responses are mocked in SDA's own JSON+COLUMNNAME format."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from drainmod_soi.core import http, texture, hydraulics, layers, pipeline  # noqa: E402

HZ_COLS = ["chkey", "hzname", "hzdept_r", "hzdepb_r", "sandtotal_r", "silttotal_r",
           "claytotal_r", "om_r", "dbthirdbar_r", "ksat_r", "wsatiated_r",
           "wthirdbar_r", "wfifteenbar_r", "texdesc"]

# Drummer-like profile: 7 horizons, ksat in um/s as SSURGO stores it
DRUMMER = [
    ["1", "Ap", "0", "18", "8.3", "61.7", "30", "6", "1.30", "9", "48", "33", "17", "silty clay loam"],
    ["2", "A", "18", "36", "8.4", "60.6", "31", "4.5", "1.33", "9", "47", "33", "17", "silty clay loam"],
    ["3", "BA", "36", "46", "8.3", "61.7", "30", "2.5", "1.38", "9", "45", "31", "17", "silty clay loam"],
    ["4", "Bg", "46", "79", "8.2", "62.8", "29", "1", "1.45", "9", "43", "30", "16", "silty clay loam"],
    ["5", "Btg", "79", "107", "15", "60", "25", "0.5", "1.50", "9", "41", "29", "15", "silt loam"],
    ["6", "2BCg", "107", "127", "30", "47", "23", "0.3", "1.55", "9", "40", "26", "14", "loam"],
    ["7", "2Cg", "127", "203", "41.4", "40.6", "18", "0.2", "1.60", "9", "39", "24", "12", "loam"],
]
# Gilford-like profile with an O horizon and bedrock at the bottom
GILFORD = [
    ["11", "Oa", "0", "5", None, None, None, "60", "0.4", "28", None, None, None, "muck"],
    ["12", "Ap", "5", "28", "65", "22", "13", "3", "1.45", "9", "41", "18", "8", "fine sandy loam"],
    ["13", "Bg", "28", "71", "70", "18", "12", "0.5", "1.55", "28", "40", "15", "7", "sandy loam"],
    ["14", "2Cg", "71", "152", "92", None, "3", "0.1", "1.6", "92", "38", "6", "3", "sand"],
    ["15", "R", "152", "200", None, None, None, None, None, None, None, None, None, "unweathered bedrock"],
]


def fake_post(url, payload):
    q = payload.get("query", "")
    if "Rosetta" in url or "rosetta" in url:
        raise RuntimeError("offline")
    if "SDA_Get_Mukey" in q:
        return {"Table": [["mukey", "muname", "musym", "areasymbol"],
                          ["242984", "Drummer silty clay loam, 0 to 2 percent slopes", "152A", "IL019"]]}
    if "FROM component" in q:
        return {"Table": [["cokey", "compname", "comppct_r", "majcompflag", "compkind",
                           "drainagecl", "hydgrp", "taxclname"],
                          ["9001", "Water", "95", "Yes", "Miscellaneous area", None, None, None],
                          ["9002", "Drummer", "90", "Yes", "Series", "Poorly drained", "B/D", "x"],
                          ["9003", "Gilford", "10", "No", "Series", "Poorly drained", "B/D", "x"]]}
    if "FROM chorizon" in q:
        rows = GILFORD if "'9003'" in q else DRUMMER
        return {"Table": [HZ_COLS] + rows}
    raise AssertionError(q)


http.set_post_json(fake_post)


def test_classify():
    assert texture.classify(92, 5, 3) == "sand"
    assert texture.classify(65, 22, 13) == "sandy loam"
    assert texture.classify(40, 40, 20) == "loam"
    assert texture.classify(8, 62, 30) == "silty clay loam"
    assert texture.classify(20, 65, 15) == "silt loam"
    assert texture.classify(32, 34, 34) == "clay loam"
    assert texture.classify(10, 40, 50) == "silty clay"
    assert texture.classify(20, 20, 60) == "clay"
    assert texture.classify(55, 15, 30) == "sandy clay loam"
    # every point on a 1% grid gets a class
    for c in range(101):
        for s in range(101 - c):
            assert texture.classify(s, 100 - s - c, c) in texture.TEXTURE_CLASSES


def test_texdesc():
    assert texture.class_from_texdesc("gravelly fine sandy loam") == "sandy loam"
    assert texture.class_from_texdesc("silty clay loam") == "silty clay loam"
    assert texture.class_from_texdesc("very fine sand") == "sand"
    assert texture.class_from_texdesc("muck") is None
    assert texture.class_from_texdesc("weathered bedrock") is None


def test_vg_curve():
    p = hydraulics.class_params("loam", "carsel")
    assert abs(p.theta(0) - 0.43) < 1e-9
    vals = [p.theta(h) for h in hydraulics.DEFAULT_SUCTIONS]
    assert all(a >= b for a, b in zip(vals, vals[1:]))
    # hand calc at h=330 cm: Carsel loam -> 0.165
    assert abs(p.theta(330) - 0.165) < 0.002


def test_drummer_merging():
    mu, comp, res = pipeline.build_from_point(-88.2, 40.1, pipeline.Options(45, 30))
    assert comp["compname"] == "Drummer"          # skipped 'Water'
    assert res.name == "Drummer silty clay loam"
    assert 1 <= len(res.layers) <= 5
    assert res.layers[-1].bottom == 203
    # Ksat 9 um/s -> 3.24 cm/hr
    assert abs(res.layers[0].ksat_cmhr - 3.24) < 1e-9
    return res


def test_max_layer_limit():
    for n in range(1, 6):
        opt = pipeline.Options(45, 30, max_layers=n, merge_similar=False)
        _, _, res = pipeline.build_from_point(-88.2, 40.1, opt)
        assert len(res.layers) == n
        tops = [L.top for L in res.layers]
        assert tops[0] == 0 and all(a.bottom == b.top for a, b in zip(res.layers, res.layers[1:]))


def test_gilford_cleanup():
    _, _, res = pipeline.build_from_point(-88.2, 40.1, pipeline.Options(45, 30), cokey="9003")
    assert res.name == "Gilford fine sandy loam"
    assert res.layers[0].top == 0                     # muck folded into Ap
    assert res.layers[-1].bottom == 152               # bedrock dropped
    assert any("bedrock" in w for w in res.warnings)
    assert any("muck" in w for w in res.warnings)
    return res


def test_format_matches_sample():
    _, _, res = pipeline.build_from_point(-88.2, 40.1, pipeline.Options(45, 30), cokey="9003")
    lines = res.text.splitlines()
    n = len(res.layers)
    assert lines[1] == "%2d 0" % n
    assert len(lines) == 2 + n * 17 + 1
    hdr = lines[2]
    assert len(hdr) == 20 and hdr[:2] == "16"
    pt = lines[3]
    assert len(pt) == 13 and pt.endswith("     0.0")
    assert lines[18].endswith(" 15000.0")
    assert lines[-1] == "  45  30   2"   # 30 cm lies in layer 2 (28-71 cm)


def test_rosetta3_fallback():
    opt = pipeline.Options(45, 30, method="rosetta3")
    _, _, res = pipeline.build_from_point(-88.2, 40.1, opt)
    assert any("Rosetta 3 service failed" in w for w in res.warnings)


def test_rosetta3_with_water_contents():
    sent = []

    def rosetta_post(url, payload):
        if "rosetta" in url:
            sent.append(payload["soildata"])
            # plausible log10 parameters for each row
            return {"van_genuchten_params": [[0.08, 0.45, -2.0, 0.17, 1.2]
                                             for _ in payload["soildata"]]}
        return fake_post(url, payload)

    http.set_post_json(rosetta_post)
    try:
        hydraulics._ROSETTA3_CACHE.clear()
        opt = pipeline.Options(45, 30, method="rosetta3w")
        _, _, res = pipeline.build_from_point(-88.2, 40.1, opt)
        rows = sent[-1]
        assert all(len(r) == 6 for r in rows)            # SSC + BD + th33 + th1500
        assert abs(rows[0][3] - 1.3) < 0.1                  # Db g/cm3
        assert 0.2 < rows[0][4] < 0.4 and 0.1 < rows[0][5] < 0.25   # cm3/cm3, not %
        assert res.params[0].source == "Rosetta 3 (SSC+BD+TH33+TH1500)"
        assert not res.warnings

        # plain Rosetta 3 option still sends SSC+BD only
        _, _, res = pipeline.build_from_point(-88.2, 40.1, pipeline.Options(45, 30, method="rosetta3"))
        assert all(len(r) == 4 for r in sent[-1])

        # a layer missing 1500 kPa water -> steps down to SSC+BD+TH33 with a note
        saved = DRUMMER[-1][12]
        DRUMMER[-1][12] = None
        try:
            hydraulics._ROSETTA3_CACHE.clear()
            _, _, res = pipeline.build_from_point(-88.2, 40.1, opt)
        finally:
            DRUMMER[-1][12] = saved
        assert all(len(r) == 5 for r in sent[-1])
        assert any("SSC+BD+TH33 model" in w for w in res.warnings), res.warnings
    finally:
        http.set_post_json(fake_post)
        hydraulics._ROSETTA3_CACHE.clear()


def test_root_layer():
    _, _, res = pipeline.build_from_point(-88.2, 40.1, pipeline.Options(150, 120, max_layers=5,
                                                                         merge_similar=False))
    idx = int(res.text.splitlines()[-1].split()[2])
    L = res.layers[idx - 1]
    assert L.top < 120 <= L.bottom


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok", k)
    print()
    print(test_drummer_merging().summary())
    print()
    r = test_gilford_cleanup()
    print(r.summary())
    print(r.warnings)
    print(r.text)
