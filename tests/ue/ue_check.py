"""Unreal side of the check. Runs inside UnrealEditor-Cmd -run=pythonscript.

Env: GA_UE_IN (folder with SM_/T_ files), GA_UE_NAME (atlas name),
     GA_UE_RESULT (json path to write).
Imports the FBX and textures with the settings the README recommends,
builds a material from the atlas textures, and records what Unreal sees.
"""

import json
import os
import traceback

import unreal

SRC = os.environ["GA_UE_IN"]
NAME = os.environ.get("GA_UE_NAME", "UECheck")
RESULT = os.environ["GA_UE_RESULT"]
DEST = "/Game/GardenAtlas"
R = {"checks": {}, "info": {}, "errors": []}


def check(name, cond, detail=""):
    R["checks"][name] = {"pass": bool(cond), "detail": str(detail)}
    unreal.log(f"[UE] {name} {'PASS' if cond else 'FAIL'} {detail}")


def task(path, options=None):
    t = unreal.AssetImportTask()
    t.filename = path
    t.destination_path = DEST
    t.automated = True
    t.replace_existing = True
    t.save = True
    if options is not None:
        t.options = options
    return t


def num_uv_channels(sm):
    """Editor subsystems are None in a commandlet; try the available APIs."""
    sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    if sub is not None:
        return sub.get_num_uv_channels(sm, 0)
    if hasattr(unreal, "EditorStaticMeshLibrary"):
        return unreal.EditorStaticMeshLibrary.get_num_uv_channels(sm, 0)
    desc = sm.get_static_mesh_description(0)
    return desc.get_num_uv_element_channels()


def main():
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    ui = unreal.FbxImportUI()
    ui.import_mesh = True
    ui.import_as_skeletal = False
    ui.import_materials = False
    ui.import_textures = False
    ui.import_animations = False
    ui.mesh_type_to_import = unreal.FBXImportType.FBXIT_STATIC_MESH
    smd = ui.static_mesh_import_data
    smd.combine_meshes = True
    smd.generate_lightmap_u_vs = False
    smd.normal_import_method = unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS_AND_TANGENTS
    tasks = [task(os.path.join(SRC, f"SM_{NAME}.fbx"), ui)]
    for suf in ("BC", "N", "ORM"):
        tasks.append(task(os.path.join(SRC, f"T_{NAME}_{suf}.png")))
    tools.import_asset_tasks(tasks)
    R["info"]["imported"] = [list(t.imported_object_paths) for t in tasks]

    lib = unreal.EditorAssetLibrary
    sm = lib.load_asset(f"{DEST}/SM_{NAME}")
    check("mesh_imported", isinstance(sm, unreal.StaticMesh), type(sm).__name__)
    if isinstance(sm, unreal.StaticMesh):
        slots = sm.static_materials
        check("one_material_slot", len(slots) == 1, [str(s.material_slot_name) for s in slots])
        # Render data (and so get_num_uv_channels) is not built under -nullrhi,
        # so compare the mesh description's UV0 with Blender's UVAtlas instead.
        R["info"]["uv_channels_render_api"] = num_uv_channels(sm)
        desc = sm.get_static_mesh_description(0)
        n_vi = desc.get_vertex_instance_count()
        ue_uv = set()
        for i in range(n_vi):
            v = desc.get_vertex_instance_uv(unreal.VertexInstanceID(i), 0)
            ue_uv.add((round(v.x, 4), round(v.y, 4)))
        bl_uv = {tuple(p) for p in json.load(open(os.path.join(SRC, "blender_uv0.json")))}
        def near(a, pool):
            return any(abs(a[0] - b[0]) <= 2e-4 and abs(a[1] - b[1]) <= 2e-4 for b in pool)
        miss = [u for u in ue_uv if not near(u, bl_uv)]
        check("uv0_is_atlas", not miss and len(ue_uv) == len(bl_uv),
              f"{len(ue_uv)} UE UVs vs {len(bl_uv)} Blender, mismatched {miss[:3]}")
        inside = all(-1e-4 <= x <= 1 + 1e-4 and -1e-4 <= y <= 1 + 1e-4 for x, y in ue_uv)
        check("uv0_in_0_1", inside, "")
        R["info"]["triangles"] = sm.get_num_triangles(0)
        R["info"]["vertices"] = sm.get_num_vertices(0)
        box = sm.get_bounding_box()
        size = box.max - box.min
        R["info"]["bounds_cm"] = [round(size.x, 2), round(size.y, 2), round(size.z, 2)]
        # 3 cubes of 2 m spaced 3 m apart along X (Blender) -> 800 x 200 x 200 cm
        check("scale_cm", abs(max(size.x, size.y) - 800) < 1 and abs(size.z - 200) < 1, R["info"]["bounds_cm"])
        check("triangles", R["info"]["triangles"] == 36, R["info"]["triangles"])

    tex = {}
    for suf in ("BC", "N", "ORM"):
        t = lib.load_asset(f"{DEST}/T_{NAME}_{suf}")
        tex[suf] = t
        check(f"tex_{suf}_imported", isinstance(t, unreal.Texture2D), type(t).__name__)
    if all(isinstance(t, unreal.Texture2D) for t in tex.values()):
        n, orm = tex["N"], tex["ORM"]
        # Unreal names *_N / normal-looking textures automatically; record and enforce.
        R["info"]["N_auto_compression"] = str(n.get_editor_property("compression_settings"))
        n.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
        n.set_editor_property("srgb", False)
        orm.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
        orm.set_editor_property("srgb", False)
        check("N_no_green_flip", not n.get_editor_property("flip_green_channel"),
              "DirectX normal: Unreal must not flip green")
        check("BC_srgb", tex["BC"].get_editor_property("srgb"), "")
        for suf, t in tex.items():
            R["info"][f"{suf}_size"] = [t.blueprint_get_size_x(), t.blueprint_get_size_y()]
            lib.save_loaded_asset(t)
        check("tex_size", all(R["info"][f"{s}_size"] == [1024, 1024] for s in tex), "")

        # material: BC -> BaseColor, ORM.R -> AO, .G -> Roughness, .B -> Metallic, N -> Normal
        mel = unreal.MaterialEditingLibrary
        mat = tools.create_asset(f"M_{NAME}", DEST, unreal.Material, unreal.MaterialFactoryNew())

        def sampler(t, stype, y):
            e = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -400, y)
            e.texture = t
            e.sampler_type = stype
            return e
        st = unreal.MaterialSamplerType
        bc = sampler(tex["BC"], st.SAMPLERTYPE_COLOR, -200)
        orm_e = sampler(orm, st.SAMPLERTYPE_MASKS, 100)
        nn = sampler(n, st.SAMPLERTYPE_NORMAL, 400)
        mp = unreal.MaterialProperty
        mel.connect_material_property(bc, "RGB", mp.MP_BASE_COLOR)
        mel.connect_material_property(orm_e, "R", mp.MP_AMBIENT_OCCLUSION)
        mel.connect_material_property(orm_e, "G", mp.MP_ROUGHNESS)
        mel.connect_material_property(orm_e, "B", mp.MP_METALLIC)
        mel.connect_material_property(nn, "RGB", mp.MP_NORMAL)
        mel.recompile_material(mat)
        lib.save_loaded_asset(mat)
        check("material_built", mel.get_num_material_expressions(mat) == 3,
              mel.get_num_material_expressions(mat))
        if isinstance(sm, unreal.StaticMesh):
            sm.set_material(0, mat)
            lib.save_loaded_asset(sm)
            check("material_assigned", sm.get_material(0) == mat, "")


try:
    main()
except Exception as e:
    R["errors"].append(traceback.format_exc())
    unreal.log_error(f"[UE] exception {e}")
with open(RESULT, "w") as f:
    json.dump(R, f, indent=2)
