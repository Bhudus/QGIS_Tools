# -*- coding: utf-8 -*-
"""
Run the Drainage outlet algorithm as a QGIS background task.

The normal Processing parameter window is shown; pressing Run validates the
inputs, closes the window and hands the job to the QGIS task manager
(progress + cancel in the status bar). Output layers are loaded when done.
"""
from qgis.core import (Qgis, QgsApplication, QgsMessageLog, QgsProcessingAlgRunnerTask,
                       QgsProcessingFeedback)
from qgis.utils import iface

from processing.gui.AlgorithmDialog import AlgorithmDialog
from processing.gui.AlgorithmDialogBase import AlgorithmDialogBase
from processing.tools import dataobjects

LOG_TAG = "Drainage outlet"
_running = []   # keep task, context, feedback alive until finished


class _LogFeedback(QgsProcessingFeedback):
    """Collects warnings so they can be reported when the task ends."""

    def __init__(self):
        super().__init__()
        self.warnings = []
        self.lines = []

    def pushInfo(self, info):
        self.lines.append(info)
        super().pushInfo(info)

    def pushWarning(self, warning):
        self.warnings.append(warning)
        self.lines.append("WARNING: " + warning)
        super().pushWarning(warning)

    def reportError(self, error, fatalError=False):
        self.lines.append("ERROR: " + error)
        super().reportError(error, fatalError)


class BackgroundAlgorithmDialog(AlgorithmDialog):
    """Processing dialog whose Run button launches a background task."""

    def runAlgorithm(self):
        try:
            self._run_in_background()
        except Exception as e:  # unexpected dialog API: fall back to normal run
            QgsMessageLog.logMessage("Background run unavailable ({}); running in the window.".format(e),
                                     LOG_TAG, Qgis.Warning)
            super().runAlgorithm()

    def _run_in_background(self):
        alg = self.algorithm()
        try:
            parameters = self.getParameterValues()
        except AlgorithmDialogBase.InvalidParameterValue as e:
            self.flag_invalid_parameter_value(e.parameter.description(), e.widget)
            return
        except AlgorithmDialogBase.InvalidOutputExtension as e:
            self.flag_invalid_output_extension(e.message, e.widget)
            return

        feedback = _LogFeedback()
        context = dataobjects.createContext(feedback)
        ok, msg = alg.checkParameterValues(parameters, context)
        if not ok:
            self.messageBar().pushMessage("", msg, level=Qgis.Warning, duration=8)
            return

        task = QgsProcessingAlgRunnerTask(alg, parameters, context, feedback)
        entry = {"task": task, "context": context, "feedback": feedback,
                 "alg": alg, "parameters": parameters}
        _running.append(entry)
        task.executed.connect(lambda ok, results, e=entry: _on_finished(e, ok, results))
        QgsApplication.taskManager().addTask(task)
        iface.messageBar().pushMessage(
            LOG_TAG, "Running in the background - progress and Cancel are in the status bar.",
            level=Qgis.Info, duration=6)
        self.close()


def _on_finished(entry, ok, results):
    feedback, context = entry["feedback"], entry["context"]
    try:
        for line in feedback.lines:
            QgsMessageLog.logMessage(line, LOG_TAG, Qgis.Info)
        if ok:
            from processing.gui.Postprocessing import handleAlgorithmResults
            handleAlgorithmResults(entry["alg"], context, feedback=feedback,
                                   parameters=entry["parameters"])
            n_warn = len(feedback.warnings)
            msg = "Drainage paths are ready."
            if n_warn:
                msg += " {} warning(s) - see the Log Messages panel ('{}' tab).".format(n_warn, LOG_TAG)
            iface.messageBar().pushMessage(LOG_TAG, msg,
                                           level=Qgis.Warning if n_warn else Qgis.Success,
                                           duration=10)
        else:
            canceled = feedback.isCanceled()
            iface.messageBar().pushMessage(
                LOG_TAG,
                "Cancelled." if canceled else
                "Failed - see the Log Messages panel ('{}' tab) for details.".format(LOG_TAG),
                level=Qgis.Info if canceled else Qgis.Critical, duration=0 if not canceled else 6)
    finally:
        if entry in _running:
            _running.remove(entry)


def open_background_dialog(alg_id, params):
    """Show the parameter window; returns False if this QGIS can't do it."""
    alg = QgsApplication.processingRegistry().createAlgorithmById(alg_id)
    if alg is None:
        return False
    dlg = BackgroundAlgorithmDialog(alg, parent=iface.mainWindow())
    if params:
        try:
            dlg.setParameters(params)
        except Exception:
            pass
    dlg.show()
    dlg.raise_()
    entry_dialogs.append(dlg)
    dlg.finished.connect(lambda *_: entry_dialogs.remove(dlg) if dlg in entry_dialogs else None)
    return True


entry_dialogs = []
