"""Scene.garden_atlas settings (spec 9)."""

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, StringProperty)
from bpy.types import PropertyGroup

from .core import settings as core_settings
from .core.presets import preset_items

RES_ITEMS = [(str(r), str(r), "") for r in core_settings.RESOLUTIONS]


def _res_update(self, context):
    margin, gap = core_settings.default_spacing(int(self.width), int(self.height))
    self.bake_margin_px = margin
    self.island_gap_px = gap


class GARDENATLAS_PG_message(PropertyGroup):
    level: EnumProperty(items=[("ERROR", "Error", ""), ("WARNING", "Warning", ""), ("INFO", "Info", "")])
    text: StringProperty()
    obj: StringProperty()


class GARDENATLAS_PG_settings(PropertyGroup):
    atlas_name: StringProperty(name="이름", default="Atlas",
                               description="영문·숫자·밑줄만 사용합니다")
    width: EnumProperty(name="가로", items=RES_ITEMS, default="4096", update=_res_update)
    height: EnumProperty(name="세로", items=RES_ITEMS, default="4096", update=_res_update)
    preset: EnumProperty(name="프리셋", items=preset_items())
    density_mode: EnumProperty(name="크기 기준", items=[
        ("UNIFORM", "균일 텍셀 밀도", "3D 면적 기준으로 텍셀 밀도를 맞춥니다"),
        ("SOURCE", "소스 해상도 비율", "소스 텍스처 해상도 비율을 유지합니다")])
    overlap_mode: EnumProperty(name="겹침 처리", items=[
        ("SEPARATE", "따로 배치", "겹친 아일랜드를 각각 따로 배치합니다"),
        ("KEEP_STACKED", "겹침 유지", "겹친 아일랜드를 겹친 채로 패킹합니다"),
        ("REUNWRAP", "새로 펴기", "Smart UV Project로 새로 폅니다")])
    island_gap_px: IntProperty(name="간격(px)", default=32, min=0, max=512)
    bake_margin_px: IntProperty(name="마진(px)", default=16, min=0, max=256)
    allow_rotate: BoolProperty(name="회전 허용", default=True)
    use_basecolor: BoolProperty(name="BaseColor", default=True)
    use_normal: BoolProperty(name="Normal", default=True)
    use_roughness: BoolProperty(name="Roughness", default=True)
    use_metallic: BoolProperty(name="Metallic", default=True)
    use_ao_bake: BoolProperty(name="AO 베이크", default=False,
                              description="끄면 AO 채널을 흰색으로 채웁니다")
    ao_isolate: BoolProperty(name="AO 대상만", default=True,
                             description="AO를 구울 때 다른 오브젝트를 숨깁니다")
    use_emissive: BoolProperty(name="Emissive", default=False)
    use_opacity: BoolProperty(name="Opacity (자동 감지)", default=True)
    samples: IntProperty(name="샘플", default=8, min=1, max=4096)
    ao_samples: IntProperty(name="AO 샘플", default=64, min=1, max=4096)
    output_dir: StringProperty(name="폴더", default="//textures/", subtype="DIR_PATH")
    file_format: EnumProperty(name="포맷", items=[("PNG", "PNG", ""), ("TARGA", "TGA", "")])
    apply_modifiers: BoolProperty(name="모디파이어 적용", default=True)
    apply_scale: BoolProperty(name="스케일 적용", default=True)
    atlas_uv_only: BoolProperty(name="아틀라스 UV만 남기기", default=True)
    join_result: BoolProperty(name="하나로 합치기", default=False)
    write_manifest: BoolProperty(name="매니페스트 저장", default=True)
    normal_16bit: BoolProperty(name="노멀 16비트", default=False,
                               description="노멀을 16비트 PNG로 굽고 저장합니다")
    export_fbx: BoolProperty(name="FBX 내보내기", default=False,
                             description="결과를 Unreal용 FBX(SM_<이름>.fbx)로 출력 폴더에 저장합니다")

    # results (written by operators, read by the panel)
    report: CollectionProperty(type=GARDENATLAS_PG_message)
    analyzed: BoolProperty(default=False)
    n_objects: IntProperty()
    n_materials: IntProperty()
    n_warnings: IntProperty()
    n_errors: IntProperty()
    uv_usage: FloatProperty()
    density_min: FloatProperty()
    density_avg: FloatProperty()
    density_max: FloatProperty()
    bake_seconds: FloatProperty()
    last_output_dir: StringProperty()
    status: StringProperty()
    running: BoolProperty(default=False)


classes = (GARDENATLAS_PG_message, GARDENATLAS_PG_settings)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.garden_atlas = bpy.props.PointerProperty(type=GARDENATLAS_PG_settings)
    bpy.types.Object.garden_atlas_weight = FloatProperty(
        name="아틀라스 가중치", default=1.0, min=0.25, max=4.0,
        description="아틀라스에서 이 오브젝트의 텍셀 밀도 배수 (2 = 두 배 선명)")
    bpy.types.Material.garden_atlas_weight = FloatProperty(
        name="아틀라스 가중치", default=1.0, min=0.25, max=4.0,
        description="아틀라스에서 이 머티리얼 면의 텍셀 밀도 배수")


def unregister():
    del bpy.types.Material.garden_atlas_weight
    del bpy.types.Object.garden_atlas_weight
    del bpy.types.Scene.garden_atlas
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
