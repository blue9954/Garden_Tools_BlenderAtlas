"""Garden Atlas Tool: merge meshes/materials into one baked texture atlas.

Metadata lives in blender_manifest.toml (no bl_info).
"""

from . import ops, prefs, props, ui

_modules = (props, prefs, ops, ui)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
