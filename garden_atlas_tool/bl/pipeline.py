"""Stage orchestration with rollback (spec 11).

``Pipeline.steps()`` is a generator: each ``yield`` is a safe point between
stages / bake passes (label, fraction). ``run()`` exhausts it synchronously;
the modal operator advances it on a timer and closes it on ESC, which rolls
back through the same exception path.
"""

import json
import os
import time
import traceback

import bpy

from ..core import naming, packing, plan, report, settings as core_settings
from ..core.version import tool_version
from ..core.presets import get_preset
from . import bake, collect, duplicate, images, result, uv_layout

MODE_ALL = "ALL"
MODE_UV = "UV"
MODE_BAKE = "BAKE"


class PipelineError(RuntimeError):
    pass


class Pipeline:
    def __init__(self, context, settings, mode=MODE_ALL, sources=None, fail_at=None):
        self.context = context
        self.s = settings
        self.mode = mode
        self.sources = list(sources if sources is not None else context.selected_objects)
        self.tracker = duplicate.Tracker()
        self.report = report.Report()
        self.result = {}
        self.label = ""
        self.fail_at = fail_at  # test hook: stage label that raises

    # -------------------------------------------------------------- helpers
    def _stage(self, label, frac):
        self.label = label
        if self.fail_at and self.fail_at == label:
            raise PipelineError(f"강제 실패: {label}")
        return label, frac

    def run(self):
        for _ in self.steps():
            pass
        return self.result

    # -------------------------------------------------------------- stages
    def steps(self):
        ctx, s = self.context, self.s
        check_out = self.mode != MODE_UV
        rep, out_dir = core_settings.validate(s, bpy.data.filepath, check_output=check_out)
        self.report.extend(rep.messages)
        dups = []
        if self.mode == MODE_BAKE:
            dups = duplicate.result_duplicates(s.atlas_name)
            if not dups:
                self.report.error("UV 미리보기 결과가 없습니다. 먼저 UV 미리보기 또는 전체 실행을 하세요")
            elif any(len(d.data.materials) == 1 and d.data.materials[0] is not None
                     and d.data.materials[0].name == naming.material_name(s.atlas_name) for d in dups):
                self.report.error("이미 베이크된 결과입니다. UV 미리보기를 다시 실행하세요")
            ins = collect.inspect(dups, s, check_uv=False)
            names = {d.get(duplicate.SRC_PROP) for d in dups}
            self.sources = [o for o in bpy.data.objects if o.name in names]
        else:
            ins = collect.inspect(self.sources, s)
        self.report.extend(ins.report.messages)
        if self.report.has_errors:
            raise PipelineError("검사 오류: " + "; ".join(m.text for m in self.report.of(report.ERROR)))

        state = bake.SceneState(ctx)
        temps = []
        t0 = time.perf_counter()
        try:
            if ctx.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            if self.mode != MODE_BAKE:
                yield self._stage("복제", 0.05)
                duplicate.remove_previous(s.atlas_name)
                coll = duplicate.ensure_collection(ctx.scene, s.atlas_name, self.tracker)
                dups = [duplicate.duplicate(ctx, o, coll, s, self.tracker) for o in ins.targets]
                yield self._stage("UV 레이아웃", 0.15)
                unwrapped = uv_layout.layout(ctx, dups, s, ins.materials)
                for o in unwrapped:
                    self.report.info("UVAtlas를 Smart UV Project로 폈습니다", o.name)
                self.result["stats"] = uv_layout.stats(dups, s.width, s.height)
                self.result["duplicates"] = [d.name for d in dups]
                if self.mode == MODE_UV:
                    self._finish_stats(t0)
                    return

            # ---- bake
            yield self._stage("베이크 준비", 0.2)
            if "stats" not in self.result:
                self.result["stats"] = uv_layout.stats(dups, s.width, s.height)
            preset = get_preset(s.preset)
            passes = plan.make_plan(s, has_alpha=ins.has_alpha, has_emission=ins.has_emission)
            temps = bake.prepare_materials(dups, ins, self.tracker)
            channels = {}
            n = max(1, len(passes))
            for i, bp in enumerate(passes):
                yield self._stage(f"베이크: {bp.channel}", 0.2 + 0.6 * i / n)
                try:
                    channels[bp.channel] = bake.run_pass(ctx, dups, temps, bp, s, preset, state,
                                                         self.sources, self.tracker)
                except (RuntimeError, bake.BakeError) as e:
                    raise PipelineError(f"베이크 실패 ({bp.channel}): {e}") from e
            bake.restore_slots(dups, temps)
            bake.remove_temps(temps, self.tracker)
            temps = []

            yield self._stage("패킹 · 저장", 0.85)
            tex_images, tex_entries = self._save_textures(preset, passes, channels, out_dir)
            channels.clear()

            yield self._stage("결과 적용", 0.95)
            mat = result.build_material(s.atlas_name, tex_images, self.tracker)
            for d in dups:
                result.assign(d, mat, s.atlas_uv_only)
            final = dups
            if s.join_result and len(dups) > 1:
                src_map = [(d.get(duplicate.SRC_PROP), d.name) for d in dups]
                joined = result.join(ctx, dups, s.atlas_name)
                final = [joined]
                for m in list(self.tracker.meshes):
                    try:
                        if m.users == 0:
                            bpy.data.meshes.remove(m)
                    except ReferenceError:
                        pass
                self.result["join_map"] = src_map
            self._finish_stats(t0)
            self.result["material"] = mat.name
            self.result["objects"] = [o.name for o in final]
            self.result["files"] = list(self.tracker.files)
            self.result["output_dir"] = out_dir
            if s.export_fbx:
                yield self._stage("FBX 내보내기", 0.98)
                self.result["fbx"] = self._export_fbx(final, out_dir)
                self.result["files"] = list(self.tracker.files)
            if s.write_manifest:
                self._write_manifest(preset, tex_entries, ins, dups if not s.join_result else final,
                                     out_dir)
            self.report.info(f"완료: {len(self.tracker.files)}개 파일, {self.result['stats']['bake_seconds']:.1f}초")
        except BaseException as e:
            if temps:
                try:
                    bake.restore_slots(dups, temps)
                except ReferenceError:
                    pass
            self.tracker.rollback()
            if not isinstance(e, GeneratorExit):
                traceback.print_exc()
                if isinstance(e, Exception) and not isinstance(e, PipelineError):
                    self.report.error(f"{self.label}: {e}")
                elif isinstance(e, PipelineError):
                    self.report.error(str(e))
            else:
                self.report.warning("사용자가 취소했습니다. 이번 실행의 데이터를 정리했습니다")
            raise
        finally:
            duplicate.remove_temp_materials()
            state.restore(ctx)

    # -------------------------------------------------------------- pieces
    def _finish_stats(self, t0):
        self.result.setdefault("stats", {})["bake_seconds"] = round(time.perf_counter() - t0, 2)

    def _save_textures(self, preset, passes, channels, out_dir):
        s = self.s
        tex_images, entries = {}, {}
        for spec in plan.output_textures(preset, passes):
            with_alpha = plan.texture_has_alpha(spec, passes)
            # 16-bit only for PNG; Blender writes float buffers to PNG as 16-bit.
            is_float = plan.texture_is_float(spec, passes) and s.file_format == "PNG"
            arr = packing.pack_texture(spec, channels, with_alpha)
            tname = naming.texture_name(s.atlas_name, spec["suffix"])
            img = images.new_image(tname, s.width, s.height, spec["colorspace"],
                                   (0, 0, 0, 1), self.tracker, alpha=with_alpha,
                                   float_buffer=is_float)
            images.write(img, arr)
            fname = naming.texture_file(s.atlas_name, spec["suffix"], s.file_format)
            path = os.path.join(out_dir, fname)
            images.save(img, path, s.file_format)
            self.tracker.files.append(path)
            images.safe_remove(img)
            loaded = images.load(path, spec["colorspace"])
            loaded.name = tname
            self.tracker.add("images", loaded)
            tex_images[spec["suffix"]] = (loaded, with_alpha)
            entries[spec["suffix"]] = report.texture_entry(spec, fname, with_alpha,
                                                           preset["normal_convention"],
                                                           16 if is_float else 8)
        return tex_images, entries

    def _export_fbx(self, objs, out_dir):
        """FR-47: Unreal-friendly FBX of the result objects."""
        path = os.path.join(out_dir, naming.mesh_name(self.s.atlas_name) + ".fbx")
        uv_layout.select_only(self.context, objs, objs[0])
        with self.context.temp_override(selected_objects=objs, active_object=objs[0],
                                        object=objs[0]):
            res = bpy.ops.export_scene.fbx(
                filepath=path, use_selection=True, object_types={"MESH"},
                apply_unit_scale=True, apply_scale_options="FBX_SCALE_NONE",
                use_mesh_modifiers=True, mesh_smooth_type="FACE", use_tspace=True,
                use_triangles=False, add_leaf_bones=False, bake_anim=False,
                path_mode="STRIP", embed_textures=False,
                axis_forward="-Z", axis_up="Y")
        if res != {"FINISHED"} or not os.path.isfile(path):
            raise PipelineError(f"FBX 내보내기 실패: {res}")
        self.tracker.files.append(path)
        return path

    def _write_manifest(self, preset, tex_entries, ins, final, out_dir):
        s = self.s
        srcs = []
        bake_kind = {collect.SIMPLE: "proxy", collect.COMPLEX: "native",
                     collect.NODELESS: "default", collect.EMPTY: "default"}
        join_map = dict(self.result.get("join_map", []))
        for src in self.sources:
            if src not in ins.targets and self.mode != MODE_BAKE:
                continue
            mats = []
            for slot in getattr(src, "material_slots", []):
                info = ins.materials.get(collect.material_key(slot.material))
                cls = info.cls if info else collect.material_info(slot.material).cls
                mats.append({"name": slot.material.name if slot.material else "",
                             "class": cls, "bake": bake_kind[cls]})
            res_name = naming.mesh_name(s.atlas_name) if join_map else naming.duplicate_name(src.name)
            srcs.append({"object": src.name, "result": res_name, "materials": mats})
        warnings = [f"{m.obj}: {m.text}" if m.obj else m.text for m in self.report.of(report.WARNING)]
        man = report.build_manifest(
            name=s.atlas_name, blender_version=bpy.app.version_string, preset_key=s.preset,
            tool_version=tool_version(),
            resolution=s.resolution, textures=tex_entries, sources=srcs,
            stats=self.result["stats"], warnings=warnings)
        if self.result.get("fbx"):
            man["fbx"] = os.path.basename(self.result["fbx"])
        weights = {o.name: o.garden_atlas_weight for o in self.sources
                   if abs(getattr(o, "garden_atlas_weight", 1.0) - 1.0) > 1e-6}
        if weights:
            man["weights"] = weights
        path = os.path.join(out_dir, naming.manifest_file(s.atlas_name))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(man, f, ensure_ascii=False, indent=2)
        self.tracker.files.append(path)
        self.result["manifest"] = path
