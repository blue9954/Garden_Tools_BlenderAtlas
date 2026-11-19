"""Bake: temp materials, proxy wiring, passes, render state (FR-30..39)."""

import bpy
import numpy as np

from ..core import naming, packing
from ..core.plan import COMPLEX_CONST, COMPLEX_DIFFUSE
from . import collect, images
from .uv_layout import ATLAS_UV, select_only

TARGET_LABEL = "__ga_target"
EMIT_LABEL = "__ga_proxy"


class BakeError(RuntimeError):
    pass


# ------------------------------------------------------------------ state
def _rna_snapshot(struct):
    snap = {}
    for p in struct.bl_rna.properties:
        if p.is_readonly or p.type in {"POINTER", "COLLECTION"} or p.identifier == "rna_type":
            continue
        try:
            snap[p.identifier] = getattr(struct, p.identifier)
        except AttributeError:
            pass
    return snap


def _rna_restore(struct, snap):
    for k, v in snap.items():
        try:
            setattr(struct, k, v)
        except (AttributeError, TypeError, ValueError):
            pass


class SceneState:
    """Selection, active object, mode, render/bake settings, hide_render (FR-02, FR-36)."""

    def __init__(self, context):
        sc = context.scene
        vl = context.view_layer
        self.scene = sc
        self.view_layer = vl
        self.active_name = vl.objects.active.name if vl.objects.active else None
        self.selected = [o.name for o in vl.objects if o.select_get()]
        self.mode = context.mode
        self.engine = sc.render.engine
        self.cycles = {k: getattr(sc.cycles, k) for k in ("samples", "use_denoising", "bake_type",
                                                          "use_adaptive_sampling") if hasattr(sc.cycles, k)}
        self.bake = _rna_snapshot(sc.render.bake)
        self.uv_sync = sc.tool_settings.use_uv_select_sync
        self.hide_render = {}

    def hide_for_render(self, objs):
        for o in objs:
            if o.name not in self.hide_render:
                self.hide_render[o.name] = o.hide_render
            o.hide_render = True

    def restore_hide(self):
        for name, v in self.hide_render.items():
            o = bpy.data.objects.get(name)
            if o is not None:
                o.hide_render = v
        self.hide_render.clear()

    def restore(self, context):
        sc = self.scene
        if context.mode != "OBJECT" and context.view_layer.objects.active is not None:
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass
        self.restore_hide()
        sc.render.engine = self.engine
        for k, v in self.cycles.items():
            setattr(sc.cycles, k, v)
        _rna_restore(sc.render.bake, self.bake)
        sc.tool_settings.use_uv_select_sync = self.uv_sync
        vl = self.view_layer
        for o in vl.objects:
            o.select_set(o.name in self.selected)
        vl.objects.active = vl.objects.get(self.active_name) if self.active_name else None


# ------------------------------------------------------------------ temp materials
class TempMat:
    def __init__(self, mat, cls):
        self.mat = mat
        self.cls = cls
        tree = mat.node_tree
        self.output = collect.output_node(mat)
        self.principled = collect.find_principled(mat) if cls != collect.COMPLEX else None
        node, sock = collect.surface_source(mat)
        self.orig_surface = sock
        self.emission = None
        self.target = tree.nodes.new("ShaderNodeTexImage")
        self.target.label = TARGET_LABEL
        self.target.location = (self.output.location.x, self.output.location.y - 400)
        tree.nodes.active = self.target

    @property
    def tree(self):
        return self.mat.node_tree

    def _surface(self, from_socket):
        surf = self.output.inputs["Surface"]
        for lk in list(surf.links):
            self.tree.links.remove(lk)
        if from_socket is not None:
            self.tree.links.new(from_socket, surf)

    def _emit_node(self):
        if self.emission is None:
            self.emission = self.tree.nodes.new("ShaderNodeEmission")
            self.emission.label = EMIT_LABEL
            self.emission.inputs["Strength"].default_value = 1.0
        col = self.emission.inputs["Color"]
        for lk in list(col.links):
            self.tree.links.remove(lk)
        return self.emission

    def restore(self):
        self._surface(self.orig_surface)

    def emit_const(self, value):
        em = self._emit_node()
        em.inputs["Color"].default_value = (value, value, value, 1.0)
        self._surface(em.outputs["Emission"])

    def emit_socket(self, socket_name):
        sock = self.principled.inputs[socket_name]
        em = self._emit_node()
        if sock.is_linked:
            src = sock.links[0].from_socket
            self.tree.links.new(src, em.inputs["Color"])
        else:
            v = sock.default_value
            try:
                rgba = tuple(v)
                rgba = (rgba + (1.0,) * 4)[:4]
            except TypeError:
                rgba = (float(v),) * 3 + (1.0,)
            em.inputs["Color"].default_value = rgba
        self._surface(em.outputs["Emission"])

    def wire(self, bp):
        """Configure the material for a pass. Returns True when it needs DIFFUSE."""
        if bp.bake_type == "EMIT" and bp.proxy_socket:
            if self.cls == collect.COMPLEX:
                if bp.complex_mode == COMPLEX_CONST:
                    self.emit_const(bp.complex_const)
                    return False
                self.restore()
                return bp.complex_mode == COMPLEX_DIFFUSE
            self.emit_socket(bp.proxy_socket)
            return False
        self.restore()
        return False


def _default_material(name, src, tracker):
    """Principled material for empty slots / node-less materials (FR-35)."""
    m = bpy.data.materials.new(name)
    tracker.add("materials", m)
    p = next((n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    if p is None:
        p = m.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    out = next((n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"), None)
    if out is None:
        out = m.node_tree.nodes.new("ShaderNodeOutputMaterial")
    m.node_tree.links.new(p.outputs["BSDF"], out.inputs["Surface"])
    if src is not None:
        p.inputs["Base Color"].default_value = tuple(src.diffuse_color)
        p.inputs["Metallic"].default_value = src.metallic
        p.inputs["Roughness"].default_value = src.roughness
    else:
        p.inputs["Base Color"].default_value = (0.8, 0.8, 0.8, 1.0)
        p.inputs["Metallic"].default_value = 0.0
        p.inputs["Roughness"].default_value = 0.5
    return m


def prepare_materials(dups, ins, tracker):
    """Swap every duplicate slot for a temporary copy with a target image node (FR-11)."""
    temps = {}

    def temp_for(mat):
        key = collect.material_key(mat)
        if key in temps:
            return temps[key]
        info = ins.materials.get(key) or collect.material_info(mat)
        name = naming.temp_name(mat.name if mat else "empty")
        if info.cls in (collect.EMPTY, collect.NODELESS):
            tmp = _default_material(name, mat, tracker)
            cls = collect.SIMPLE
        else:
            tmp = mat.copy()
            tmp.name = name
            tracker.add("materials", tmp)
            cls = info.cls
        temps[key] = TempMat(tmp, cls)
        temps[key].source = mat
        return temps[key]

    for o in dups:
        mesh = o.data
        if len(mesh.materials) == 0:
            mesh.materials.append(None)
        for i, mat in enumerate(list(mesh.materials)):
            mesh.materials[i] = temp_for(mat).mat
    for tm in temps.values():
        if tm.output is None or tm.target is None:
            raise BakeError(f"{tm.mat.name}: 베이크 타깃 노드를 만들 수 없습니다")
    return list(temps.values())


def restore_slots(dups, temps):
    """Put source materials back into duplicate slots (before deleting temps)."""
    back = {tm.mat.as_pointer(): tm.source for tm in temps}
    for o in dups:
        for i, m in enumerate(o.data.materials):
            if m is not None and m.as_pointer() in back:
                o.data.materials[i] = back[m.as_pointer()]


# ------------------------------------------------------------------ bake
def _bake(context, dups, bake_type, margin, normal_g, **extra):
    select_only(context, dups, dups[0])
    with context.temp_override(selected_objects=dups, selected_editable_objects=dups,
                               active_object=dups[0], object=dups[0]):
        res = bpy.ops.object.bake(
            type=bake_type, uv_layer=ATLAS_UV, margin=margin, margin_type="EXTEND",
            use_clear=False, use_selected_to_active=False, target="IMAGE_TEXTURES",
            save_mode="INTERNAL", normal_space="TANGENT", normal_r="POS_X",
            normal_g=normal_g, normal_b="POS_Z", **extra)
    if res != {"FINISHED"}:  # FR-37
        raise BakeError(f"object.bake 반환값 {res}")


def run_pass(context, dups, temps, bp, settings, preset, state, sources, tracker):
    """Execute one pass; returns its (H, W, 4) image in stored space."""
    w, h = settings.width, settings.height
    if bp.is_fill_only:
        return packing.filled(w, h, bp.fill)
    sc = context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = bp.samples
    if hasattr(sc.cycles, "use_denoising"):
        sc.cycles.use_denoising = False
    if hasattr(sc.cycles, "use_adaptive_sampling"):
        sc.cycles.use_adaptive_sampling = False
    normal_g = preset["normal_g"]
    margin = settings.bake_margin_px

    if bp.channel == "AO":
        others = [o for o in sc.objects if o not in dups and (settings.ao_isolate or o in sources)]
        state.hide_for_render(others)

    try:
        diffuse = [tm for tm in temps if tm.wire(bp)]
        mixed = diffuse and len(diffuse) != len(temps)
        if not mixed and not diffuse:
            img = images.new_image(naming.temp_name(bp.channel), w, h, bp.colorspace, bp.fill, tracker,
                                   float_buffer=bp.float_buffer)
            for tm in temps:
                tm.target.image = img
            _bake(context, dups, bp.bake_type, margin, normal_g)
            out = images.read(img)
            images.safe_remove(img)
            return out
        if not mixed:  # every material is complex -> one DIFFUSE colour pass
            img = images.new_image(naming.temp_name(bp.channel), w, h, bp.colorspace, bp.fill, tracker)
            for tm in temps:
                tm.target.image = img
            _bake(context, dups, "DIFFUSE", margin, normal_g, pass_filter={"COLOR"})
            out = images.read(img)
            images.safe_remove(img)
            return out
        return _mixed_pass(context, dups, temps, set(diffuse), bp, margin, normal_g, tracker, w, h)
    finally:
        if bp.channel == "AO":
            state.restore_hide()


def _mixed_pass(context, dups, temps, diffuse, bp, margin, normal_g, tracker, w, h):
    """Proxy materials and DIFFUSE materials baked separately and composited."""
    clear = tuple(bp.fill[:3]) + (0.0,)
    img_p = images.new_image(naming.temp_name(bp.channel + "_p"), w, h, bp.colorspace, clear, tracker)
    img_d = images.new_image(naming.temp_name(bp.channel + "_d"), w, h, bp.colorspace, clear, tracker)
    dummy = images.new_image(naming.temp_name("dummy"), 8, 8, bp.colorspace, clear, tracker)
    try:
        for tm in temps:
            tm.target.image = dummy if tm in diffuse else img_p
        _bake(context, dups, bp.bake_type, 0, normal_g)
        for tm in temps:
            tm.target.image = img_d if tm in diffuse else dummy
        _bake(context, dups, "DIFFUSE", 0, normal_g, pass_filter={"COLOR"})
        a_p = images.read(img_p)
        a_d = images.read(img_d)
    finally:
        for im in (img_p, img_d, dummy):
            images.safe_remove(im)
    m_p = a_p[..., 3] > 0.5
    m_d = a_d[..., 3] > 0.5
    out = packing.composite(packing.filled(w, h, bp.fill), [(a_p, m_p), (a_d, m_d)])
    out, _ = packing.dilate(out, m_p | m_d, margin)
    out[..., 3] = 1.0
    return out


def remove_temps(temps, tracker):
    for tm in temps:
        try:
            tm.target.image = None
        except ReferenceError:
            pass
    tracker.remove_materials()


def channel_value_at(arr, u, v):
    """Sample a (H,W,4) image at UV (nearest). Utility for tests/diagnostics."""
    h, w = arr.shape[:2]
    x = min(w - 1, max(0, int(u * w)))
    y = min(h - 1, max(0, int(v * h)))
    return np.asarray(arr[y, x])
