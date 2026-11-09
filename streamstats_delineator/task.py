# -*- coding: utf-8 -*-
"""Background task: runs the web calls and geometry work off the GUI thread."""

from qgis.core import QgsMessageLog, QgsProject, QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from .client import StreamStatsClient, StreamStatsError
from .compat import msg_level, scoped
from .layers import prepare_geometries
from .network import qgis_fetch

LOG_TAG = "StreamStats"


class DelineationTask(QgsTask):
    """Emits ``done(result_dict)`` or ``failed(message)`` on the main thread."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, lat, lon, region=None, snap=True, timeout=120.0,
                 max_outside_m=100.0, streams=True, subbasins=False,
                 split_order=None, max_subbasins=20):
        super().__init__("StreamStats delineation at {:.5f}, {:.5f}".format(lat, lon),
                         scoped(QgsTask, "Flag", "CanCancel"))
        self.lat, self.lon = lat, lon
        self.region = region
        self.snap = snap
        self.timeout = timeout
        self.max_outside_m = max_outside_m
        self.streams = streams
        self.subbasins = subbasins
        self.split_order = split_order
        self.max_subbasins = max_subbasins
        # copied on the main thread; used by QgsDistanceArea in run()
        self.transform_context = QgsProject.instance().transformContext()
        self.result_data = None
        self.error = None

    def run(self):
        # Never raise from run(): an exception here can take QGIS down.
        client = StreamStatsClient(
            fetch=qgis_fetch,
            timeout=self.timeout,
            is_canceled=self.isCanceled,
            log=lambda m: QgsMessageLog.logMessage(m, LOG_TAG, msg_level("Info")),
        )
        try:
            self.setProgress(5)
            res = client.delineate_from_click(
                self.lat, self.lon, region=self.region, snap=self.snap,
                max_outside_m=self.max_outside_m, streams=self.streams,
                subbasins=self.subbasins, split_order=self.split_order,
                max_subbasins=self.max_subbasins)
            if self.isCanceled():
                return False
            self.setProgress(80)
            self.result_data = prepare_geometries(res, self.transform_context,
                                                  self.isCanceled)
            if self.isCanceled():
                return False
            self.setProgress(100)
            return True
        except StreamStatsError as e:
            self.error = str(e)
        except Exception as e:  # unexpected: keep a full message for the log
            self.error = "{}: {}".format(type(e).__name__, e)
        return False

    def finished(self, ok):
        if ok and self.result_data is not None:
            for key, label in (("streams_error", "Streams"),
                               ("subbasins_error", "Sub-basins")):
                err = self.result_data.get(key)
                if err:
                    QgsMessageLog.logMessage("{}: {}".format(label, err), LOG_TAG,
                                             msg_level("Warning"))
            self.done.emit(self.result_data)
        elif self.isCanceled():
            self.failed.emit("Delineation canceled.")
        else:
            QgsMessageLog.logMessage(self.error or "unknown error", LOG_TAG,
                                     msg_level("Critical"))
            self.failed.emit(self.error or "Delineation failed.")
