# -*- coding: utf-8 -*-
"""
Remembers the tool's settings from the last run, separately for each project.

Settings are stored in the QGIS user profile (QgsSettings), keyed by the
project file path, so:
  * reopening the dialog, or restarting QGIS with the same project, restores
    the last-used settings;
  * opening a different project, or starting a new one, gives the defaults
    (or that project's own last-used settings).

Settings used in a new, unsaved project are carried over when it is first
saved, and dropped when the project is closed without saving.
"""

import hashlib
import json

from qgis.core import QgsProject, QgsSettings
from qgis.core import QgsProcessingFeatureSourceDefinition

ROOT = "TileDrainage/BoundaryContours/last_run/"
UNSAVED = "unsaved"

# Plain-value parameters remembered as they are
SCALAR_KEYS = (
    "BAND", "INTERVAL", "INDEX_EVERY", "BUFFER", "CLIP", "FIELD_NAME",
    "FILL_METHOD", "SMOOTH_DEM", "SMOOTH_LINES", "APPLY_STYLE", "OFFSET",
    "FILL_MARGIN", "SMOOTH_OFFSET", "MIN_LENGTH", "CREATE_3D",
    "IGNORE_NODATA", "NODATA",
)


def project_key(project=None):
    project = project or QgsProject.instance()
    path = project.absoluteFilePath() if project else ""
    if not path:
        return UNSAVED
    return hashlib.sha1(path.encode("utf-8")).hexdigest()


def save(values, project=None):
    QgsSettings().setValue(ROOT + project_key(project), json.dumps(values))


def load(project=None):
    raw = QgsSettings().value(ROOT + project_key(project), "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def forget_unsaved():
    QgsSettings().remove(ROOT + UNSAVED)


def adopt_unsaved(project=None):
    """Move settings from an unsaved project to its new file path."""
    s = QgsSettings()
    raw = s.value(ROOT + UNSAVED, "")
    key = project_key(project)
    if raw and key != UNSAVED:
        if not s.value(ROOT + key, ""):
            s.setValue(ROOT + key, raw)
        s.remove(ROOT + UNSAVED)


def dialog_parameters(project=None):
    """Last-run values as initial parameters for the algorithm dialog.
    Layers are only restored if they are still in the project."""
    project = project or QgsProject.instance()
    stored = load(project)
    params = {k: stored[k] for k in SCALAR_KEYS if k in stored and stored[k] is not None}

    dem_id = stored.get("INPUT")
    if dem_id and project.mapLayer(dem_id):
        params["INPUT"] = dem_id
    else:
        params.pop("BAND", None)  # band only makes sense with the same DEM

    bnd_id = stored.get("BOUNDARY")
    if bnd_id and project.mapLayer(bnd_id):
        if stored.get("BOUNDARY_SELECTED"):
            params["BOUNDARY"] = QgsProcessingFeatureSourceDefinition(bnd_id, True)
        else:
            params["BOUNDARY"] = bnd_id
    return params
