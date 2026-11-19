"""GUI smoke test (needs a display). Not part of L2; run manually:

    blender --factory-startup --python tests/integration/gui_smoke.py

Registers the add-on, opens the Garden N-panel, runs analyze and the modal
run_all (INVOKE_DEFAULT), takes screenshots, prints a verdict and quits.
"""

import os
import sys
import tempfile
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import garden_atlas_tool as addon  # noqa: E402

OUT = os.environ.get("GA_GUI_OUT") or tempfile.mkdtemp(prefix="ga_gui_")
STATE = {"phase": 0, "errors": [], "wait": 0}


def view3d():
    win = bpy.context.window_manager.windows[0]
    for area in win.screen.areas:
        if area.type == "VIEW_3D":
            region = next(r for r in area.regions if r.type == "WINDOW")
            return win, area, region
    raise RuntimeError("no 3D view")


def setup():
    addon.register()
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    cols = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (0, 1, 1)]
    objs = []
    for i, c in enumerate(cols):
        bpy.ops.mesh.primitive_cube_add(location=(i * 2.5, 0, 0))
        o = bpy.context.active_object
        o.name = f"Prop{i}"
        for k in range(2):
            m = bpy.data.materials.new(f"Mat{i}_{k}")
            p = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
            p.inputs["Base Color"].default_value = (*[x * (1 - 0.5 * k) for x in c], 1)
            p.inputs["Metallic"].default_value = 0.5 * k
            o.data.materials.append(m)
        o.data.polygons.foreach_set("material_index", [0, 1, 0, 1, 0, 1])
        objs.append(o)
    for o in objs:
        o.select_set(True)
    p = bpy.context.scene.garden_atlas
    p.atlas_name = "GuiSmoke"
    p.width = "1024"
    p.height = "1024"
    p.output_dir = OUT
    win, area, region = view3d()
    area.spaces[0].show_region_ui = True
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.view3d.view_all()
    # The active N-panel tab is read-only from Python; for the screenshot only,
    # re-register the panel under the default "Item" tab.
    from garden_atlas_tool import ui
    bpy.utils.unregister_class(ui.GARDENATLAS_PT_atlas)
    ui.GARDENATLAS_PT_atlas.bl_category = "Item"
    bpy.utils.register_class(ui.GARDENATLAS_PT_atlas)


def shot(name):
    win, area, _ = view3d()
    path = os.path.join(OUT, name)
    with bpy.context.temp_override(window=win, area=area):
        bpy.ops.screen.screenshot_area(filepath=path)
    print("[GUI] screenshot", path)


def tick():
    try:
        ph = STATE["phase"]
        win, area, region = view3d()
        if ph == 0:
            setup()
        elif ph == 1:
            with bpy.context.temp_override(window=win, area=area, region=region):
                bpy.ops.garden_atlas.analyze()
        elif ph == 2:
            shot("01_analyzed.png")
            with bpy.context.temp_override(window=win, area=area, region=region):
                r = bpy.ops.garden_atlas.run_all("INVOKE_DEFAULT")
            print("[GUI] run_all invoke ->", r)
        elif ph == 3:
            p = bpy.context.scene.garden_atlas
            if p.running:
                print("[GUI] running:", p.status)
                return 0.5
        elif ph == 4:
            area.spaces[0].shading.type = "MATERIAL"
            shot("02_done.png")
            p = bpy.context.scene.garden_atlas
            files = sorted(f for f in os.listdir(OUT) if f.startswith("T_GuiSmoke"))
            print("[GUI] report:", [(m.level, m.text) for m in p.report])
            print("[GUI] files:", files)
            ok = len(files) == 3 and "M_GuiSmoke" in bpy.data.materials
            print("[GUI] VERDICT", "PASS" if ok and not STATE["errors"] else "FAIL", STATE["errors"])
        elif ph == 5:
            bpy.ops.wm.quit_blender()
            return None
        STATE["phase"] += 1
    except Exception as e:
        traceback.print_exc()
        STATE["errors"].append(str(e))
        STATE["phase"] = 4
    return 1.0


bpy.app.timers.register(tick, first_interval=1.0)
