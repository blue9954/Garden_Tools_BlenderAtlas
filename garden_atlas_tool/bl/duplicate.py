"""Working duplicates, datablock tracking and cleanup (FR-10..15)."""

import os

import bpy
from mathutils import Matrix

from ..core import naming

SRC_PROP = "garden_atlas_src"
WEIGHT_PROP = "garden_atlas_weight_src"


class Tracker:
    """Remembers datablocks/files created in this run by reference (not name)."""

    def __init__(self):
        self.objects = []
        self.meshes = []
        self.materials = []
        self.images = []
        self.collections = []
        self.files = []

    def add(self, kind, item):
        getattr(self, kind).append(item)
        return item

    @staticmethod
    def _remove(coll, items):
        for it in reversed(items):
            try:
                if it is not None and it.name in coll and coll[it.name] == it:
                    coll.remove(it)
            except ReferenceError:
                pass
        items.clear()

    def remove_images(self):
        self._remove(bpy.data.images, self.images)

    def remove_materials(self):
        self._remove(bpy.data.materials, self.materials)

    def rollback(self):
        self._remove(bpy.data.objects, self.objects)
        self._remove(bpy.data.meshes, self.meshes)
        self.remove_materials()
        self.remove_images()
        self._remove(bpy.data.collections, self.collections)
        for f in reversed(self.files):
            try:
                os.remove(f)
            except OSError:
                pass
        self.files.clear()


def find_result_collection(name):
    return bpy.data.collections.get(naming.collection_name(name))


def existing_result(name):
    """True when a previous result with this atlas name exists."""
    return (find_result_collection(name) is not None
            or naming.material_name(name) in bpy.data.materials)


def remove_previous(name):
    """Delete a previous result: collection, its objects/meshes, M_ material, T_ images."""
    coll = find_result_collection(name)
    if coll is not None:
        for obj in list(coll.all_objects):
            mesh = obj.data if obj.type == "MESH" else None
            bpy.data.objects.remove(obj)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(coll)
    mat = bpy.data.materials.get(naming.material_name(name))
    if mat is not None:
        bpy.data.materials.remove(mat)
    prefix = naming.texture_name(name, "")
    for img in list(bpy.data.images):
        if img.name.startswith(prefix):
            bpy.data.images.remove(img)
    remove_temp_materials()


def remove_temp_materials():
    for m in list(bpy.data.materials):
        if m.name.startswith(naming.TEMP_PREFIX) and m.users == 0:
            bpy.data.materials.remove(m)


def ensure_collection(scene, name, tracker):
    coll = bpy.data.collections.new(naming.collection_name(name))
    tracker.add("collections", coll)
    scene.collection.children.link(coll)
    return coll


def duplicate(context, src, coll, settings, tracker):
    """Create a working duplicate of ``src`` inside ``coll``."""
    dg = context.evaluated_depsgraph_get()
    if settings.apply_modifiers and len(src.modifiers):
        ev = src.evaluated_get(dg)
        mesh = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
    else:
        mesh = src.data.copy()
    tracker.add("meshes", mesh)
    mesh.name = naming.duplicate_name(src.name)
    # Effective slot materials go on the mesh so slot link mode no longer matters.
    # (materials.clear() would drop the material_index attribute; assign in place.)
    slots = [slot.material for slot in src.material_slots]
    while len(mesh.materials) < len(slots):
        mesh.materials.append(None)
    for i, m in enumerate(slots):
        mesh.materials[i] = m
    dup = bpy.data.objects.new(naming.duplicate_name(src.name), mesh)
    tracker.add("objects", dup)
    dup[SRC_PROP] = src.name
    dup[WEIGHT_PROP] = float(getattr(src, "garden_atlas_weight", 1.0))
    coll.objects.link(dup)
    dup.matrix_world = src.matrix_world.copy()
    if settings.apply_scale:
        apply_scale(dup)
    return dup


def apply_scale(obj):
    loc, rot, scl = obj.matrix_world.decompose()
    if all(abs(s - 1.0) < 1e-6 for s in scl) and obj.matrix_world.determinant() > 0:
        return
    # decompose() folds a mirror into rotation; use the full linear part instead.
    lin = rot.to_matrix().to_4x4().inverted() @ Matrix.Translation(-loc) @ obj.matrix_world
    obj.data.transform(lin)
    if lin.determinant() < 0:
        obj.data.flip_normals()
    obj.matrix_world = Matrix.LocRotScale(loc, rot, (1.0, 1.0, 1.0))
    obj.data.update()


def result_duplicates(name):
    """Duplicates from a previous build_uv run (for bake-only)."""
    coll = find_result_collection(name)
    if coll is None:
        return []
    return [o for o in coll.objects if o.type == "MESH" and SRC_PROP in o]
