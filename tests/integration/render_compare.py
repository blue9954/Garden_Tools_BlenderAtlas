"""Acceptance check (spec 16): 5 objects / 10+ materials, original vs atlas result.

    blender -b --factory-startup --python-exit-code 1 --python tests/integration/render_compare.py [-- OUT_DIR]

Renders the source objects and the atlas result from the same camera with
Cycles and compares them. Writes source.png, result.png, side_by_side.png,
diff.png and metrics.json into OUT_DIR (default: a temp folder).
"""

import json
import math
import os
import sys
import tempfile

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import garden_atlas_tool as addon  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = os.path.abspath(argv[0]) if argv else tempfile.mkdtemp(prefix="ga_cmp_")
os.makedirs(OUT, exist_ok=True)
TEX = os.path.join(OUT, "textures")
W, H = 640, 400
MEAN_TOL = 4.0 / 255   # mean |diff| over object pixels
P95_TOL = 16.0 / 255


def img_fn(name, size, fn, cs="sRGB"):
    img = bpy.data.images.new(name, size, size, alpha=True)
    img.colorspace_settings.name = cs
    ys, xs = np.mgrid[0:size, 0:size]
    u, v = (xs + 0.5) / size, (ys + 0.5) / size
    a = np.ones((size, size, 4), np.float32)
    a[..., :3] = fn(u, v)
    img.pixels.foreach_set(a.ravel())
    img.pack()
    return img


def mat(name, color=(0.8, 0.8, 0.8), metallic=0.0, rough=0.5, image=None, normal=None, rough_img=None):
    m = bpy.data.materials.new(name)
    t = m.node_tree
    p = next(n for n in t.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    p.inputs["Base Color"].default_value = (*color, 1)
    p.inputs["Metallic"].default_value = metallic
    p.inputs["Roughness"].default_value = rough
    if image is not None:
        n = t.nodes.new("ShaderNodeTexImage")
        n.image = image
        t.links.new(n.outputs["Color"], p.inputs["Base Color"])
    if rough_img is not None:
        n = t.nodes.new("ShaderNodeTexImage")
        n.image = rough_img
        t.links.new(n.outputs["Color"], p.inputs["Roughness"])
    if normal is not None:
        n = t.nodes.new("ShaderNodeTexImage")
        n.image = normal
        nm = t.nodes.new("ShaderNodeNormalMap")
        t.links.new(n.outputs["Color"], nm.inputs["Color"])
        t.links.new(nm.outputs["Normal"], p.inputs["Normal"])
    return m


def build_scene():
    for c in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for it in list(c):
            c.remove(it)
    wood = img_fn("Wood", 512, lambda u, v: np.stack([0.45 + 0.15 * np.sin(40 * u + 3 * np.sin(6 * v)),
                                                       0.28 + 0.08 * np.sin(40 * u), 0.12 + 0 * u], -1))
    check = img_fn("Check", 256, lambda u, v: np.repeat(((np.floor(u * 8) + np.floor(v * 8)) % 2)[..., None], 3, -1) * 0.7 + 0.15)
    bumps = img_fn("Bumps", 256, lambda u, v: np.stack([0.5 + 0.25 * np.sin(2 * np.pi * 6 * u),
                                                        0.5 + 0.25 * np.sin(2 * np.pi * 6 * v),
                                                        0 * u + 0.85], -1), cs="Non-Color")
    rough = img_fn("RoughTex", 128, lambda u, v: np.repeat((0.2 + 0.6 * v)[..., None], 3, -1), cs="Non-Color")
    mats = [
        mat("Red", (0.8, 0.05, 0.05)), mat("Gold", (1.0, 0.75, 0.3), metallic=1.0, rough=0.3),
        mat("Wood", image=wood), mat("Checker", image=check), mat("Bumpy", (0.2, 0.4, 0.8), normal=bumps),
        mat("Steel", (0.6, 0.6, 0.65), metallic=1.0, rough_img=rough), mat("Green", (0.1, 0.6, 0.2), rough=0.8),
        mat("White", (0.9, 0.9, 0.9)), mat("Teal", (0.05, 0.5, 0.5), rough=0.2), mat("Plum", (0.4, 0.1, 0.4)),
        mat("Copper", (0.95, 0.5, 0.35), metallic=1.0, rough=0.45),
    ]
    adds = [
        lambda: bpy.ops.mesh.primitive_cube_add(size=1.6, location=(-4, 0, 0.8)),
        lambda: bpy.ops.mesh.primitive_uv_sphere_add(radius=0.9, location=(-2, 0, 0.9)),
        lambda: bpy.ops.mesh.primitive_cylinder_add(radius=0.7, depth=1.8, location=(0, 0, 0.9)),
        lambda: bpy.ops.mesh.primitive_monkey_add(size=1.6, location=(2, 0, 0.9), rotation=(0, 0, 0)),
        lambda: bpy.ops.mesh.primitive_torus_add(location=(4, 0, 0.5), major_radius=0.8, minor_radius=0.3),
    ]
    objs = []
    for i, add in enumerate(adds):
        add()
        o = bpy.context.active_object
        o.name = f"Prop{i}"
        if i == 3:
            o.rotation_euler = (math.radians(-60), 0, 0)
            o.modifiers.new("sub", "SUBSURF").levels = 1
            bpy.ops.object.shade_smooth()
        if i in (1, 4):
            bpy.ops.object.shade_smooth()
        if i == 0:
            o.data.uv_layers[0].uv.foreach_set("vector", np.repeat(
                [[0, 0], [1, 0], [1, 1], [0, 1]], 6, axis=0).astype(np.float32).ravel()[:len(o.data.loops) * 2])
        ms = [mats[2 * i], mats[2 * i + 1]] + ([mats[10]] if i == 4 else [])
        for m in ms:
            o.data.materials.append(m)
        mi = np.arange(len(o.data.polygons)) % len(ms)
        o.data.polygons.foreach_set("material_index", mi.astype(np.int32))
        o.data.update()
        objs.append(o)
    bpy.ops.object.camera_add(location=(0, -11, 4.5), rotation=(math.radians(70), 0, 0))
    bpy.context.scene.camera = bpy.context.active_object
    bpy.ops.object.light_add(type="SUN", rotation=(math.radians(40), math.radians(20), math.radians(30)))
    bpy.context.active_object.data.energy = 3.0
    world = bpy.context.scene.world or bpy.data.worlds.new("W")
    bpy.context.scene.world = world
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.25, 0.27, 0.3, 1)
    return objs


def render(path, show, hide):
    for o in show:
        o.hide_render = False
    for o in hide:
        o.hide_render = True
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 128
    sc.cycles.use_denoising = True
    sc.cycles.seed = 1
    sc.render.resolution_x, sc.render.resolution_y = W, H
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = True
    sc.view_settings.view_transform = "Standard"
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path, check_existing=False)
    a = np.empty(W * H * 4, np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    return a.reshape(H, W, 4)


def save_png(arr, path):
    h, w = arr.shape[:2]
    img = bpy.data.images.new("tmp_out", w, h, alpha=True)
    img.pixels.foreach_set(np.ascontiguousarray(arr, np.float32).ravel())
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def main():
    addon.register()
    srcs = build_scene()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "scene.blend"))
    p = bpy.context.scene.garden_atlas
    p.atlas_name = "Compare"
    p.width = "2048"
    p.height = "2048"
    p.output_dir = "//textures/"
    p.samples = 16
    for o in bpy.context.view_layer.objects:
        o.select_set(o in srcs)
    bpy.context.view_layer.objects.active = srcs[0]
    bpy.ops.garden_atlas.analyze()
    print("[CMP] analyze:", [(m.level, m.obj, m.text) for m in p.report])
    res = bpy.ops.garden_atlas.run_all()
    assert res == {"FINISHED"}, res
    n_mats = len({m.name for o in srcs for m in o.data.materials})
    results = list(bpy.data.collections["ATLAS_Compare"].objects)
    a = render(os.path.join(OUT, "source.png"), srcs, results)
    b = render(os.path.join(OUT, "result.png"), results, srcs)
    cov = (a[..., 3] > 0.99) & (b[..., 3] > 0.99)
    diff = np.abs(a[..., :3] - b[..., :3]).max(axis=2)
    d = diff[cov]
    metrics = {"objects": len(srcs), "materials": n_mats, "atlas": [2048, 2048],
               "result_objects": len(results), "mean_diff_255": round(float(d.mean()) * 255, 2),
               "p95_diff_255": round(float(np.percentile(d, 95)) * 255, 2),
               "max_diff_255": round(float(d.max()) * 255, 2),
               "alpha_mismatch_px": int(((a[..., 3] > 0.5) != (b[..., 3] > 0.5)).sum()),
               "stats": {"uv_usage": p.uv_usage, "density_avg": p.density_avg, "bake_s": p.bake_seconds}}
    side = np.concatenate([a, b], axis=1)
    save_png(side, os.path.join(OUT, "side_by_side.png"))
    heat = np.zeros_like(a)
    heat[..., 0] = np.clip(diff * 8, 0, 1)
    heat[..., 3] = 1
    save_png(heat, os.path.join(OUT, "diff.png"))
    ok = (metrics["mean_diff_255"] <= MEAN_TOL * 255 and metrics["p95_diff_255"] <= P95_TOL * 255
          and n_mats >= 10 and len(srcs) >= 5)
    metrics["verdict"] = "PASS" if ok else "FAIL"
    json.dump(metrics, open(os.path.join(OUT, "metrics.json"), "w"), indent=2)
    print("[CMP]", json.dumps(metrics))
    print("[CMP] out:", OUT)
    addon.unregister()
    if not ok:
        sys.exit(1)


main()
