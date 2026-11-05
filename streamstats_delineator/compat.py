# -*- coding: utf-8 -*-
"""Small shims so the plugin runs on QGIS 3.22+ (Qt5) and QGIS 4 (Qt6)."""

from qgis.core import Qgis, QgsField


def msg_level(name):
    """Qgis.MessageLevel member by name: 'Info', 'Warning', 'Critical', 'Success'."""
    enum = getattr(Qgis, "MessageLevel", None)
    if enum is not None and hasattr(enum, name):
        return getattr(enum, name)
    return getattr(Qgis, name)


def scoped(cls, enum_name, member):
    """cls.EnumName.member on Qt6 bindings, cls.member on Qt5 bindings."""
    enum = getattr(cls, enum_name, None)
    if enum is not None and hasattr(enum, member):
        return getattr(enum, member)
    return getattr(cls, member)


def _field_types():
    try:
        from qgis.PyQt.QtCore import QMetaType
        types = {"double": QMetaType.Type.Double, "string": QMetaType.Type.QString,
                 "int": QMetaType.Type.Int}
        QgsField("probe", types["double"])  # QgsField(QMetaType) needs QGIS >= 3.38
        return types
    except Exception:
        from qgis.PyQt.QtCore import QVariant
        return {"double": QVariant.Double, "string": QVariant.String, "int": QVariant.Int}


_TYPES = None


def make_field(name, kind):
    global _TYPES
    if _TYPES is None:
        _TYPES = _field_types()
    return QgsField(name, _TYPES[kind])
