"""L2 integration tests. Run inside Blender:

    blender -b --factory-startup --python-exit-code 1 --python tests/integration/run_integration.py [-- IT-02 IT-05]

Builds synthetic scenes, runs the add-on headless, verifies pixels.
Writes a JSON summary to $GA_RESULT_JSON when set.
"""

import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import time
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import garden_atlas_tool as addon  # noqa: E402
from garden_atlas_tool.bl import pipeline as pl, uv_layout  # noqa: E402
from garden_atlas_tool.core import packing, uvmath  # noqa: E402
from garden_atlas_tool.core.settings import AtlasSettings  # noqa: E402

RES = 512
TOL = 2.5 / 255
OUT = tempfile.mkdtemp(prefix="ga_it_")
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class Fail(AssertionError):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


# ------------------------------------------------------------------ scene helpers
def reset():
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.images,
                 bpy.data.node_groups):
        for it in list(coll):
            coll.remove(it)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    sc = bpy.context.scene
    p = sc.garden_atlas
    for k in ("atlas_name", "density_mode", "overlap_mode", "join_result", "use_ao_bake",
              "use_emissive", "atlas_uv_only", "allow_rotate", "apply_scale", "normal_16bit",
              "export_fbx"):
        p.property_unset(k)
    p.width = str(RES)
    p.height = str(RES)
    p.output_dir = OUT
    p.samples = 4


def principled(name, color=(0.8, 0.8, 0.8), metallic=0.0, roughness=0.5, alpha=1.0,
               emission=None, strength=1.0):
    m = bpy.data.materials.new(name)
    p = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    p.inputs["Base Color"].default_value = (*color, 1.0)
    p.inputs["Metallic"].default_value = metallic
    p.inputs["Roughness"].default_value = roughness
    p.inputs["Alpha"].default_value = alpha
    if emission:
        p.inputs["Emission Color"].default_value = (*emission, 1.0)
        p.inputs["Emission Strength"].default_value = strength
    return m


def textured(name, img, uv_map="", normal_img=None):
    m = principled(name)
    t = m.node_tree
    p = next(n for n in t.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    if img is not None:
        tex = t.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.interpolation = "Linear"
        t.links.new(tex.outputs["Color"], p.inputs["Base Color"])
    if normal_img is not None:
        tn = t.nodes.new("ShaderNodeTexImage")
        tn.image = normal_img
        nm = t.nodes.new("ShaderNodeNormalMap")
        nm.uv_map = uv_map
        t.links.new(tn.outputs["Color"], nm.inputs["Color"])
        t.links.new(nm.outputs["Normal"], p.inputs["Normal"])
    return m


def image_from_fn(name, size, fn, colorspace="sRGB"):
    img = bpy.data.images.new(name, size, size, alpha=True)
    img.colorspace_settings.name = colorspace
    ys, xs = np.mgrid[0:size, 0:size]
    u = (xs + 0.5) / size
    v = (ys + 0.5) / size
    rgb = fn(u, v)
    a = np.ones((size, size, 4), np.float32)
    a[..., :3] = rgb
    img.pixels.foreach_set(a.ravel())
    img.update()
    img.pack()
    return img


def add_cube(name, loc=(0, 0, 0), size=2.0):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    o = bpy.context.active_object
    o.name = name
    return o


def add_plane(name, loc=(0, 0, 0), size=2.0):
    bpy.ops.mesh.primitive_plane_add(size=size, location=loc)
    o = bpy.context.active_object
    o.name = name
    return o


def set_materials(obj, mats, face_mat=None):
    obj.data.materials.clear()
    for m in mats:
        obj.data.materials.append(m)
    if face_mat is not None:
        obj.data.polygons.foreach_set("material_index", np.asarray(face_mat, np.int32))
        obj.data.update()


def select(objs):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def run(name, objs, op="run_all", **props):
    p = bpy.context.scene.garden_atlas
    p.atlas_name = name
    for k, v in props.items():
        setattr(p, k, v)
    select(objs)
    res = getattr(bpy.ops.garden_atlas, op)()
    msgs = [(m.level, m.obj, m.text) for m in p.report]
    check(res == {"FINISHED"}, f"{op} returned {res}: {msgs}")
    return msgs


def load_tex(name, suffix):
    path = os.path.join(OUT, f"T_{name}_{suffix}.png")
    check(os.path.isfile(path), f"missing {path}")
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    info = {"size": (w, h), "depth": img.depth, "path": path}
    bpy.data.images.remove(img)
    return a.reshape(h, w, 4), info


def sample(arr, uv, bilinear=False):
    h, w = arr.shape[:2]
    uv = np.atleast_2d(uv)
    if not bilinear:
        x = np.clip((uv[:, 0] * w).astype(int), 0, w - 1)
        y = np.clip((uv[:, 1] * h).astype(int), 0, h - 1)
        return arr[y, x]
    fx = uv[:, 0] * w - 0.5
    fy = uv[:, 1] * h - 0.5
    x0 = np.floor(fx).astype(int)
    y0 = np.floor(fy).astype(int)
    tx = (fx - x0)[:, None]
    ty = (fy - y0)[:, None]
    def px(x, y):
        return arr[np.mod(y, h), np.mod(x, w)]
    return ((px(x0, y0) * (1 - tx) + px(x0 + 1, y0) * tx) * (1 - ty)
            + (px(x0, y0 + 1) * (1 - tx) + px(x0 + 1, y0 + 1) * tx) * ty)


def result_obj(src_name):
    return bpy.data.objects[f"{src_name}_atlas"]


def face_atlas_centers(obj):
    mesh = obj.data
    uv = np.empty(len(mesh.loops) * 2, np.float32)
    mesh.uv_layers["UVAtlas"].uv.foreach_get("vector", uv)
    uv = uv.reshape(-1, 2)
    out = []
    for poly in mesh.polygons:
        out.append(uv[poly.loop_start:poly.loop_start + poly.loop_total].mean(axis=0))
    return np.array(out)


def srgb(c):
    return packing.linear_to_srgb(np.asarray(c))


def mesh_hash(obj):
    h = hashlib.sha1()
    me = obj.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    h.update(co.tobytes())
    for l in me.uv_layers:
        a = np.empty(len(me.loops) * 2, np.float32)
        l.uv.foreach_get("vector", a)
        h.update(l.name.encode())
        h.update(a.tobytes())
        h.update(bytes([l.active, l.active_render]))
    mi = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("material_index", mi)
    h.update(mi.tobytes())
    for m in me.materials:
        h.update((m.name if m else "-").encode())
        if m and m.node_tree:
            for n in m.node_tree.nodes:
                h.update(n.bl_idname.encode())
                for i in n.inputs:
                    if hasattr(i, "default_value"):
                        try:
                            h.update(repr(tuple(i.default_value)).encode())
                        except TypeError:
                            h.update(repr(i.default_value).encode())
            for lk in m.node_tree.links:
                h.update(f"{lk.from_node.name}.{lk.from_socket.identifier}>{lk.to_node.name}.{lk.to_socket.identifier}".encode())
    h.update(repr([tuple(r) for r in obj.matrix_world]).encode())
    h.update(repr(len(obj.modifiers)).encode())
    return h.hexdigest()


def assert_colors(name, obj_src_mats, expect_fn, tex="BC", tol=TOL):
    arr, _ = load_tex(name, tex)
    for src, face_mat in obj_src_mats:
        dup = result_obj(src)
        centers = face_atlas_centers(dup)
        vals = sample(arr, centers)
        for fi, (v, mi) in enumerate(zip(vals, face_mat)):
            exp = expect_fn(src, mi)
            diff = np.abs(v[:len(exp)] - exp).max()
            check(diff <= tol, f"{src} face {fi} mat {mi}: got {np.round(v * 255)} exp {np.round(np.asarray(exp) * 255)}")


# ------------------------------------------------------------------ tests
@test
def IT_01_register_roundtrip():
    addon.unregister()
    check(not hasattr(bpy.types.Scene, "garden_atlas"), "Scene.garden_atlas left after unregister")
    for cls in ("GARDENATLAS_OT_run_all", "GARDENATLAS_PT_atlas", "GARDENATLAS_PG_settings"):
        check(not hasattr(bpy.types, cls), f"{cls} left after unregister")
    addon.register()
    check(hasattr(bpy.types.Scene, "garden_atlas"), "re-register failed")
    check(hasattr(bpy.ops.garden_atlas, "run_all"), "operator missing")


COLORS = [(1.0, 0.0, 0.0), (0.0, 0.5, 0.0), (0.2, 0.3, 1.0)]
FACE_MAT = [0, 0, 1, 1, 2, 2]


@test
def IT_02_one_object_three_materials():
    reset()
    o = add_cube("Box")
    mats = [principled(f"C{i}", c) for i, c in enumerate(COLORS)]
    set_materials(o, mats, FACE_MAT)
    run("IT02", [o])
    assert_colors("IT02", [("Box", FACE_MAT)], lambda s, mi: srgb(COLORS[mi]))


@test
def IT_03_three_objects():
    reset()
    objs = []
    for i in range(3):
        o = add_cube(f"Obj{i}", loc=(i * 3, 0, 0), size=1 + i)
        set_materials(o, [principled(f"M{i}", COLORS[i])])
        objs.append(o)
    run("IT03", objs)
    dups = [result_obj(f"Obj{i}") for i in range(3)]
    tri_uv, _, isl = uv_layout.atlas_triangles(dups)
    check(tri_uv.min() >= -1e-6 and tri_uv.max() <= 1 + 1e-6, f"UV outside 0..1: {tri_uv.min()} {tri_uv.max()}")
    obj_id = np.concatenate([np.full(len(d.data.loop_triangles), i) for i, d in enumerate(dups)])
    d, _ = uvmath.min_island_distance(tri_uv, obj_id)
    check(d * RES >= 1.0, f"objects overlap/touch in atlas: {d * RES:.2f}px")
    assert_colors("IT03", [(f"Obj{i}", [0] * 6) for i in range(3)],
                  lambda s, mi: srgb(COLORS[int(s[-1])]))


METALS = [0.0, 0.5, 1.0]


@test
def IT_04_metallic():
    reset()
    o = add_cube("Metal")
    mats = [principled(f"Me{i}", (1.0, 0.0, 0.0), metallic=m, roughness=0.2 + 0.3 * i)
            for i, m in enumerate(METALS)]
    set_materials(o, mats, FACE_MAT)
    run("IT04", [o])
    assert_colors("IT04", [("Metal", FACE_MAT)],
                  lambda s, mi: [1.0, 0.2 + 0.3 * mi, METALS[mi]], tex="ORM")
    assert_colors("IT04", [("Metal", FACE_MAT)], lambda s, mi: srgb((1.0, 0.0, 0.0)))


def _periodic_img():
    return image_from_fn("SrcTex", 128, lambda u, v: np.stack([
        0.5 + 0.4 * np.sin(2 * np.pi * u), 0.5 + 0.4 * np.cos(2 * np.pi * v),
        0.3 + 0.2 * np.sin(2 * np.pi * (u + v))], axis=-1))


def _resample_check(name, src_obj, dup, src_arr, tol_mean=3 / 255, tol_p95=8 / 255):
    """Bake result sampled via UVAtlas must match source sampled via source UV."""
    bc, _ = load_tex(name, "BC")
    sm, dm = src_obj.data, dup.data
    suv = np.empty(len(sm.loops) * 2, np.float32)
    sm.uv_layers[0].uv.foreach_get("vector", suv)
    auv = np.empty(len(dm.loops) * 2, np.float32)
    dm.uv_layers["UVAtlas"].uv.foreach_get("vector", auv)
    suv, auv = suv.reshape(-1, 2), auv.reshape(-1, 2)
    sm.calc_loop_triangles()
    tris = np.array([t.loops[:] for t in sm.loop_triangles])
    rng = np.random.default_rng(1)
    bary = rng.dirichlet((3, 3, 3), size=200)
    bary = bary[bary.min(axis=1) > 0.12]
    errs = []
    for t in tris:
        sp = bary @ suv[t]
        ap = bary @ auv[t]
        errs.append(np.abs(sample(src_arr, sp, True)[:, :3] - sample(bc, ap, True)[:, :3]).max(axis=1))
    errs = np.concatenate(errs)
    check(errs.mean() <= tol_mean and np.percentile(errs, 95) <= tol_p95,
          f"resample mismatch mean={errs.mean() * 255:.2f} p95={np.percentile(errs, 95) * 255:.2f} (/255)")
    return errs


def _img_arr(img):
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    return a.reshape(h, w, 4)


@test
def IT_05_image_texture_rotation():
    reset()
    img = _periodic_img()
    objs = []
    for i in range(3):
        o = add_plane(f"Tex{i}", loc=(i * 3, 0, 0), size=1 + 0.7 * i)
        o.rotation_euler = (0, 0, 0.3 * i)
        set_materials(o, [textured(f"T{i}", img)])
        objs.append(o)
    run("IT05", objs, allow_rotate=True)
    src = _img_arr(img)
    for o in objs:
        _resample_check("IT05", o, result_obj(o.name), src)


def _tangent_frame(p, uv):
    e1, e2 = p[1] - p[0], p[2] - p[0]
    d1, d2 = uv[1] - uv[0], uv[2] - uv[0]
    r = 1.0 / (d1[0] * d2[1] - d2[0] * d1[1])
    t = (e1 * d2[1] - e2 * d1[1]) * r
    b = (e2 * d1[0] - e1 * d2[0]) * r
    return t / np.linalg.norm(t), b / np.linalg.norm(b)


@test
def IT_06_normal_map_rotated_island():
    reset()
    n_ts = np.array([0.3, 0.2, 0.93])
    n_ts /= np.linalg.norm(n_ts)
    nimg = image_from_fn("NrmTex", 32, lambda u, v: np.broadcast_to(0.5 + 0.5 * n_ts, u.shape + (3,)),
                         colorspace="Non-Color")
    o = add_plane("Nrm")
    set_materials(o, [textured("NM", None, uv_map="UVMap", normal_img=nimg)])
    run("IT06", [o], op="build_uv", allow_rotate=False)
    dup = result_obj("Nrm")
    me = dup.data
    uv = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers["UVAtlas"].uv.foreach_get("vector", uv)
    uv = uv.reshape(-1, 2) - 0.5
    uv = np.column_stack([-uv[:, 1], uv[:, 0]]) + 0.5  # rotate island 90 degrees
    me.uv_layers["UVAtlas"].uv.foreach_set("vector", uv.ravel().astype(np.float32))
    run("IT06", [o], op="bake")
    narr, _ = load_tex("IT06", "N")
    # world normal from source tangent frame (plane: T=+X, B=+Y, N=+Z)
    n_world = n_ts[0] * np.array([1, 0, 0]) + n_ts[1] * np.array([0, 1, 0]) + n_ts[2] * np.array([0, 0, 1])
    me.calc_loop_triangles()
    co = np.array([v.co[:] for v in me.vertices])
    auv = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers["UVAtlas"].uv.foreach_get("vector", auv)
    auv = auv.reshape(-1, 2)
    lv = [l.vertex_index for l in me.loops]
    for tri in me.loop_triangles:
        p = co[[lv[i] for i in tri.loops]]
        t_, b_ = _tangent_frame(p, auv[list(tri.loops)])
        exp = np.array([0.5 + 0.5 * n_world @ t_, 0.5 - 0.5 * n_world @ b_, 0.5 + 0.5 * n_world[2]])
        c = auv[list(tri.loops)].mean(axis=0)
        got = sample(narr, c)[0, :3]
        check(np.abs(got - exp).max() <= 4 / 255,
              f"normal got {np.round(got * 255)} exp {np.round(exp * 255)} (T'={np.round(t_, 2)})")
    # the rotation moved the X tilt to the green channel
    check(abs(got[1] - 0.5) > 0.1, "rotation did not move the tilt into G")


@test
def IT_07_tiling():
    reset()
    img = _periodic_img()
    o = add_plane("Tile")
    me = o.data
    uv = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers[0].uv.foreach_get("vector", uv)
    me.uv_layers[0].uv.foreach_set("vector", uv * 3)
    set_materials(o, [textured("TileMat", img)])
    msgs = run("IT07", [o])
    check(any("타일링 3회" in t for _, _, t in msgs), f"no tiling warning: {msgs}")
    _resample_check("IT07", o, result_obj("Tile"), _img_arr(img), tol_mean=6 / 255, tol_p95=16 / 255)


@test
def IT_08_no_uv():
    reset()
    o = add_cube("NoUV")
    o.data.uv_layers.remove(o.data.uv_layers[0])
    set_materials(o, [principled(f"C{i}", c) for i, c in enumerate(COLORS)], FACE_MAT)
    run("IT08", [o])
    assert_colors("IT08", [("NoUV", FACE_MAT)], lambda s, mi: srgb(COLORS[mi]))


@test
def IT_09_source_untouched():
    reset()
    img = _periodic_img()
    a = add_cube("SrcA")
    a.scale = (2, 1, -1)
    a.modifiers.new("bevel", "BEVEL")
    set_materials(a, [principled("SA", COLORS[0]), textured("SB", img)], [0, 1, 0, 1, 0, 1])
    b = add_plane("SrcB", loc=(4, 0, 0))
    set_materials(b, [principled("SC", COLORS[2], alpha=0.5)])
    before = {o.name: mesh_hash(o) for o in (a, b)}
    img_hash = hashlib.sha1(_img_arr(img).tobytes()).hexdigest()
    run("IT09", [a, b])
    after = {o.name: mesh_hash(o) for o in (a, b)}
    check(before == after, "source data changed")
    check(hashlib.sha1(_img_arr(img).tobytes()).hexdigest() == img_hash, "source image changed")


@test
def IT_10_forced_failure_rollback():
    reset()
    a = add_cube("FailA")
    b = add_cube("FailB", loc=(3, 0, 0))
    set_materials(a, [principled("FA", COLORS[0])])
    set_materials(b, [principled("FB", COLORS[1])])
    sc = bpy.context.scene
    engine = sc.render.engine
    samples = sc.cycles.samples
    select([b, a])
    n_obj, n_mesh, n_mat, n_img = (len(bpy.data.objects), len(bpy.data.meshes),
                                   len(bpy.data.materials), len(bpy.data.images))
    sc.garden_atlas.atlas_name = "IT10"
    s = AtlasSettings.from_object(sc.garden_atlas)
    for stage in ("UV 레이아웃", "베이크: Normal", "결과 적용"):
        pipe = pl.Pipeline(bpy.context, s, pl.MODE_ALL, sources=[b, a], fail_at=stage)
        try:
            pipe.run()
            raise Fail("pipeline did not fail")
        except pl.PipelineError:
            pass
        check((len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials), len(bpy.data.images))
              == (n_obj, n_mesh, n_mat, n_img),
              f"[{stage}] leftover data: objs {[o.name for o in bpy.data.objects]} "
              f"mats {[m.name for m in bpy.data.materials]} imgs {[i.name for i in bpy.data.images]}")
        check("ATLAS_IT10" not in bpy.data.collections, f"[{stage}] collection left")
        check(sc.render.engine == engine and sc.cycles.samples == samples, f"[{stage}] render settings not restored")
        check([o.name for o in bpy.context.selected_objects] and set(o.name for o in bpy.context.selected_objects) == {"FailA", "FailB"}
              and bpy.context.view_layer.objects.active == b, f"[{stage}] selection not restored")
        check(not [f for f in os.listdir(OUT) if f.startswith("T_IT10")], f"[{stage}] files left")
    # cancel (ESC) path: close the generator mid-bake
    pipe = pl.Pipeline(bpy.context, s, pl.MODE_ALL, sources=[b, a])
    gen = pipe.steps()
    for label, _ in gen:
        if label.startswith("베이크: Roughness"):
            break
    gen.close()
    check(len(bpy.data.objects) == n_obj and len(bpy.data.materials) == n_mat, "cancel left data")


@test
def IT_11_files():
    reset()
    o = add_cube("Files")
    set_materials(o, [principled("FI", COLORS[0])])
    p = bpy.context.scene.garden_atlas
    p.width = "1024"
    p.height = "512"
    run("IT11", [o])
    for suf, depth in (("BC", 24), ("N", 24), ("ORM", 24)):
        arr, info = load_tex("IT11", suf)
        check(info["size"] == (1024, 512), f"{suf} size {info['size']}")
        check(info["depth"] == depth, f"{suf} depth {info['depth']}")
    check(not os.path.exists(os.path.join(OUT, "T_IT11_E.png")), "E written without emission")
    mat = bpy.data.materials["M_IT11"]
    cs = {n.label: n.image.colorspace_settings.name for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeTexImage"}
    check(cs == {"BC": "sRGB", "ORM": "Non-Color", "N": "Non-Color"}, f"colorspaces {cs}")
    man = json.load(open(os.path.join(OUT, "IT11_atlas.json"), encoding="utf-8"))
    check(man["resolution"] == [1024, 512] and set(man["textures"]) == {"BC", "N", "ORM"}, f"manifest {man}")
    check(man["textures"]["N"]["convention"] == "DirectX", "manifest normal convention")
    from garden_atlas_tool.core.version import tool_version
    check(man["tool_version"] == tool_version() != "unknown", f"tool_version {man.get('tool_version')}")


@test
def IT_12_result_material():
    reset()
    o = add_cube("Res")
    set_materials(o, [principled(f"C{i}", c) for i, c in enumerate(COLORS)], FACE_MAT)
    run("IT12", [o])
    d = result_obj("Res")
    check(len(d.material_slots) == 1 and d.material_slots[0].material.name == "M_IT12", "slots")
    mi = np.empty(len(d.data.polygons), np.int32)
    d.data.polygons.foreach_get("material_index", mi)
    check((mi == 0).all(), "material_index not 0")
    check([l.name for l in d.data.uv_layers] == ["UVAtlas"], f"uv layers {[l.name for l in d.data.uv_layers]}")
    check(d.users_collection[0].name == "ATLAS_IT12", "collection")


@test
def IT_13_island_gap():
    reset()
    objs = []
    for i in range(4):
        o = add_cube(f"Gap{i}", loc=(i * 3, 0, 0))
        set_materials(o, [principled(f"G{i}", COLORS[i % 3])])
        objs.append(o)
    p = bpy.context.scene.garden_atlas
    gap = p.island_gap_px
    run("IT13", objs, op="build_uv")
    dups = [result_obj(o.name) for o in objs]
    tri_uv, _, isl = uv_layout.atlas_triangles(dups)
    d, pair = uvmath.min_island_distance(tri_uv, isl)
    check(d * RES >= gap - 0.5, f"min island distance {d * RES:.2f}px < gap {gap}px")
    print(f"    IT-13 gap setting {gap}px, measured min {d * RES:.2f}px")


@test
def IT_14_complex_shader():
    reset()
    m = bpy.data.materials.new("Mixed")
    t = m.node_tree
    p1 = next(n for n in t.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    p1.inputs["Base Color"].default_value = (1.0, 0.0, 0.0, 1.0)
    p2 = t.nodes.new("ShaderNodeBsdfPrincipled")
    p2.inputs["Base Color"].default_value = (0.0, 0.0, 1.0, 1.0)
    mix = t.nodes.new("ShaderNodeMixShader")
    mix.inputs["Fac"].default_value = 0.5
    out = next(n for n in t.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
    t.links.new(p1.outputs[0], mix.inputs[1])
    t.links.new(p2.outputs[0], mix.inputs[2])
    t.links.new(mix.outputs[0], out.inputs["Surface"])
    a = add_cube("Cx")
    set_materials(a, [m, principled("Simple", (0.0, 1.0, 0.0), metallic=1.0)], [0, 0, 0, 1, 1, 1])
    msgs = run("IT14", [a])
    check(any("근사" in tx for _, _, tx in msgs), f"no approximation warning {msgs}")
    bc, _ = load_tex("IT14", "BC")
    c = face_atlas_centers(result_obj("Cx"))
    v = sample(bc, c)
    for fi, mi in enumerate([0, 0, 0, 1, 1, 1]):
        if mi == 0:
            check(v[fi, 0] > 0.4 and v[fi, 2] > 0.4 and v[fi, 1] < 0.1, f"mixed face {np.round(v[fi] * 255)}")
        else:
            check(np.abs(v[fi, :3] - [0, 1, 0]).max() <= TOL, f"simple face {np.round(v[fi] * 255)}")
    orm, _ = load_tex("IT14", "ORM")
    o = sample(orm, c)
    check(o[0, 2] <= TOL and o[3, 2] >= 1 - TOL, f"metallic {o[:, 2]}")


@test
def IT_15_rerun_same_name():
    reset()
    o = add_cube("Again")
    set_materials(o, [principled("AG", COLORS[0])])
    run("IT15", [o])
    run("IT15", [o])
    check(len(bpy.data.collections["ATLAS_IT15"].objects) == 1, "duplicate objects")
    names = [x.name for x in list(bpy.data.objects) + list(bpy.data.materials) + list(bpy.data.images)
             + list(bpy.data.meshes) + list(bpy.data.collections)]
    check(not [n for n in names if ".00" in n], f"name collisions {names}")
    check(sum(1 for i in bpy.data.images if i.name.startswith("T_IT15")) == 3, "images")
    check(not [m.name for m in bpy.data.materials if m.name.startswith("__ga")], "temp materials left")


# ---------------------------------------------------------------- extra coverage
@test
def EX_opacity_emissive_ao_join():
    reset()
    a = add_cube("OpA")
    set_materials(a, [principled("Glass", (0.0, 0.0, 1.0), alpha=0.25),
                      principled("Lamp", (0.1, 0.1, 0.1), emission=(1.0, 0.5, 0.0), strength=1.0)],
                  [0, 0, 0, 1, 1, 1])
    b = add_cube("OpB", loc=(3, 0, 0))
    set_materials(b, [principled("Solid", (1.0, 1.0, 1.0))])
    run("EX1", [a, b], use_emissive=True, use_ao_bake=True, join_result=True)
    bc, info = load_tex("EX1", "BC")
    check(info["depth"] == 32, f"BC should be RGBA, depth {info['depth']}")
    e, _ = load_tex("EX1", "E")
    j = bpy.data.objects.get("SM_EX1")
    check(j is not None and len(bpy.data.collections["ATLAS_EX1"].objects) == 1, "join")
    c = face_atlas_centers(j)
    vb = sample(bc, c)
    ve = sample(e, c)
    alphas = sorted(np.round(vb[:, 3] * 255).astype(int).tolist())
    check(alphas.count(64) == 3 or abs(alphas[0] - 64) <= 2, f"alpha {alphas}")
    check(np.abs(ve[:, :3] - srgb((1.0, 0.5, 0.0))).max(axis=1).min() <= TOL, f"emission {np.round(ve * 255)}")
    orm, _ = load_tex("EX1", "ORM")
    check(sample(orm, c)[:, 0].min() < 0.99, "AO baked nothing (all white)")
    man = json.load(open(os.path.join(OUT, "EX1_atlas.json"), encoding="utf-8"))
    check(man["textures"]["BC"]["channels"] == "RGBA" and "E" in man["textures"], f"manifest {man['textures']}")


@test
def EX_modes_source_stacked_nonsquare():
    reset()
    big = image_from_fn("Big", 256, lambda u, v: np.stack([u, v, u * 0 + 0.5], -1))
    small = image_from_fn("Small", 64, lambda u, v: np.stack([v, u, u * 0 + 0.5], -1))
    a = add_cube("SrcBig")
    set_materials(a, [textured("BigM", big)])
    b = add_cube("SrcSmall", loc=(3, 0, 0))
    set_materials(b, [textured("SmallM", small)])
    p = bpy.context.scene.garden_atlas
    run("EX2", [a, b], op="build_uv", density_mode="SOURCE")
    st = uv_layout.stats([result_obj("SrcBig"), result_obj("SrcSmall")], RES, RES)
    da = uv_layout.stats([result_obj("SrcBig")], RES, RES)["texel_density_px_m"]["avg"]
    db = uv_layout.stats([result_obj("SrcSmall")], RES, RES)["texel_density_px_m"]["avg"]
    check(3.0 < da / db < 5.0, f"SOURCE density ratio {da / db:.2f} (expect ~4)")
    check(st["uv_usage"] > 0.1, f"usage {st}")
    # mirrored geometry stacked UVs
    reset()
    c = add_plane("Mir")
    c.modifiers.new("m", "MIRROR").use_axis[0] = True
    c.location.x = 1.5
    set_materials(c, [principled("MirM", COLORS[1])])
    run("EX3", [c], op="build_uv", overlap_mode="KEEP_STACKED")
    tri, _, isl = uv_layout.atlas_triangles([result_obj("Mir")])
    check(uvmath.overlap_fraction(tri, isl) > 0.5, "KEEP_STACKED did not keep overlap")
    reset()
    c = add_plane("Mir")
    c.modifiers.new("m", "MIRROR").use_axis[0] = True
    c.location.x = 1.5
    set_materials(c, [principled("MirM", COLORS[1])])
    run("EX4", [c], op="build_uv", overlap_mode="SEPARATE")
    tri, _, isl = uv_layout.atlas_triangles([result_obj("Mir")])
    check(uvmath.overlap_fraction(tri, isl) < 0.01, "SEPARATE left overlap")
    # non-square atlas keeps pixels isotropic
    reset()
    d = add_plane("Wide")
    set_materials(d, [principled("WM", COLORS[0])])
    p.width = "1024"
    run("EX5", [d])
    me = result_obj("Wide").data
    uv = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers["UVAtlas"].uv.foreach_get("vector", uv)
    uv = uv.reshape(-1, 2)
    ext = (uv.max(0) - uv.min(0)) * [1024, RES]
    check(abs(ext[0] / ext[1] - 1.0) < 0.02, f"non-square pixel aspect {ext}")


@test
def EX_unscaled_object_density():
    """Spec 13 open item: is unapplied object scale reflected? (apply_scale on/off)."""
    reset()
    a = add_cube("ScA")
    a.scale = (3, 3, 3)
    b = add_cube("ScB", loc=(6, 0, 0))
    set_materials(a, [principled("SA", COLORS[0])])
    set_materials(b, [principled("SB", COLORS[1])])
    run("EX6", [a, b], op="build_uv", apply_scale=True)
    da = uv_layout.stats([result_obj("ScA")], RES, RES)["texel_density_px_m"]["avg"]
    db = uv_layout.stats([result_obj("ScB")], RES, RES)["texel_density_px_m"]["avg"]
    check(abs(da / db - 1) < 0.05, f"apply_scale=True density ratio {da / db:.3f}")
    reset()
    a = add_cube("ScA")
    a.scale = (3, 3, 3)
    b = add_cube("ScB", loc=(6, 0, 0))
    set_materials(a, [principled("SA", COLORS[0])])
    set_materials(b, [principled("SB", COLORS[1])])
    run("EX7", [a, b], op="build_uv", apply_scale=False)
    da = uv_layout.stats([result_obj("ScA")], RES, RES)["texel_density_px_m"]["avg"]
    db = uv_layout.stats([result_obj("ScB")], RES, RES)["texel_density_px_m"]["avg"]
    print(f"    apply_scale=False density ratio {da / db:.3f} (1.0 = object scale honoured)")


@test
def M3_capacity_warning():
    reset()
    big = image_from_fn("Big4k", 2048, lambda u, v: np.stack([u, v, u * 0], -1))
    objs = []
    for i in range(3):
        o = add_plane(f"Cap{i}", loc=(i * 3, 0, 0))
        set_materials(o, [textured(f"CapM{i}", big)])
        objs.append(o)
    p = bpy.context.scene.garden_atlas
    p.atlas_name = "CAP"
    select(objs)
    bpy.ops.garden_atlas.analyze()
    msgs = [m.text for m in p.report]
    check(any("분할" in t for t in msgs), f"no capacity warning at 512: {msgs}")
    print("    " + next(t for t in msgs if "분할" in t))
    p.width = "8192"
    p.height = "8192"
    bpy.ops.garden_atlas.analyze()
    check(not any("분할" in m.text for m in p.report), "capacity warning at 8192")


@test
def P2_weights():
    """FR-26: object and material weights scale texel density (linear)."""
    reset()
    a = add_cube("WA")
    b = add_cube("WB", loc=(3, 0, 0))
    c = add_cube("WC", loc=(6, 0, 0))
    set_materials(a, [principled("WMa", COLORS[0])])
    mb = principled("WMb", COLORS[1])
    set_materials(b, [mb])
    set_materials(c, [principled("WMc", COLORS[2])])
    a.garden_atlas_weight = 2.0
    mb.garden_atlas_weight = 0.5
    run("P2W", [a, b, c], op="build_uv")
    den = {n: uv_layout.stats([result_obj(n)], RES, RES)["texel_density_px_m"]["avg"] for n in ("WA", "WB", "WC")}
    ra, rb = den["WA"] / den["WC"], den["WB"] / den["WC"]
    print(f"    weights: obj 2.0 -> x{ra:.2f}, mat 0.5 -> x{rb:.2f}")
    check(abs(ra - 2.0) < 0.1 and abs(rb - 0.5) < 0.05, f"weight ratios {ra:.3f} {rb:.3f}")
    check(a.garden_atlas_weight == 2.0, "source weight changed")


def _png_bit_depth(path):
    with open(path, "rb") as f:
        head = f.read(26)
    return head[24]


@test
def P2_normal16_fbx():
    reset()
    a = add_cube("Fx")
    b = add_cube("Fy", loc=(3, 0, 0))
    set_materials(a, [principled("FxM", COLORS[0])])
    set_materials(b, [principled("FyM", COLORS[1])])
    run("P2F", [a, b], normal_16bit=True, export_fbx=True, join_result=True)
    npath = os.path.join(OUT, "T_P2F_N.png")
    check(_png_bit_depth(npath) == 16, f"N bit depth {_png_bit_depth(npath)}")
    check(_png_bit_depth(os.path.join(OUT, "T_P2F_BC.png")) == 8, "BC should stay 8-bit")
    img = bpy.data.images.load(npath, check_existing=False)
    img.colorspace_settings.name = "Non-Color"
    img.reload()
    n = _img_arr(img)
    bpy.data.images.remove(img)
    c = face_atlas_centers(bpy.data.objects["SM_P2F"])
    v = sample(n, c)[:, :3]
    check(np.abs(v - [0.5, 0.5, 1.0]).max() < 0.001, f"16-bit flat normal {v[:2]} (8-bit would be 0.50196)")
    fbx = os.path.join(OUT, "SM_P2F.fbx")
    check(os.path.isfile(fbx), "fbx missing")
    man = json.load(open(os.path.join(OUT, "P2F_atlas.json"), encoding="utf-8"))
    check(man.get("fbx") == "SM_P2F.fbx" and man["textures"]["N"]["bit_depth"] == 16, f"manifest {man}")
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=fbx)
    new = [o for o in bpy.data.objects if o not in before]
    check(len(new) == 1, f"fbx objects {[o.name for o in new]}")
    me = new[0].data
    check(len(new[0].material_slots) == 1, f"fbx slots {len(new[0].material_slots)}")
    check([l.name for l in me.uv_layers] == ["UVAtlas"], f"fbx uv {[l.name for l in me.uv_layers]}")
    check(len(me.polygons) == 12, f"fbx polys {len(me.polygons)}")


@test
def P2_preset_ops():
    reset()
    p = bpy.context.scene.garden_atlas
    path = os.path.join(OUT, "preset.json")
    p.atlas_name = "Keep"
    p.width = "2048"
    p.island_gap_px = 20
    p.samples = 12
    p.density_mode = "SOURCE"
    p.export_fbx = True
    check(bpy.ops.garden_atlas.preset_save(filepath=path) == {"FINISHED"}, "save")
    p.width = "512"
    p.island_gap_px = 8
    p.samples = 4
    p.density_mode = "UNIFORM"
    p.export_fbx = False
    p.atlas_name = "Other"
    check(bpy.ops.garden_atlas.preset_load(filepath=path) == {"FINISHED"}, "load")
    got = (p.width, p.island_gap_px, p.samples, p.density_mode, p.export_fbx, p.atlas_name)
    check(got == ("2048", 20, 12, "SOURCE", True, "Other"), f"preset values {got}")
    with open(path, "w") as f:
        f.write('{"kind": "nope"}')
    try:  # an operator reporting ERROR raises in background mode
        r = bpy.ops.garden_atlas.preset_load(filepath=path)
    except RuntimeError as e:
        r = {"CANCELLED"}
        check("프리셋" in str(e), f"unexpected error {e}")
    check(r == {"CANCELLED"}, "bad preset accepted")
    check(p.width == "2048", "bad preset changed settings")


# ------------------------------------------------------------------ main
def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    want = {a.replace("-", "_") for a in argv}
    addon.register()
    device = os.environ.get("GA_DEVICE", "CPU").upper()
    if device != "CPU":
        cp = bpy.context.preferences.addons["cycles"].preferences
        cp.compute_device_type = device  # METAL / CUDA / OPTIX / HIP / ONEAPI
        cp.get_devices()
        used = [d.name for d in cp.devices if d.type == device and setattr(d, "use", True) is None]
        check(used, f"no {device} device")
        bpy.context.scene.cycles.device = "GPU"
        print(f"    device {device}: {used}")
    results = []
    for fn in TESTS:
        tid = fn.__name__
        if want and not any(tid.startswith(w) for w in want):
            continue
        t0 = time.perf_counter()
        try:
            fn()
            status, msg = "PASS", ""
        except Fail as e:
            status, msg = "FAIL", str(e)
        except Exception as e:
            status, msg = "ERROR", f"{type(e).__name__}: {e}"
            traceback.print_exc()
        dt = time.perf_counter() - t0
        results.append({"id": tid, "status": status, "msg": msg, "seconds": round(dt, 1)})
        print(f"[{status}] {tid} ({dt:.1f}s) {msg}", flush=True)
    addon.unregister()
    shutil.rmtree(OUT, ignore_errors=True)
    out = os.environ.get("GA_RESULT_JSON")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    bad = [r for r in results if r["status"] != "PASS"]
    print(f"L2 integration: {len(results) - len(bad)}/{len(results)} passed")
    if bad:
        sys.exit(1)


main()
