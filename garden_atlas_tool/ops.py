"""Operators (thin: inspect -> pipeline -> report)."""

import json
import os
import traceback

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .bl import collect, duplicate
from .bl.pipeline import MODE_ALL, MODE_BAKE, MODE_UV, Pipeline, PipelineError
from .core import report as core_report
from .core import settings as core_settings


def _props(context):
    return context.scene.garden_atlas


def _settings(context):
    p = _props(context)
    s = core_settings.AtlasSettings.from_object(p)
    if not s.output_dir:
        prefs = context.preferences.addons.get(__package__)
        if prefs is not None:
            s.output_dir = prefs.preferences.default_output_dir
    return s


def store_report(props, messages):
    props.report.clear()
    for m in messages:
        it = props.report.add()
        it.level, it.text, it.obj = m.level, m.text, m.obj
    props.n_errors = sum(1 for m in messages if m.level == core_report.ERROR)
    props.n_warnings = sum(1 for m in messages if m.level == core_report.WARNING)


def print_report(messages):
    for m in messages:
        print("[Garden Atlas]", m)


def _store_result(props, pipe):
    st = pipe.result.get("stats", {})
    props.uv_usage = st.get("uv_usage", 0.0)
    td = st.get("texel_density_px_m", {})
    props.density_min = td.get("min", 0.0)
    props.density_avg = td.get("avg", 0.0)
    props.density_max = td.get("max", 0.0)
    props.bake_seconds = st.get("bake_seconds", 0.0)
    if pipe.result.get("output_dir"):
        props.last_output_dir = pipe.result["output_dir"]
    store_report(props, pipe.report.messages)
    print_report(pipe.report.messages)


def _poll_object_mode(context):
    return context.mode == "OBJECT" and context.scene is not None


class GARDENATLAS_OT_analyze(Operator):
    bl_idname = "garden_atlas.analyze"
    bl_label = "검사"
    bl_description = "선택한 오브젝트와 설정을 검사합니다"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _poll_object_mode(context) and bool(context.selected_objects)

    def execute(self, context):
        props = _props(context)
        s = _settings(context)
        rep, _ = core_settings.validate(s, bpy.data.filepath)
        ins = collect.inspect(context.selected_objects, s)
        msgs = rep.messages + ins.report.messages
        if duplicate.existing_result(s.atlas_name):
            msgs.append(core_report.Message(core_report.INFO,
                                            f"'{s.atlas_name}' 결과가 이미 있습니다. 실행하면 교체합니다"))
        store_report(props, msgs)
        print_report(msgs)
        props.analyzed = True
        props.n_objects = len(ins.targets)
        props.n_materials = len(ins.materials)
        self.report({"INFO"}, f"오브젝트 {len(ins.targets)}, 머티리얼 {len(ins.materials)}, "
                              f"경고 {props.n_warnings}, 오류 {props.n_errors}")
        return {"FINISHED"}


class _PipelineOperator:
    """Shared modal/synchronous runner."""
    mode = MODE_ALL
    _timer = None
    _gen = None
    _pipe = None

    @classmethod
    def poll(cls, context):
        if not _poll_object_mode(context) or _props(context).running:
            return False
        if cls.mode == MODE_BAKE:
            return True
        return bool(context.selected_objects)

    def invoke(self, context, event):
        s = _settings(context)
        if self.mode != MODE_BAKE and duplicate.existing_result(s.atlas_name):
            return context.window_manager.invoke_confirm(
                self, event, title="결과 교체",
                message=f"'{s.atlas_name}' 결과가 이미 있습니다. 지우고 새로 만들까요?")
        return self.execute(context)

    def execute(self, context):
        self._pipe = Pipeline(context, _settings(context), self.mode)
        if bpy.app.background or context.window is None:
            return self._run_sync(context)
        props = _props(context)
        props.running = True
        self._gen = self._pipe.steps()
        wm = context.window_manager
        wm.progress_begin(0, 100)
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _run_sync(self, context):
        props = _props(context)
        try:
            self._pipe.run()
        except Exception as e:  # report, never swallow silently
            _store_result(props, self._pipe)
            if not isinstance(e, PipelineError):
                traceback.print_exc()
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _store_result(props, self._pipe)
        return {"FINISHED"}

    def _finish(self, context, ok, msg=""):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        wm.progress_end()
        props = _props(context)
        props.running = False
        props.status = ""
        _store_result(props, self._pipe)
        for area in context.screen.areas if context.screen else []:
            area.tag_redraw()
        if ok:
            self.report({"INFO"}, "Garden Atlas: 완료")
            return {"FINISHED"}
        self.report({"ERROR"} if msg else {"WARNING"}, msg or "취소했습니다")
        return {"CANCELLED"}

    def modal(self, context, event):
        if event.type == "ESC" and event.value == "PRESS":
            self._gen.close()  # GeneratorExit -> rollback inside the pipeline
            return self._finish(context, False)
        if event.type != "TIMER":
            return {"RUNNING_MODAL"}
        try:
            label, frac = next(self._gen)
        except StopIteration:
            return self._finish(context, True)
        except Exception as e:
            return self._finish(context, False, str(e))
        props = _props(context)
        props.status = label
        context.window_manager.progress_update(int(frac * 100))
        context.workspace.status_text_set(f"Garden Atlas: {label}  (ESC 취소)")
        for area in context.screen.areas:
            area.tag_redraw()
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        if self._gen is not None:
            self._gen.close()
        self._finish(context, False)


def _clear_status(context):
    try:
        context.workspace.status_text_set(None)
    except AttributeError:
        pass


class GARDENATLAS_OT_run_all(_PipelineOperator, Operator):
    bl_idname = "garden_atlas.run_all"
    bl_label = "아틀라스 생성"
    bl_description = "UV 패킹, 베이크, 저장, 결과 머티리얼 적용을 차례로 실행합니다"
    bl_options = {"REGISTER"}
    mode = MODE_ALL

    def _finish(self, context, ok, msg=""):
        _clear_status(context)
        return super()._finish(context, ok, msg)


class GARDENATLAS_OT_build_uv(_PipelineOperator, Operator):
    bl_idname = "garden_atlas.build_uv"
    bl_label = "UV 미리보기"
    bl_description = "복제본과 아틀라스 UV만 만들고 베이크하지 않습니다"
    bl_options = {"REGISTER"}
    mode = MODE_UV

    def _finish(self, context, ok, msg=""):
        _clear_status(context)
        return super()._finish(context, ok, msg)


class GARDENATLAS_OT_bake(_PipelineOperator, Operator):
    bl_idname = "garden_atlas.bake"
    bl_label = "베이크만"
    bl_description = "UV 미리보기로 만든 복제본으로 베이크부터 실행합니다"
    bl_options = {"REGISTER"}
    mode = MODE_BAKE

    def _finish(self, context, ok, msg=""):
        _clear_status(context)
        return super()._finish(context, ok, msg)


class GARDENATLAS_OT_select_issue(Operator):
    bl_idname = "garden_atlas.select_issue"
    bl_label = "오브젝트 선택"
    bl_description = "리포트 항목의 오브젝트를 선택합니다"
    bl_options = {"REGISTER", "UNDO"}

    obj: StringProperty()

    @classmethod
    def poll(cls, context):
        return _poll_object_mode(context)

    def execute(self, context):
        ob = context.view_layer.objects.get(self.obj)
        if ob is None:
            self.report({"WARNING"}, f"오브젝트를 찾을 수 없습니다: {self.obj}")
            return {"CANCELLED"}
        for o in context.view_layer.objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        return {"FINISHED"}


class GARDENATLAS_OT_open_output(Operator):
    bl_idname = "garden_atlas.open_output"
    bl_label = "출력 폴더 열기"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return bool(_props(context).last_output_dir)

    def execute(self, context):
        path = _props(context).last_output_dir
        if not os.path.isdir(path):
            self.report({"ERROR"}, f"폴더가 없습니다: {path}")
            return {"CANCELLED"}
        bpy.ops.wm.path_open(filepath=path)
        return {"FINISHED"}


def apply_values(props, values):
    """Write preset values to the PropertyGroup. Resolution first: its update
    callback resets gap/margin, which the preset may then override."""
    for key in ("width", "height"):
        if key in values:
            setattr(props, key, str(values[key]))
    for key, val in values.items():
        if key not in ("width", "height"):
            setattr(props, key, val)


class GARDENATLAS_OT_preset_save(Operator, ExportHelper):
    bl_idname = "garden_atlas.preset_save"
    bl_label = "프리셋 저장"
    bl_description = "현재 설정을 JSON 프리셋으로 저장합니다 (아틀라스 이름 제외)"
    bl_options = {"REGISTER"}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        data = core_settings.to_preset(_settings(context))
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            self.report({"ERROR"}, f"프리셋 저장 실패: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"프리셋 저장: {self.filepath}")
        return {"FINISHED"}


class GARDENATLAS_OT_preset_load(Operator, ImportHelper):
    bl_idname = "garden_atlas.preset_load"
    bl_label = "프리셋 불러오기"
    bl_description = "JSON 프리셋에서 설정을 불러옵니다"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        try:
            with open(self.filepath, encoding="utf-8") as f:
                values, warnings = core_settings.from_preset(json.load(f))
        except (OSError, ValueError) as e:
            self.report({"ERROR"}, f"프리셋 불러오기 실패: {e}")
            return {"CANCELLED"}
        apply_values(_props(context), values)
        for w in warnings:
            print("[Garden Atlas] preset:", w)
        self.report({"WARNING"} if warnings else {"INFO"},
                    f"프리셋 {len(values)}개 항목 적용" + (f", {len(warnings)}개 건너뜀" if warnings else ""))
        return {"FINISHED"}


classes = (GARDENATLAS_OT_analyze, GARDENATLAS_OT_preset_save, GARDENATLAS_OT_preset_load, GARDENATLAS_OT_run_all, GARDENATLAS_OT_build_uv,
           GARDENATLAS_OT_bake, GARDENATLAS_OT_select_issue, GARDENATLAS_OT_open_output)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
