"""Spike: pack_islands margin_method vs real pixel gap and time (spec 13 open item).
Run: blender -b --factory-startup --python tests/spike/pack_margin.py -- ADD SCALED FRACTION
Result (Blender 5.3): ADD gap = 2*margin*packing_scale (fast); FRACTION exact but up to 90s
for a smart-projected cube. -> bl/uv_layout.pack() uses ADD + scale correction.
"""
import bpy, sys, time, numpy as np
sys.path.insert(0, "/Users/mac_pro/Documents/Garden_Tools_BlenderAtlas")
from garden_atlas_tool.bl import uv_layout
from garden_atlas_tool.core import uvmath
for o in list(bpy.data.objects): bpy.data.objects.remove(o)
objs=[]
for i in range(4):
    bpy.ops.mesh.primitive_cube_add(location=(i*3,0,0)); o=bpy.context.active_object; objs.append(o)
    if i==0:
        o.data.uv_layers.remove(o.data.uv_layers[0]); o.data.uv_layers.new(name="UVAtlas")
    else:
        o.data.uv_layers[0].name="UVAtlas"
uv_layout.unwrap(bpy.context,[objs[0]])
uv_layout._edit(bpy.context, objs, lambda: bpy.ops.uv.average_islands_scale())
base={o.name: uv_layout._atlas_uv(o.data).copy() for o in objs}
for method in sys.argv[sys.argv.index("--")+1:]:
    for gap in (8, 32):
        for o in objs: uv_layout._set_atlas_uv(o.data, base[o.name])
        m = gap/512*0.5
        t=time.time()
        uv_layout._edit(bpy.context, objs, lambda: bpy.ops.uv.pack_islands(rotate=True, scale=True, margin_method=method, margin=m, shape_method="CONCAVE"))
        dt=time.time()-t
        tri,_,isl=uv_layout.atlas_triangles(objs)
        d,_=uvmath.min_island_distance(tri,isl)
        print(f"RESULT {method} gap={gap} time={dt:.2f}s min={d*512:.2f}px usage={uvmath.uv_usage(tri,256):.2f}")
