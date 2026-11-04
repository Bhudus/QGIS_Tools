"""Qt5 / Qt6 enum access (QGIS 3 uses PyQt5, QGIS 4 uses PyQt6)."""


def qenum(cls, scope, name):
    scoped = getattr(cls, scope, None)
    if scoped is not None and hasattr(scoped, name):
        return getattr(scoped, name)
    return getattr(cls, name)
