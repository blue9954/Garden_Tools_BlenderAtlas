"""Target collection, material classification and inspection (FR-01..06)."""

from dataclasses import dataclass, field

import bpy
import numpy as np

from ..core import uvmath
from ..core.report import Report

SIMPLE = "simple"
COMPLEX = "complex"
NODELESS = "nodeless"
EMPTY = "empty"

TILING_TRI_LIMIT = 50000


@dataclass
class MaterialInfo:
    name: str
    cls: str
    has_alpha: bool = False
    has_emission: bool = False
    emission_max: float = 0.0
    image_res: int = 0          # largest image texture side (0 = none)


@dataclass
class Inspection:
    targets: list = field(default_factory=list)       # bpy objects
    materials: dict = field(default_factory=dict)     # key -> MaterialInfo (key = name or "" for empty)
    report: Report = field(default_factory=Report)
    source_uv_area: dict = field(default_factory=dict)  # material key -> UV area used (tiles)

    @property
    def has_alpha(self):
        return any(m.has_alpha for m in self.materials.values())

    @property
    def has_emission(self):
        return any(m.has_emission for m in self.materials.values())

    def classes(self):
        return {m.cls for m in self.materials.values()}


def material_key(mat):
    return mat.name_full if mat is not None else ""


# ------------------------------------------------------------------ nodes
def _skip_reroute(link):
    node = link.from_node
    sock = link.from_socket
    while node.bl_idname == "NodeReroute":
        inp = node.inputs[0]
        if not inp.is_linked:
            return None, None
        lk = inp.links[0]
        node, sock = lk.from_node, lk.from_socket
    return node, sock


def output_node(mat):
    tree = mat.node_tree
    if tree is None:
        return None
    node = tree.get_output_node("CYCLES")
    if node is None:
        node = tree.get_output_node("ALL")
    return node


def surface_source(mat):
    """(node, socket) feeding the active Material Output's Surface, or (None, None)."""
    out = output_node(mat)
    if out is None:
        return None, None
    surf = out.inputs.get("Surface")
    if surf is None or not surf.is_linked:
        return None, None
    return _skip_reroute(surf.links[0])


def find_principled(mat):
    node, _ = surface_source(mat)
    if node is not None and node.bl_idname == "ShaderNodeBsdfPrincipled":
        return node
    return None


def classify(mat):
    if mat is None:
        return EMPTY
    if mat.node_tree is None or output_node(mat) is None:
        return NODELESS
    node, _ = surface_source(mat)
    if node is None:
        return NODELESS
    if node.bl_idname == "ShaderNodeBsdfPrincipled":
        return SIMPLE
    return COMPLEX


def _iter_nodes(tree, seen=None):
    seen = seen if seen is not None else set()
    if tree is None or tree.as_pointer() in seen:
        return
    seen.add(tree.as_pointer())
    for n in tree.nodes:
        yield n
        if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None:
            yield from _iter_nodes(n.node_tree, seen)


def _socket_value(sock):
    v = sock.default_value
    try:
        return tuple(v)
    except TypeError:
        return (float(v),)


def _principled_emission(p):
    col = p.inputs["Emission Color"]
    st = p.inputs["Emission Strength"]
    strength = float(st.default_value) if not st.is_linked else 1.0
    col_on = col.is_linked or any(c > 1e-6 for c in _socket_value(col)[:3])
    on = col_on and (st.is_linked or strength > 1e-6)
    return on, (strength if on else 0.0)


def material_info(mat):
    cls = classify(mat)
    info = MaterialInfo(name=mat.name_full if mat else "", cls=cls)
    if mat is None or mat.node_tree is None:
        return info
    for n in _iter_nodes(mat.node_tree):
        if n.bl_idname == "ShaderNodeTexImage" and n.image is not None:
            info.image_res = max(info.image_res, *n.image.size)
    if cls == SIMPLE:
        p = find_principled(mat)
        a = p.inputs["Alpha"]
        info.has_alpha = a.is_linked or float(a.default_value) < 0.999
        info.has_emission, info.emission_max = _principled_emission(p)
    elif cls == COMPLEX:
        for n in _iter_nodes(mat.node_tree):
            if n.bl_idname == "ShaderNodeEmission":
                info.has_emission = True
                st = n.inputs["Strength"]
                info.emission_max = max(info.emission_max, 1.0 if st.is_linked else float(st.default_value))
            elif n.bl_idname == "ShaderNodeBsdfPrincipled":
                on, s = _principled_emission(n)
                if on:
                    info.has_emission = True
                    info.emission_max = max(info.emission_max, s)
    return info


# ------------------------------------------------------------------ mesh data
def mesh_arrays(mesh, layer=None):
    """Flat arrays used by core.uvmath. layer: UV layer (default active_render)."""
    nf = len(mesh.polygons)
    nl = len(mesh.loops)
    fs = np.empty(nf, np.int64)
    ft = np.empty(nf, np.int64)
    mi = np.empty(nf, np.int64)
    mesh.polygons.foreach_get("loop_start", fs)
    mesh.polygons.foreach_get("loop_total", ft)
    mesh.polygons.foreach_get("material_index", mi)
    lv = np.empty(nl, np.int64)
    mesh.loops.foreach_get("vertex_index", lv)
    if layer is None:
        layer = next((l for l in mesh.uv_layers if l.active_render), None)
    uv = None
    if layer is not None:
        uv = np.empty(nl * 2, np.float32)
        layer.uv.foreach_get("vector", uv)
        uv = uv.reshape(-1, 2).astype(np.float64)
    mesh.calc_loop_triangles()
    nt = len(mesh.loop_triangles)
    tris = np.empty(nt * 3, np.int64)
    mesh.loop_triangles.foreach_get("loops", tris)
    tface = np.empty(nt, np.int64)
    mesh.loop_triangles.foreach_get("polygon_index", tface)
    co = np.empty(len(mesh.vertices) * 3, np.float64)
    mesh.vertices.foreach_get("co", co)
    return {
        "face_start": fs, "face_total": ft, "mat_index": mi, "loop_vert": lv,
        "uv": uv, "tris": tris.reshape(-1, 3), "tri_face": tface, "co": co.reshape(-1, 3),
    }


def source_uv_checks(obj, rep, uv_area=None):
    """FR-04: tiling and overlap per material on the source (active_render) UV.

    uv_area: optional dict, accumulates source UV area per material key (M3).
    """
    mesh = obj.data
    d = mesh_arrays(mesh)
    if d["uv"] is None or len(d["tris"]) == 0:
        return
    islands = uvmath.face_islands(d["face_start"], d["face_total"], d["loop_vert"], d["uv"])
    tri_mat = d["mat_index"][d["tri_face"]]
    for mi in np.unique(tri_mat):
        sel = tri_mat == mi
        slot_mat = obj.material_slots[mi].material if mi < len(obj.material_slots) else None
        mname = slot_mat.name if slot_mat else f"slot {mi}"
        tri_uv = d["uv"][d["tris"][sel]]
        if uv_area is not None:
            key = material_key(slot_mat)
            uv_area[key] = uv_area.get(key, 0.0) + float(uvmath.tri_area_2d(tri_uv).sum())
        rep_n = uvmath.tiling_repeats(tri_uv.reshape(-1, 2))
        if rep_n > 1:
            rep.warning(f"{mname}: 타일링 {rep_n}회, 해상도 손실 가능", obj.name)
        if sel.sum() > TILING_TRI_LIMIT:
            rep.info(f"{mname}: 삼각형이 많아 겹침 검사를 건너뜀", obj.name)
            continue
        frac = uvmath.overlap_fraction(tri_uv, islands[d["tri_face"][sel]], max_cells=128 * 128)
        if frac > 0.01:
            rep.warning(f"{mname}: 같은 머티리얼 안에서 UV가 겹칩니다 ({frac:.0%})", obj.name)


# ------------------------------------------------------------------ inspect
def _scale_unapplied(obj):
    return any(abs(abs(s) - 1.0) > 1e-4 for s in obj.scale)


def inspect(objects, settings, check_uv=True) -> Inspection:
    ins = Inspection()
    rep = ins.report
    for obj in objects:
        if obj.type != "MESH":
            rep.info(f"메시가 아니라 건너뜀 ({obj.type})", obj.name)
            continue
        mesh = obj.data
        if len(mesh.polygons) == 0:
            rep.warning("폴리곤이 없어 건너뜀", obj.name)
            continue
        if obj.library or mesh.library or obj.override_library:
            rep.warning("라이브러리 링크 데이터입니다. 복제본은 로컬 데이터로 만듭니다", obj.name)
        if len(obj.material_slots) == 0:
            rep.warning("머티리얼 슬롯이 없어 기본값으로 굽습니다", obj.name)
            ins.materials.setdefault("", material_info(None))
        for i, slot in enumerate(obj.material_slots):
            mat = slot.material
            if mat is None:
                rep.warning(f"빈 머티리얼 슬롯 {i}: 기본값으로 굽습니다", obj.name)
            key = material_key(mat)
            if key not in ins.materials:
                ins.materials[key] = material_info(mat)
        if len(mesh.uv_layers) == 0:
            rep.info("UV가 없어 Smart UV Project로 새로 폅니다", obj.name)
        elif check_uv:
            source_uv_checks(obj, rep, ins.source_uv_area)
        if any(s < 0 for s in obj.scale):
            rep.warning("음수 스케일입니다. 복제본에서 적용하고 노멀을 뒤집습니다", obj.name)
        elif _scale_unapplied(obj):
            if settings.apply_scale:
                rep.info("적용 안 된 스케일: 복제본에 적용합니다", obj.name)
            else:
                rep.warning("적용 안 된 스케일: 텍셀 밀도가 고르지 않을 수 있습니다", obj.name)
        ins.targets.append(obj)
    for info in ins.materials.values():
        label = info.name or "(빈 슬롯)"
        if info.cls == COMPLEX:
            rep.warning(f"{label}: 복합 셰이더 - 근사 처리 (DIFFUSE 컬러, Metallic 0)")
        elif info.cls == NODELESS:
            rep.info(f"{label}: 노드 없음 - 뷰포트 색으로 굽습니다")
        if info.has_emission and info.emission_max > 1.0 and settings.use_emissive:
            rep.warning(f"{label}: 발광 강도 {info.emission_max:g} > 1, 8비트에서 잘립니다")
    if not ins.targets:
        rep.error("대상 메시 오브젝트가 없습니다")
    elif ins.source_uv_area:
        capacity_check(ins, settings)
    return ins


def capacity_check(ins, settings):
    """M3 review: warn when one atlas cannot hold the sources' texel budget."""
    px = sum(ins.materials[k].image_res ** 2 * a for k, a in ins.source_uv_area.items()
             if k in ins.materials and ins.materials[k].image_res)
    if px <= 0:
        return
    cap = uvmath.atlas_capacity(px, settings.width, settings.height)
    if cap["ratio"] > 1.05:
        loss = 1.0 / (cap["ratio"] ** 0.5)
        ins.report.warning(
            f"소스 텍스처 해상도를 그대로 담으려면 아틀라스가 {cap['ratio']:.1f}배 커야 합니다 "
            f"(선명도 약 {loss:.0%}로 감소). 권장: {cap['suggested_res']}px 또는 {cap['sheets']}장으로 분할")
    else:
        ins.report.info(f"아틀라스 용량 사용 예상 {cap['ratio']:.0%} (소스 해상도 유지 가능)")
