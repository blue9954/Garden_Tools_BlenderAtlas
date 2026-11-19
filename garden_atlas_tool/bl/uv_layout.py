"""UVAtlas layer creation, packing and statistics (FR-20..28)."""

import math

import bpy
import numpy as np

from ..core import uvmath
from . import collect

ATLAS_UV = "UVAtlas"
SMART_ANGLE = math.radians(66.0)

# pack_islands margin (spec 13, open item, measured in IT-13 / spike pack_margin):
#  * margin_method='ADD' adds the margin on every island side *before* the final
#    scale-to-fit, so the real gap is 2 * margin * s (s = packing scale).
#  * 'FRACTION' is exact but can take minutes for many identical islands.
# We pack with ADD, measure s from the UV area, and repack with a corrected
# margin until the gap is at least the requested one.
PACK_ITERATIONS = 4
PACK_SAFETY = 1.03


def select_only(context, objs, active):
    vl = context.view_layer
    for o in vl.objects:
        if o.select_get():
            o.select_set(False)
    for o in objs:
        o.select_set(True)
    vl.objects.active = active


def _render_layer(mesh):
    return next((l for l in mesh.uv_layers if l.active_render), None)


def create_atlas_layer(obj, overlap_mode):
    """Add the UVAtlas layer. Returns True if it still needs an unwrap."""
    mesh = obj.data
    old = mesh.uv_layers.get(ATLAS_UV)
    if old is not None:
        old.name = ATLAS_UV + "_src"
    src = _render_layer(mesh)
    if src is None:
        layer = mesh.uv_layers.new(name=ATLAS_UV, do_init=False)
        mesh.uv_layers.active = mesh.uv_layers[ATLAS_UV]
        mesh.uv_layers[ATLAS_UV].active_render = True
        return True
    src_name = src.name
    data = np.empty(len(mesh.loops) * 2, np.float32)
    src.uv.foreach_get("vector", data)
    layer = mesh.uv_layers.new(name=ATLAS_UV, do_init=False)
    if layer is None:
        raise RuntimeError(f"{obj.name}: UV 레이어를 더 만들 수 없습니다 (최대 8개)")
    mesh.uv_layers[ATLAS_UV].uv.foreach_set("vector", data)
    mesh.uv_layers.active = mesh.uv_layers[ATLAS_UV]
    mesh.uv_layers[src_name].active_render = True  # FR-31: sources sample their own UV
    return overlap_mode == "REUNWRAP"


def _edit(context, objs, fn):
    select_only(context, objs, objs[0])
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        ts = context.scene.tool_settings
        ts.use_uv_select_sync = False
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.select_all(action="SELECT")
        fn()
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def unwrap(context, objs):
    _edit(context, objs, lambda: bpy.ops.uv.smart_project(
        angle_limit=SMART_ANGLE, island_margin=0.0, scale_to_bounds=False, correct_aspect=False))


def _atlas_uv(mesh):
    a = np.empty(len(mesh.loops) * 2, np.float32)
    mesh.uv_layers[ATLAS_UV].uv.foreach_get("vector", a)
    return a.reshape(-1, 2).astype(np.float64)


def _set_atlas_uv(mesh, uv):
    mesh.uv_layers[ATLAS_UV].uv.foreach_set("vector", uv.astype(np.float32).ravel())


def scale_islands(obj, face_factor):
    """Scale each UVAtlas island around its centre by the max factor of its faces."""
    mesh = obj.data
    d = collect.mesh_arrays(mesh, mesh.uv_layers[ATLAS_UV])
    uv = d["uv"]
    isl = uvmath.face_islands(d["face_start"], d["face_total"], d["loop_vert"], uv)
    n = isl.max() + 1 if len(isl) else 0
    f_isl = np.zeros(n)
    np.maximum.at(f_isl, isl, face_factor)
    loop_isl = np.repeat(isl, d["face_total"])
    for i in range(n):
        sel = loop_isl == i
        c = (uv[sel].min(axis=0) + uv[sel].max(axis=0)) * 0.5
        uv[sel] = c + (uv[sel] - c) * f_isl[i]
    _set_atlas_uv(mesh, uv)


def source_factors(obj, materials):
    """FR-22 SOURCE: per-face linear factor = texture res / largest texture res."""
    res = {k: m.image_res for k, m in materials.items()}
    top = max([r for r in res.values() if r] or [1])
    mesh = obj.data
    mi = np.empty(len(mesh.polygons), np.int64)
    mesh.polygons.foreach_get("material_index", mi)
    per_slot = []
    for slot in obj.material_slots:
        r = res.get(collect.material_key(slot.material), 0) or top
        per_slot.append(r / top)
    if not per_slot:
        return np.ones(len(mi))
    per_slot = np.asarray(per_slot)
    return per_slot[np.clip(mi, 0, len(per_slot) - 1)]


def weight_factors(obj):
    """FR-26: per-face linear factor = object weight x material weight."""
    from .duplicate import WEIGHT_PROP
    mesh = obj.data
    mi = np.empty(len(mesh.polygons), np.int64)
    mesh.polygons.foreach_get("material_index", mi)
    ow = float(obj.get(WEIGHT_PROP, 1.0))
    mw = [float(getattr(m, "garden_atlas_weight", 1.0)) if m is not None else 1.0
          for m in mesh.materials] or [1.0]
    mw = np.clip(np.asarray(mw), 0.25, 4.0)
    return np.clip(ow, 0.25, 4.0) * mw[np.clip(mi, 0, len(mw) - 1)]


def layout(context, dups, settings, materials):
    """Create, normalise and pack UVAtlas on all duplicates together."""
    need = [o for o in dups if create_atlas_layer(o, settings.overlap_mode)]
    for o in need:
        unwrap(context, [o])
    w, h = settings.width, settings.height
    if settings.density_mode == "UNIFORM" or settings.density_mode == "SOURCE":
        _edit(context, dups, lambda: bpy.ops.uv.average_islands_scale(scale_uv=False, shear=False))
    for o in dups:
        f = weight_factors(o)
        if settings.density_mode == "SOURCE":
            f = f * source_factors(o, materials)
        if np.any(np.abs(f - 1.0) > 1e-6):
            scale_islands(o, f)
    rotate = settings.allow_rotate
    if w != h:
        # pack_islands fills a square; pre-squash u so pixels stay isotropic.
        rotate = False
        for o in dups:
            uv = _atlas_uv(o.data)
            uv[:, 0] *= h / w
            _set_atlas_uv(o.data, uv)
    pack(context, dups, settings.island_gap_px / min(w, h), rotate,
         settings.overlap_mode == "KEEP_STACKED")
    return need


def _uv_area(objs):
    total = 0.0
    for o in objs:
        mesh = o.data
        mesh.calc_loop_triangles()
        tris = np.empty(len(mesh.loop_triangles) * 3, np.int64)
        mesh.loop_triangles.foreach_get("loops", tris)
        total += uvmath.tri_area_2d(_atlas_uv(mesh)[tris.reshape(-1, 3)]).sum()
    return total


def pack(context, objs, gap_uv, rotate, merge_overlap):
    """Pack all islands into 0..1 with at least ``gap_uv`` between islands.

    Returns the achieved gap in UV units.
    """
    before = {o.name: _atlas_uv(o.data) for o in objs}
    area0 = _uv_area(objs)
    margin = gap_uv * 0.5
    achieved = 0.0
    for _ in range(PACK_ITERATIONS):
        for o in objs:
            _set_atlas_uv(o.data, before[o.name])
        _edit(context, objs, lambda: bpy.ops.uv.pack_islands(
            udim_source="CLOSEST_UDIM", rotate=rotate, rotate_method="ANY", scale=True,
            merge_overlap=merge_overlap, margin_method="ADD", margin=margin,
            shape_method="CONCAVE"))
        if gap_uv <= 0 or area0 <= 0:
            return 0.0
        scale = math.sqrt(_uv_area(objs) / area0)
        achieved = 2.0 * margin * scale
        if achieved >= gap_uv:
            break
        margin = gap_uv / (2.0 * scale) * PACK_SAFETY
    return achieved


def atlas_triangles(objs):
    """(tri_uv (T,3,2), tri_area3d (T,), tri_island ids unique across objects)."""
    uvs, areas, isl_all = [], [], []
    offset = 0
    for o in objs:
        mesh = o.data
        d = collect.mesh_arrays(mesh, mesh.uv_layers[ATLAS_UV])
        tri_uv = d["uv"][d["tris"]]
        mw = np.array(o.matrix_world)
        co = d["co"] @ mw[:3, :3].T + mw[:3, 3]
        tri_co = co[d["loop_vert"][d["tris"]]]
        isl = uvmath.face_islands(d["face_start"], d["face_total"], d["loop_vert"], d["uv"])
        uvs.append(tri_uv)
        areas.append(uvmath.tri_area_3d(tri_co))
        isl_all.append(isl[d["tri_face"]] + offset)
        offset += (isl.max() + 1) if len(isl) else 0
    if not uvs:
        return np.zeros((0, 3, 2)), np.zeros(0), np.zeros(0, np.int64)
    return np.concatenate(uvs), np.concatenate(areas), np.concatenate(isl_all)


def stats(objs, width, height):
    """FR-27: UV space usage and texel density (px/m)."""
    tri_uv, area3d, _ = atlas_triangles(objs)
    area_uv = uvmath.tri_area_2d(tri_uv)
    return {
        "uv_usage": round(uvmath.uv_usage(tri_uv, 512), 4),
        "texel_density_px_m": {k: round(v, 1) for k, v in
                               uvmath.density_stats(area3d, area_uv, width, height).items()},
    }
