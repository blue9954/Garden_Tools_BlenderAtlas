"""3D View N-panel, tab "Garden" (spec 8). draw() only reads data."""

import bpy
from bpy.types import Panel

ICON = {"ERROR": "CANCEL", "WARNING": "ERROR", "INFO": "INFO"}
MAX_LINES = 12


class GARDENATLAS_PT_atlas(Panel):
    bl_label = "Atlas"
    bl_idname = "GARDENATLAS_PT_atlas"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Garden"

    def draw(self, context):
        p = context.scene.garden_atlas
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        # -- targets
        head, body = layout.panel("garden_atlas_targets", default_closed=False)
        head.label(text="대상")
        if body:
            row = body.row()
            sel = [o for o in context.selected_objects if o.type == "MESH"]
            if p.analyzed:
                row.label(text=f"오브젝트 {p.n_objects}   머티리얼 {p.n_materials}")
            else:
                row.label(text=f"선택한 메시 {len(sel)}")
            row = body.row()
            row.label(text=f"경고 {p.n_warnings}", icon="ERROR")
            row.label(text=f"오류 {p.n_errors}", icon="CANCEL")
            row.operator("garden_atlas.analyze", text="검사")
            col = body.column(align=True)
            for i, m in enumerate(p.report):
                if i >= MAX_LINES:
                    col.label(text=f"... 외 {len(p.report) - MAX_LINES}개 (콘솔 참고)")
                    break
                r = col.row(align=True)
                r.label(text=(f"{m.obj}: " if m.obj else "") + m.text, icon=ICON[m.level])
                if m.obj:
                    op = r.operator("garden_atlas.select_issue", text="", icon="RESTRICT_SELECT_OFF")
                    op.obj = m.obj

        # -- atlas
        head, body = layout.panel("garden_atlas_atlas", default_closed=False)
        head.label(text="아틀라스")
        if body:
            body.prop(p, "atlas_name")
            row = body.row(align=True)
            row.prop(p, "width", text="해상도")
            row.prop(p, "height", text="")
            body.prop(p, "preset")
            row = body.row(align=True)
            row.operator("garden_atlas.preset_load", icon="IMPORT")
            row.operator("garden_atlas.preset_save", icon="EXPORT")

        # -- uv
        head, body = layout.panel("garden_atlas_uv", default_closed=False)
        head.label(text="UV 레이아웃")
        if body:
            body.prop(p, "density_mode")
            body.prop(p, "overlap_mode")
            body.prop(p, "island_gap_px")
            body.prop(p, "allow_rotate")
            ob = context.active_object
            if ob is not None and ob.type == "MESH" and hasattr(ob, "garden_atlas_weight"):
                box = body.box()
                box.label(text=f"가중치: {ob.name}", icon="OBJECT_DATA")
                box.prop(ob, "garden_atlas_weight", text="오브젝트")
                mat = ob.active_material
                if mat is not None:
                    box.prop(mat, "garden_atlas_weight", text=f"머티리얼 {mat.name}")
            body.operator("garden_atlas.build_uv", icon="UV")

        # -- channels
        head, body = layout.panel("garden_atlas_channels", default_closed=False)
        head.label(text="채널")
        if body:
            grid = body.grid_flow(columns=3, align=True)
            grid.use_property_split = False
            for name in ("use_basecolor", "use_normal", "use_roughness", "use_metallic",
                         "use_ao_bake", "use_emissive", "use_opacity"):
                grid.prop(p, name)
            if p.use_ao_bake:
                body.prop(p, "ao_isolate")
                body.prop(p, "ao_samples")
            body.prop(p, "normal_16bit")
            body.prop(p, "samples")
            body.prop(p, "bake_margin_px")
            if p.island_gap_px < 2 * p.bake_margin_px:
                body.label(text="간격이 마진의 2배보다 작습니다", icon="ERROR")

        # -- output
        head, body = layout.panel("garden_atlas_output", default_closed=False)
        head.label(text="출력")
        if body:
            body.prop(p, "output_dir")
            body.prop(p, "file_format")
            body.prop(p, "write_manifest")

        # -- result
        head, body = layout.panel("garden_atlas_result", default_closed=False)
        head.label(text="결과")
        if body:
            col = body.column()
            col.use_property_split = False
            col.prop(p, "apply_modifiers")
            col.prop(p, "apply_scale")
            col.prop(p, "join_result")
            col.prop(p, "atlas_uv_only")
            col.prop(p, "export_fbx")

        col = layout.column()
        col.scale_y = 1.6
        col.enabled = p.n_errors == 0 and not p.running
        col.operator("garden_atlas.run_all", icon="RENDER_STILL")
        if p.n_errors:
            first = next((m for m in p.report if m.level == "ERROR"), None)
            layout.label(text=f"오류: {first.text if first else ''}", icon="CANCEL")
        if p.running:
            layout.label(text=f"진행 중: {p.status}  (ESC 취소)", icon="TIME")
        layout.operator("garden_atlas.bake", text="베이크만 다시 실행", icon="FILE_REFRESH")

        # -- report
        head, body = layout.panel("garden_atlas_report", default_closed=False)
        head.label(text="리포트")
        if body:
            body.label(text=f"UV 사용률 {p.uv_usage * 100:.0f}%   밀도 평균 {p.density_avg:.0f} px/m")
            body.label(text=f"밀도 최소 {p.density_min:.0f} / 최대 {p.density_max:.0f} px/m")
            row = body.row()
            row.label(text=f"소요 {p.bake_seconds:.1f}초")
            row.operator("garden_atlas.open_output", icon="FILE_FOLDER")


classes = (GARDENATLAS_PT_atlas,)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
