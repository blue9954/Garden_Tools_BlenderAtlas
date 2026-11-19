"""L4 GUI checks in a real Blender window (needs a display):

    blender --factory-startup --enable-event-simulate --python tests/integration/gui_l4.py

A) ESC during the modal bake: a real ESC window event cancels, and the run's
   duplicates, temp materials, images and files are gone; selection restored.
B) UV editor edit -> "bake only": UV Preview, rotate the islands in an
   IMAGE_EDITOR (UV) area with transform.rotate, then run garden_atlas.bake
   (INVOKE) and check the baked colours follow the edited UVs.
Prints "[L4] <check> PASS/FAIL" lines and "[L4] VERDICT".
"""

import math
import os
import sys
import tempfile
import traceback

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import garden_atlas_tool as addon  # noqa: E402

OUT = os.environ.get("GA_GUI_OUT") or tempfile.mkdtemp(prefix="ga_l4_")
COLORS = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
S = {"phase": 0, "results": [], "uv_before": None, "esc_sent": False}


def ok(name, cond, detail=""):
    S["results"].append((name, bool(cond)))
    print(f"[L4] {name} {'PASS' if cond else 'FAIL'} {detail}", flush=True)


def win_area(kind="VIEW_3D"):
    win = bpy.context.window_manager.windows[0]
    for a in win.screen.areas:
        if a.type == kind:
            return win, a, next(r for r in a.regions if r.type == "WINDOW")
    return win, None, None


def ctx(kind="VIEW_3D"):
    win, area, region = win_area(kind)
    return bpy.context.temp_override(window=win, area=area, region=region, screen=win.screen)


def scene_setup(name, res, samples):
    for c in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for it in list(c):
            c.remove(it)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    objs = []
    for i in range(3):
        bpy.ops.mesh.primitive_cube_add(location=(i * 3, 0, 0))
        o = bpy.context.active_object
        o.name = f"L4_{i}"
        for k, c in enumerate(COLORS):
            m = bpy.data.materials.new(f"L4M{i}{k}")
            p = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
            p.inputs["Base Color"].default_value = (*c, 1)
            o.data.materials.append(m)
        o.data.polygons.foreach_set("material_index", np.array([0, 0, 1, 1, 2, 2], np.int32))
        o.data.update()
        objs.append(o)
    for o in bpy.context.view_layer.objects:
        o.select_set(o in objs)
    bpy.context.view_layer.objects.active = objs[1]
    p = bpy.context.scene.garden_atlas
    p.atlas_name = name
    p.width = p.height = str(res)
    p.samples = samples
    p.output_dir = OUT
    return objs


def leftovers(name):
    return {
        "collection": f"ATLAS_{name}" in bpy.data.collections,
        "objects": [o.name for o in bpy.data.objects if o.name.endswith("_atlas")],
        "temp_mats": [m.name for m in bpy.data.materials if m.name.startswith("__ga")],
        "images": [i.name for i in bpy.data.images if i.name.startswith(("__ga", f"T_{name}"))],
        "files": [f for f in os.listdir(OUT) if f.startswith(f"T_{name}")],
    }


def atlas_uv(o):
    a = np.empty(len(o.data.loops) * 2, np.float32)
    o.data.uv_layers["UVAtlas"].uv.foreach_get("vector", a)
    return a.reshape(-1, 2)


def tick():
    try:
        ph = S["phase"]
        if ph == 0:
            addon.register()
        if ph == 6:
            fails = [n for n, c in S["results"] if not c]
            print(f"[L4] VERDICT {'PASS' if not fails else 'FAIL'} {fails}", flush=True)
            print(f"[L4] out {OUT}", flush=True)
            bpy.ops.wm.quit_blender()
            return None
        p = bpy.context.scene.garden_atlas
        win = bpy.context.window_manager.windows[0]
        # ---------------- A) ESC cancel
        if ph == 0:
            S["objs"] = scene_setup("L4ESC", 4096, 64)  # slow enough to press ESC mid-run
            with ctx():
                r = bpy.ops.garden_atlas.run_all("INVOKE_DEFAULT")
            ok("A.invoke_modal", r == {"RUNNING_MODAL"}, str(r))
        elif ph == 1:
            if not S["esc_sent"]:
                if p.status.startswith("베이크:"):
                    S["esc_label"] = p.status
                    win.event_simulate(type="ESC", value="PRESS")
                    win.event_simulate(type="ESC", value="RELEASE")
                    S["esc_sent"] = True
                elif not p.running:
                    ok("A.esc_in_time", False, "run finished before a bake pass was seen")
                    S["phase"] = 3
                return 0.05
            if p.running:
                return 0.1
            left = leftovers("L4ESC")
            ok("A.esc_cancelled", not p.running, f"pressed during '{S['esc_label']}'")
            ok("A.no_leftovers", not any(left.values()), str(left))
            sel = {o.name for o in bpy.context.selected_objects}
            ok("A.selection_restored", sel == {"L4_0", "L4_1", "L4_2"}
               and bpy.context.view_layer.objects.active.name == "L4_1", str(sel))
            ok("A.report_cancel", any("취소" in m.text for m in p.report),
               str([m.text for m in p.report]))
        # ---------------- B) UV editor edit -> bake only
        elif ph == 2:
            S["objs"] = scene_setup("L4UV", 1024, 4)
            with ctx():
                r = bpy.ops.garden_atlas.build_uv("INVOKE_DEFAULT")
            ok("B.build_uv_invoke", r in ({"RUNNING_MODAL"}, {"FINISHED"}), str(r))
        elif ph == 3:
            if p.running:
                return 0.1
            if "ATLAS_L4UV" not in bpy.data.collections:
                S["phase"] = 6
                return 0.1
            dups = list(bpy.data.collections["ATLAS_L4UV"].objects)
            S["uv_before"] = {o.name: atlas_uv(o).copy() for o in dups}
            # turn the 3D view into a UV editor and edit like a user would
            _, area, _ = win_area("VIEW_3D")
            area.type = "IMAGE_EDITOR"
            area.ui_type = "UV"
            for o in bpy.context.view_layer.objects:
                o.select_set(o in dups)
            bpy.context.view_layer.objects.active = dups[0]
            with ctx("IMAGE_EDITOR"):
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.context.scene.tool_settings.use_uv_select_sync = False
                bpy.ops.mesh.select_all(action="SELECT")
                bpy.ops.uv.select_all(action="SELECT")
            sp = area.spaces.active
            sp.pivot_point = "CURSOR"
            sp.cursor_location = (0.5, 0.5)
        elif ph == 4:
            with ctx("IMAGE_EDITOR"):
                r = bpy.ops.transform.rotate(value=math.radians(90), orient_axis="Z")
                bpy.ops.object.mode_set(mode="OBJECT")
            dups = list(bpy.data.collections["ATLAS_L4UV"].objects)
            moved = max(np.abs(atlas_uv(o) - S["uv_before"][o.name]).max() for o in dups)
            ok("B.uv_edited_in_editor", r == {"FINISHED"} and moved > 0.05, f"{r} max move {moved:.3f}")
            _, area, _ = win_area("IMAGE_EDITOR")
            area.type = "VIEW_3D"
            for o in bpy.context.view_layer.objects:
                o.select_set(o.name.startswith("L4_") and not o.name.endswith("_atlas"))
            with ctx():
                r = bpy.ops.garden_atlas.bake("INVOKE_DEFAULT")
            ok("B.bake_only_invoke", r in ({"RUNNING_MODAL"}, {"FINISHED"}), str(r))
        elif ph == 5:
            if p.running:
                return 0.1
            dups = [bpy.data.objects[f"L4_{i}_atlas"] for i in range(3)]
            path = os.path.join(OUT, "T_L4UV_BC.png")
            img = bpy.data.images.load(path, check_existing=False)
            w, h = img.size
            a = np.empty(w * h * 4, np.float32)
            img.pixels.foreach_get(a)
            bpy.data.images.remove(img)
            a = a.reshape(h, w, 4)
            worst = 0.0
            for d in dups:
                uv = atlas_uv(d)
                for poly in d.data.polygons:
                    c = uv[poly.loop_start:poly.loop_start + poly.loop_total].mean(axis=0)
                    px = a[min(h - 1, int(c[1] * h)), min(w - 1, int(c[0] * w)), :3]
                    exp = np.array(COLORS[[0, 0, 1, 1, 2, 2][poly.index]], float)
                    worst = max(worst, float(np.abs(px - exp).max()))
            ok("B.bake_follows_edited_uv", worst <= 2.5 / 255, f"worst {worst * 255:.1f}/255")
            ok("B.result_material", all(d.material_slots[0].material.name == "M_L4UV" for d in dups))
            bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "l4_result.blend"))
        S["phase"] += 1
    except Exception as e:
        traceback.print_exc()
        ok(f"phase{S['phase']}_exception", False, str(e))
        if S["phase"] == 6:  # failure while finishing: quit anyway
            bpy.ops.wm.quit_blender()
            return None
        S["phase"] = 6
    return 0.5


bpy.app.timers.register(tick, first_interval=1.0)
