"""Add-on preferences."""

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import AddonPreferences

from .core.presets import preset_items


class GARDENATLAS_AP_prefs(AddonPreferences):
    bl_idname = __package__

    default_output_dir: StringProperty(
        name="기본 출력 폴더", default="//textures/", subtype="DIR_PATH",
        description="씬 설정의 출력 폴더가 비어 있을 때 사용합니다")
    default_preset: EnumProperty(name="기본 프리셋", items=preset_items())

    def draw(self, context):
        self.layout.prop(self, "default_output_dir")
        self.layout.prop(self, "default_preset")


classes = (GARDENATLAS_AP_prefs,)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
