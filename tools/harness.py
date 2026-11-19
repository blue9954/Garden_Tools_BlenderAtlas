#!/usr/bin/env python3
"""Garden Atlas Tool test harness (spec 14).

    python tools/harness.py              # L0 -> L1 -> L2 -> L3
    python tools/harness.py L0 L1        # selected levels
    BLENDER=/path/to/blender python tools/harness.py

L0 static   : manifest, syntax, naming, register/unregister symmetry, layer rules
L1 unit     : tests/unit (pytest if available, else unittest via Blender's Python)
L2 integ.   : tests/integration/run_integration.py inside `blender -b`
L3 package  : blender --command extension validate / build
ACC         : (opt-in) tests/integration/render_compare.py, 5 objects / 11 materials,
              source vs atlas rendered and diffed. `python tools/harness.py ACC`
              GA_DEVICE=METAL|CUDA|OPTIX runs L2 on the GPU.
UE          : (opt-in) Blender exports SM_/T_ + FBX, UnrealEditor-Cmd imports them
              headless and checks slots, UV0, scale, texture settings, material.
              `python tools/harness.py UE`  (UE_ROOT=/path/to/UE_5.x)
L4 manual   : printed as BLOCKED (real GUI + Unreal import must be done by a person)
"""

import ast
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "garden_atlas_tool")
DIST = os.path.join(ROOT, "dist")


def find_blender():
    cands = [os.environ.get("BLENDER"), shutil.which("blender"),
             "/Applications/Blender.app/Contents/MacOS/Blender",
             r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


# ------------------------------------------------------------------ L0
REQUIRED_MANIFEST = ("schema_version", "id", "version", "name", "tagline", "maintainer",
                     "type", "blender_version_min", "license")
CLASS_RE = re.compile(r"^[A-Z][A-Z0-9_]*_(OT|PT|PG|AP|MT|UL)_[A-Za-z0-9_]+$")


def _parse_manifest(path):
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except ImportError:  # Python < 3.11: minimal key = value parser
        data = {}
        for line in open(path, encoding="utf-8"):
            m = re.match(r'^\s*([A-Za-z_]+)\s*=\s*(.+?)\s*$', line)
            if m:
                data[m.group(1)] = m.group(2).strip('"')
        return data


def level0():
    errors = []
    man_path = os.path.join(PKG, "blender_manifest.toml")
    if not os.path.isfile(man_path):
        return ["blender_manifest.toml missing"]
    man = _parse_manifest(man_path)
    for k in REQUIRED_MANIFEST:
        if k not in man:
            errors.append(f"manifest: missing '{k}'")
    if str(man.get("blender_version_min", "")) < "5.1.0":
        errors.append("manifest: blender_version_min must be >= 5.1.0")
    if man.get("id") != "garden_atlas_tool":
        errors.append("manifest: id must be garden_atlas_tool")
    if len(str(man.get("tagline", ""))) > 64:
        errors.append("manifest: tagline longer than 64 chars")
    perms = man.get("permissions", {})
    if isinstance(perms, dict):
        for k, v in perms.items():
            if len(str(v)) > 64:
                errors.append(f"manifest: permission '{k}' reason longer than 64 chars")

    for path in sorted(glob.glob(os.path.join(PKG, "**", "*.py"), recursive=True)):
        rel = os.path.relpath(path, ROOT)
        src = open(path, encoding="utf-8").read()
        try:
            tree = ast.parse(src, rel)
        except SyntaxError as e:
            errors.append(f"{rel}:{e.lineno}: syntax error: {e.msg}")
            continue
        in_core = os.sep + "core" + os.sep in path
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                level = getattr(node, "level", 0)
                for n in names:
                    if in_core and (n == "bpy" or n.startswith("bpy.") or n in ("mathutils", "bmesh")):
                        errors.append(f"{rel}:{node.lineno}: core/ must not import {n}")
                    if level == 0 and n.split(".")[0] == "garden_atlas_tool":
                        errors.append(f"{rel}:{node.lineno}: use relative imports inside the package")
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "bl_info":
                        errors.append(f"{rel}:{node.lineno}: bl_info is not allowed (manifest only)")
                    if isinstance(t, ast.Attribute) and t.attr == "use_nodes":
                        errors.append(f"{rel}:{node.lineno}: do not write Material.use_nodes")
            if isinstance(node, ast.ClassDef):
                bases = {b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "") for b in node.bases}
                if bases & {"Operator", "Panel", "PropertyGroup", "AddonPreferences", "Menu", "UIList"}:
                    if not CLASS_RE.match(node.name):
                        errors.append(f"{rel}:{node.lineno}: class name '{node.name}' breaks PREFIX_XX_name")
                    for st in node.body:
                        if (isinstance(st, ast.Assign) and any(getattr(t, "id", "") == "bl_idname" for t in st.targets)
                                and "Operator" in bases and isinstance(st.value, ast.Constant)):
                            if not str(st.value.value).startswith("garden_atlas."):
                                errors.append(f"{rel}:{st.lineno}: operator id must start with garden_atlas.")
        funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        if ("register" in funcs) != ("unregister" in funcs):
            errors.append(f"{rel}: register/unregister not paired")
        if "register" in funcs:
            reg = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "register")
            unreg = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "unregister")
            if "reversed" not in ast.unparse(unreg) and "register_class" in ast.unparse(reg):
                errors.append(f"{rel}: unregister should unregister classes in reverse order")
            sets = re.findall(r"bpy\.types\.(\w+)\.(\w+)\s*=", ast.unparse(reg))
            for typ, attr in sets:
                if f"del bpy.types.{typ}.{attr}" not in ast.unparse(unreg):
                    errors.append(f"{rel}: bpy.types.{typ}.{attr} is not deleted in unregister")
    return errors


# ------------------------------------------------------------------ runners
def _have_numpy_pytest():
    try:
        import numpy  # noqa: F401
        import pytest  # noqa: F401
        return True
    except ImportError:
        return False


def level1(blender):
    if _have_numpy_pytest():
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", os.path.join(ROOT, "tests", "unit")])
        return [] if r.returncode == 0 else [f"pytest exit {r.returncode}"]
    if not blender:
        return ["BLOCKED: no numpy/pytest here and no Blender to borrow Python from"]
    expr = ("import sys, unittest; sys.argv=['x']; "
            f"s=unittest.defaultTestLoader.discover({os.path.join(ROOT, 'tests', 'unit')!r}); "
            "r=unittest.TextTestRunner(verbosity=1).run(s); sys.exit(0 if r.wasSuccessful() else 1)")
    r = subprocess.run([blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python-expr", expr])
    return [] if r.returncode == 0 else [f"unit tests failed (exit {r.returncode})"]


def level2(blender, select):
    if not blender:
        return ["BLOCKED: Blender executable not found (set BLENDER=...)"]
    out = tempfile.mktemp(suffix=".json")
    env = dict(os.environ, GA_RESULT_JSON=out)
    cmd = [blender, "-b", "--factory-startup", "--python-exit-code", "1",
           "--python", os.path.join(ROOT, "tests", "integration", "run_integration.py")]
    if select:
        cmd += ["--"] + select
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    lines = [l for l in r.stdout.splitlines() if l.startswith(("[PASS]", "[FAIL]", "[ERROR]", "L2", "    "))]
    print("\n".join(lines))
    if not os.path.isfile(out):
        print(r.stdout[-3000:], r.stderr[-3000:])
        return [f"integration run crashed (exit {r.returncode})"]
    res = json.load(open(out, encoding="utf-8"))
    os.remove(out)
    return [f"{x['id']}: {x['status']} {x['msg']}" for x in res if x["status"] != "PASS"]


def level3(blender):
    if not blender:
        return ["BLOCKED: Blender executable not found"]
    errs = []
    r = subprocess.run([blender, "--factory-startup", "--command", "extension", "validate", PKG], capture_output=True, text=True)
    print(r.stdout.strip()[-1500:])
    if r.returncode != 0:
        errs.append(f"extension validate failed: {r.stderr.strip()[-500:]}")
    os.makedirs(DIST, exist_ok=True)
    r = subprocess.run([blender, "--factory-startup", "--command", "extension", "build", "--source-dir", PKG,
                        "--output-dir", DIST], capture_output=True, text=True)
    print(r.stdout.strip()[-1500:])
    if r.returncode != 0:
        errs.append(f"extension build failed: {r.stderr.strip()[-500:]}")
    return errs


def level_acc(blender):
    if not blender:
        return ["BLOCKED: Blender executable not found"]
    out = os.path.join(ROOT, "dist", "acceptance")
    shutil.rmtree(out, ignore_errors=True)
    r = subprocess.run([blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python",
                        os.path.join(ROOT, "tests", "integration", "render_compare.py"), "--", out],
                       capture_output=True, text=True)
    print("\n".join(l for l in r.stdout.splitlines() if l.startswith("[CMP]")))
    return [] if r.returncode == 0 else [f"render comparison failed (exit {r.returncode})"]


UPROJECT = {"FileVersion": 3, "EngineAssociation": "", "Category": "", "Description": "",
            "Plugins": [{"Name": "PythonScriptPlugin", "Enabled": True},
                        {"Name": "EditorScriptingUtilities", "Enabled": True}]}


def find_unreal_cmd():
    roots = [os.environ.get("UE_ROOT")] + sorted(glob.glob("/Users/Shared/Epic Games/UE_5.*"), reverse=True) \
        + sorted(glob.glob(r"C:\Program Files\Epic Games\UE_5.*"), reverse=True)
    for r in roots:
        if not r:
            continue
        for rel in ("Engine/Binaries/Mac/UnrealEditor-Cmd", "Engine/Binaries/Win64/UnrealEditor-Cmd.exe",
                    "Engine/Binaries/Linux/UnrealEditor-Cmd"):
            p = os.path.join(r, rel)
            if os.path.isfile(p):
                return p
    return None


def level_ue(blender):
    ue = find_unreal_cmd()
    if not blender or not ue:
        return [f"BLOCKED: {'Blender' if not blender else 'UnrealEditor-Cmd (set UE_ROOT)'} not found"]
    work = os.path.join(DIST, "ue_check")
    shutil.rmtree(work, ignore_errors=True)
    assets, proj = os.path.join(work, "assets"), os.path.join(work, "proj")
    os.makedirs(assets)
    os.makedirs(os.path.join(proj, "Content"))
    with open(os.path.join(proj, "GACheck.uproject"), "w") as f:
        json.dump(UPROJECT, f)
    r = subprocess.run([blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python",
                        os.path.join(ROOT, "tests", "ue", "make_assets.py"), "--", assets],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return [f"Blender export failed: {r.stderr[-500:]}"]
    result = os.path.join(work, "ue_result.json")
    env = dict(os.environ, GA_UE_IN=assets, GA_UE_NAME="UECheck", GA_UE_RESULT=result)
    print(f"UnrealEditor-Cmd: {ue}")
    with open(os.path.join(work, "ue_log.txt"), "w") as log:
        subprocess.run([ue, os.path.join(proj, "GACheck.uproject"), "-run=pythonscript",
                        f"-script={os.path.join(ROOT, 'tests', 'ue', 'ue_check.py')}", "-unattended",
                        "-nop4", "-nosplash", "-nullrhi", "-stdout", "-FullStdOutLogOutput"],
                       env=env, stdout=log, stderr=subprocess.STDOUT, timeout=3600)
    if not os.path.isfile(result):
        return [f"Unreal produced no result (see {work}/ue_log.txt)"]
    res = json.load(open(result, encoding="utf-8"))
    for k, v in res["checks"].items():
        print(f"  [{'PASS' if v['pass'] else 'FAIL'}] {k} {v['detail']}")
    errs = [f"{k}: {v['detail']}" for k, v in res["checks"].items() if not v["pass"]]
    errs += [e.strip().splitlines()[-1] for e in res["errors"]]
    if len(res["checks"]) < 10:
        errs.append(f"only {len(res['checks'])} checks ran")
    return errs


def main():
    args = [a for a in sys.argv[1:]]
    levels = [a for a in args if a in ("L0", "L1", "L2", "L3", "ACC", "UE")] or ["L0", "L1", "L2", "L3"]
    select = [a for a in args if a not in levels]
    blender = find_blender()
    print(f"Blender: {blender or 'not found'}")
    summary = []
    for lv in levels:
        print(f"\n===== {lv} =====", flush=True)
        t0 = time.perf_counter()
        fn = {"L0": level0, "L1": lambda: level1(blender), "L2": lambda: level2(blender, select),
              "L3": lambda: level3(blender), "ACC": lambda: level_acc(blender),
              "UE": lambda: level_ue(blender)}[lv]
        errs = fn()
        dt = time.perf_counter() - t0
        blocked = errs and all(e.startswith("BLOCKED") for e in errs)
        status = "PASS" if not errs else ("BLOCKED" if blocked else "FAIL")
        for e in errs:
            print("  -", e)
        summary.append((lv, status, dt))
    summary.append(("L4", "BLOCKED", 0.0))
    print("\n===== summary =====")
    for lv, st, dt in summary:
        note = ("  (scripted parts: gui_l4.py, ACC, UE; still manual: CUDA/OptiX, UE visual"
                " -> docs/L4_checklist.md)") if lv == "L4" else ""
        print(f"{lv}: {st:8s} {dt:6.1f}s{note}")
    sys.exit(0 if all(st in ("PASS", "BLOCKED") for lv, st, _ in summary if lv != "L4")
             and not any(st == "FAIL" for _, st, _ in summary) else 1)


if __name__ == "__main__":
    main()
