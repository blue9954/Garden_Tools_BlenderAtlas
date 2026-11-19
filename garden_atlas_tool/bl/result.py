"""Result material, slot replacement, UV cleanup, join (FR-41..45)."""

import bpy
import numpy as np

from ..core import naming
from .uv_layout import ATLAS_UV, select_only


def _node(tree, idname, x, y, label=""):
    n = tree.nodes.new(idname)
    n.location = (x, y)
    if label:
        n.label = label
    return n


def build_material(name, tex_images, tracker):
    """tex_images: {suffix: (bpy image, has_alpha)}. Returns the material."""
    mat = bpy.data.materials.new(naming.material_name(name))
    tracker.add("materials", mat)
    tree = mat.node_tree
    nodes, links = tree.nodes, tree.links
    p = next((n for n in nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    out = next((n for n in nodes if n.bl_idname == "ShaderNodeOutputMaterial"), None)
    if p is None:
        p = _node(tree, "ShaderNodeBsdfPrincipled", 0, 0)
    if out is None:
        out = _node(tree, "ShaderNodeOutputMaterial", 400, 0)
    links.new(p.outputs["BSDF"], out.inputs["Surface"])
    uv = _node(tree, "ShaderNodeUVMap", -1300, 0)
    uv.uv_map = ATLAS_UV

    def tex(suffix, y):
        img, alpha = tex_images[suffix]
        t = _node(tree, "ShaderNodeTexImage", -900, y, suffix)
        t.image = img
        links.new(uv.outputs["UV"], t.inputs["Vector"])
        return t, alpha

    if "BC" in tex_images:
        t, alpha = tex("BC", 300)
        links.new(t.outputs["Color"], p.inputs["Base Color"])
        if alpha:
            links.new(t.outputs["Alpha"], p.inputs["Alpha"])
    if "ORM" in tex_images:
        t, _ = tex("ORM", 0)
        sep = _node(tree, "ShaderNodeSeparateColor", -550, 0, "ORM")
        links.new(t.outputs["Color"], sep.inputs["Color"])
        links.new(sep.outputs["Green"], p.inputs["Roughness"])
        links.new(sep.outputs["Blue"], p.inputs["Metallic"])
    if "N" in tex_images:
        t, _ = tex("N", -300)
        # FR-42: DirectX (-Y) -> Blender (+Y): invert green.
        sep = _node(tree, "ShaderNodeSeparateColor", -550, -300, "N DirectX")
        inv = _node(tree, "ShaderNodeMath", -380, -340, "Flip G")
        inv.operation = "SUBTRACT"
        inv.inputs[0].default_value = 1.0
        comb = _node(tree, "ShaderNodeCombineColor", -220, -300)
        nm = _node(tree, "ShaderNodeNormalMap", -60, -300)
        nm.uv_map = ATLAS_UV
        links.new(t.outputs["Color"], sep.inputs["Color"])
        links.new(sep.outputs["Red"], comb.inputs["Red"])
        links.new(sep.outputs["Green"], inv.inputs[1])
        links.new(inv.outputs["Value"], comb.inputs["Green"])
        links.new(sep.outputs["Blue"], comb.inputs["Blue"])
        links.new(comb.outputs["Color"], nm.inputs["Color"])
        links.new(nm.outputs["Normal"], p.inputs["Normal"])
    if "E" in tex_images:
        t, _ = tex("E", -650)
        links.new(t.outputs["Color"], p.inputs["Emission Color"])
        p.inputs["Emission Strength"].default_value = 1.0
    return mat


def assign(obj, mat, atlas_uv_only):
    """FR-43 / FR-44."""
    mesh = obj.data
    mesh.materials.clear()
    mesh.materials.append(mat)
    for slot in obj.material_slots:
        slot.link = "DATA"
    n = len(mesh.polygons)
    mesh.polygons.foreach_set("material_index", np.zeros(n, np.int32))
    if atlas_uv_only:
        for layer in [l for l in mesh.uv_layers if l.name != ATLAS_UV]:
            mesh.uv_layers.remove(layer)
    atlas = mesh.uv_layers[ATLAS_UV]
    mesh.uv_layers.active = atlas
    atlas.active_render = True
    mesh.update()


def join(context, dups, name):
    """FR-45: join duplicates into SM_<name>. Returns the joined object."""
    active = dups[0]
    select_only(context, dups, active)
    with context.temp_override(active_object=active, object=active,
                               selected_objects=dups, selected_editable_objects=dups):
        res = bpy.ops.object.join()
    if res != {"FINISHED"}:
        raise RuntimeError(f"object.join 실패: {res}")
    active.name = naming.mesh_name(name)
    active.data.name = naming.mesh_name(name)
    return active
