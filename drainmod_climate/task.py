"""Background QgsTasks: build DRAINMOD files, download LOCA2 files."""
import io
import os
import traceback

from qgis.core import QgsTask, QgsMessageLog, Qgis

from .core.series import DailySeries
from .core.writer import write_all, safe_name
from .providers.nex_gddp import NexGddpProvider, THREDDS
from .providers import generic_ncss, local_netcdf
from .core.net import download_file

TAG = "DRAINMOD Climate"


def log(msg, level=Qgis.Info):
    QgsMessageLog.logMessage(msg, TAG, level)


class BuildWeatherTask(QgsTask):
    def __init__(self, job):
        QgsTask.__init__(self, "DRAINMOD climate weather files", QgsTask.Flag.CanCancel)
        self.job = job
        self.results = []
        self.errors = []
        self.exception = None

    def _period(self, scenario):
        j = self.job
        if scenario.lower() == "historical":
            return j["hist_y0"], j["hist_y1"]
        return j["fut_y0"], j["fut_y1"]

    def _combos(self):
        j = self.job
        if j["source"] == "local":
            return sorted(j["local_groups"].keys())
        return [(m, s) for m in j["models"] for s in j["scenarios"]]

    def run(self):
        j = self.job
        try:
            combos = self._combos()
            n = max(1, len(combos) * len(j["sites"]))
            k = 0
            nex = NexGddpProvider(j.get("thredds") or THREDDS, cache_dir=j.get("cache_dir"))
            for site in j["sites"]:
                for model, scen in combos:
                    if self.isCanceled():
                        return False
                    y0, y1 = self._period(scen)
                    label = "%s / %s / %s" % (site["name"], model, scen)
                    base_prog = 100.0 * k / n

                    def prog(f, _b=base_prog):
                        self.setProgress(_b + 100.0 * f / n)

                    try:
                        raw, info = self._fetch(nex, model, scen, site, y0, y1, prog)
                        series = DailySeries.build(raw, y0, y1, j.get("leap_mode", "feb29"))
                        base = safe_name("%s_%s_%d-%d" % (model, scen,
                                                          series.years[0], series.years[1]))
                        out = os.path.join(j["out_dir"], safe_name(site["name"]), base)
                        meta = {
                            "Source": j["source_label"],
                            "Model": model,
                            "Scenario": scen,
                            "Ensemble member": info.get("member", "-"),
                            "Grid cell centre": "%s, %s" % (
                                _f(info.get("cell_lat")), _f(info.get("cell_lon"))),
                        }
                        write_all(series, out, site["station"], site,
                                  j["pet"], j["csv"], meta)
                        y0r, y1r = series.years
                        nyr = y1r - y0r + 1
                        self.results.append({
                            "site": site["name"], "model": model, "scenario": scen,
                            "member": info.get("member", ""),
                            "cell_lat": info.get("cell_lat"), "cell_lon": info.get("cell_lon"),
                            "y0": y0r, "y1": y1r,
                            "precip_mm_yr": sum(series.pr) / nyr,
                            "tmax_c": sum(series.tmax) / len(series.tmax),
                            "tmin_c": sum(series.tmin) / len(series.tmin),
                            "folder": out, "base": base,
                        })
                        log("OK  %s -> %s" % (label, base))
                    except Exception as e:  # noqa
                        if self.isCanceled():
                            return False
                        self.errors.append("%s: %s" % (label, e))
                        log("FAILED %s: %s\n%s" % (label, e, traceback.format_exc()), Qgis.Warning)
                    k += 1
                    self.setProgress(100.0 * k / n)
            self._summary()
            return True
        except Exception as e:  # noqa
            self.exception = e
            log(traceback.format_exc(), Qgis.Critical)
            return False

    def _fetch(self, nex, model, scen, site, y0, y1, prog):
        j = self.job
        if j["source"] == "nex":
            return nex.fetch(model, scen, site["lat"], site["lon"], y0, y1,
                             log=log, progress=prog, cancelled=self.isCanceled)
        if j["source"] == "ncss":
            return generic_ncss.fetch(j["ncss_template"], j["ncss_vars"], model, scen,
                                      site["lat"], site["lon"], y0, y1,
                                      cache_dir=j.get("cache_dir"), progress=prog,
                                      cancelled=self.isCanceled, log=log)
        files = j["local_groups"][(model, scen)]
        return local_netcdf.fetch_local(files, site["lat"], site["lon"],
                                        j.get("local_vars"), log=log)

    def _summary(self):
        if not self.results:
            return
        p = os.path.join(self.job["out_dir"], "ensemble_summary.csv")
        with io.open(p, "w", newline="") as f:
            f.write("site,model,scenario,member,cell_lat,cell_lon,start,end,"
                    "precip_mm_yr,tmax_c,tmin_c,folder\n")
            for r in self.results:
                f.write("%s,%s,%s,%s,%s,%s,%d,%d,%.1f,%.2f,%.2f,%s\n" % (
                    r["site"], r["model"], r["scenario"], r["member"],
                    _f(r["cell_lat"]), _f(r["cell_lon"]), r["y0"], r["y1"],
                    r["precip_mm_yr"], r["tmax_c"], r["tmin_c"], os.path.relpath(r["folder"], self.job["out_dir"])))
        self.summary_path = p


def _f(x):
    return "" if x is None else "%.4f" % x


class Loca2Task(QgsTask):
    """Find LOCA2 files on the UCSD server for each (model, scenario) and
    download them into a folder (skips files already present)."""

    def __init__(self, specs, region, member, folder):
        QgsTask.__init__(self, "LOCA2: find and download files", QgsTask.Flag.CanCancel)
        self.specs, self.region, self.member, self.folder = specs, region, member, folder
        self.done = []
        self.missing = []
        self.exception = None

    def run(self):
        from .providers import loca2
        try:
            items = []
            for model, scen in self.specs:
                if self.isCanceled():
                    return False
                found = loca2.find_files(model, self.region, self.member, scen, log=log)
                for var, lst in found.items():
                    if not lst:
                        self.missing.append("%s %s %s" % (model, scen, var))
                    for url, size in lst:
                        log("Found %s (%s)" % (url, size or "size ?"))
                        dest = os.path.join(self.folder, url.rsplit("/", 1)[-1])
                        items.append((url, dest))
            self.items = items
            return self._download()
        except Exception as e:  # noqa
            self.exception = e
            log(traceback.format_exc(), Qgis.Warning)
            return False

    def _download(self):
        os.makedirs(self.folder, exist_ok=True)
        return DownloadTask.run(self)


class DownloadTask(QgsTask):
    """Download a list of (url, dest) pairs (LOCA2 files)."""

    def __init__(self, items):
        QgsTask.__init__(self, "Downloading LOCA2 files", QgsTask.Flag.CanCancel)
        self.items = items
        self.done = []
        self.exception = None

    def run(self):
        try:
            n = max(1, len(self.items))
            for i, (url, dest) in enumerate(self.items):
                if self.isCanceled():
                    return False
                if os.path.exists(dest) and os.path.getsize(dest) > 0:
                    self.done.append(dest)
                    continue
                log("Downloading %s" % url)

                def prog(d, t, _i=i):
                    self.setProgress(100.0 * (_i + d / float(t)) / n)

                download_file(url, dest, prog, self.isCanceled)
                self.done.append(dest)
            return True
        except Exception as e:  # noqa
            self.exception = e
            log(traceback.format_exc(), Qgis.Warning)
            return False
