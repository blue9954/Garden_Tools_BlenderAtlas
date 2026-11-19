"""Blender side of the Unreal check: build a scene and export the atlas + FBX.

    blender -b --factory-startup --python tests/ue/make_assets.py -- OUT_DIR
"""

import json
import os
import sys

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import garden_atlas_tool as addon  # noqa: E402

OUT = os.path.abspath(sys.argv[sys.argv.index("--") + 1])
os.makedirs(OUT, exist_ok=True)


def main():
    addon.register()
    for c in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for it in list(c):
            c.remove(it)
    # tangent-space tilt towards +Y (up in UV v): DirectX stores it as G < 0.5
    n = np.array([0.0, 0.45, 0.89])
    n /= np.linalg.norm(n)
    nimg = bpy.data.images.new("Tilt", 16, 16, alpha=True)
    nimg.colorspace_settings.name = "Non-Color"
    px = np.ones((16, 16, 4), np.float32)
    px[..., :3] = 0.5 + 0.5 * n
    nimg.pixels.foreach_set(px.ravel())
    nimg.pack()
    objs = []
    for i, col in enumerate([(0.8, 0.1, 0.1), (0.1, 0.7, 0.2), (0.2, 0.3, 0.9)]):
        bpy.ops.mesh.primitive_cube_add(location=(i * 3, 0, 1))
        o = bpy.context.active_object
        o.name = f"Crate{i}"
        for k in range(2):
            m = bpy.data.materials.new(f"Crate{i}_{k}")
            t = m.node_tree
            p = next(x for x in t.nodes if x.bl_idname == "ShaderNodeBsdfPrincipled")
            p.inputs["Base Color"].default_value = (*[c * (1 - 0.4 * k) for c in col], 1)
            p.inputs["Metallic"].default_value = float(k)
            p.inputs["Roughness"].default_value = 0.3 + 0.4 * k
            if k == 1:
                tn = t.nodes.new("ShaderNodeTexImage")
                tn.image = nimg
                nm = t.nodes.new("ShaderNodeNormalMap")
                t.links.new(tn.outputs["Color"], nm.inputs["Color"])
                t.links.new(nm.outputs["Normal"], p.inputs["Normal"])
            o.data.materials.append(m)
        o.data.polygons.foreach_set("material_index", np.array([0, 1, 0, 1, 0, 1], np.int32))
        o.data.update()
        objs.append(o)
    for o in bpy.context.view_layer.objects:
        o.select_set(o in objs)
    bpy.context.view_layer.objects.active = objs[0]
    p = bpy.context.scene.garden_atlas
    p.atlas_name = "UECheck"
    p.width = p.height = "1024"
    p.output_dir = OUT
    p.join_result = True
    p.export_fbx = True
    assert bpy.ops.garden_atlas.run_all() == {"FINISHED"}
    # UVAtlas of the result in Unreal's convention (v flipped) for the UE-side comparison
    me = bpy.data.objects["SM_UECheck"].data
    uv = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers["UVAtlas"].uv.foreach_get("vector", uv)
    uv = uv.reshape(-1, 2)
    uv[:, 1] = 1.0 - uv[:, 1]
    with open(os.path.join(OUT, "blender_uv0.json"), "w") as f:
        json.dump(sorted({(round(float(x), 4), round(float(y), 4)) for x, y in uv}), f)
    print("[UE-BL] files", sorted(os.listdir(OUT)))


main()
