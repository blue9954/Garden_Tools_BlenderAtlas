"""Settings dataclass, validation and spacing rules (spec 7.3, 9)."""

import os
from dataclasses import dataclass, fields

from . import naming
from .presets import PRESETS
from .report import Report

RESOLUTIONS = (512, 1024, 2048, 4096, 8192)

# resolution -> (bake margin px, island gap px)
_SPACING = {512: (4, 8), 1024: (4, 8), 2048: (8, 16), 4096: (16, 32), 8192: (32, 64)}

DENSITY_MODES = ("UNIFORM", "SOURCE")
OVERLAP_MODES = ("SEPARATE", "KEEP_STACKED", "REUNWRAP")
FILE_FORMATS = ("PNG", "TARGA")


def default_spacing(width: int, height: int) -> tuple[int, int]:
    """Default (bake_margin_px, island_gap_px) for the larger side."""
    res = max(width, height)
    for r in sorted(_SPACING):
        if res <= r:
            return _SPACING[r]
    return _SPACING[max(_SPACING)]


@dataclass
class AtlasSettings:
    atlas_name: str = "Atlas"
    width: int = 4096
    height: int = 4096
    preset: str = "UNREAL"
    density_mode: str = "UNIFORM"
    overlap_mode: str = "SEPARATE"
    island_gap_px: int = 32
    bake_margin_px: int = 16
    allow_rotate: bool = True
    use_basecolor: bool = True
    use_normal: bool = True
    use_roughness: bool = True
    use_metallic: bool = True
    use_ao_bake: bool = False
    ao_isolate: bool = True
    use_emissive: bool = False
    use_opacity: bool = True
    samples: int = 8
    ao_samples: int = 64
    output_dir: str = "//textures/"
    file_format: str = "PNG"
    apply_modifiers: bool = True
    apply_scale: bool = True
    atlas_uv_only: bool = True
    join_result: bool = False
    write_manifest: bool = True
    normal_16bit: bool = False
    export_fbx: bool = False

    @classmethod
    def from_object(cls, obj):
        """Copy matching attributes from any object (e.g. a PropertyGroup)."""
        kw = {}
        for f in fields(cls):
            if hasattr(obj, f.name):
                val = getattr(obj, f.name)
                if f.name in ("width", "height"):
                    val = int(val)
                kw[f.name] = val
        return cls(**kw)

    @property
    def resolution(self):
        return (self.width, self.height)


def resolve_output_dir(output_dir: str, blend_filepath: str) -> tuple[str | None, str | None]:
    """Resolve a Blender-style path. Returns (abs_path, error)."""
    if not output_dir:
        return None, "출력 폴더가 비어 있습니다"
    if output_dir.startswith("//"):
        if not blend_filepath:
            return None, ".blend 파일이 저장되지 않아 상대 경로(//)를 쓸 수 없습니다. 먼저 저장하세요"
        base = os.path.dirname(blend_filepath)
        return os.path.normpath(os.path.join(base, output_dir[2:])), None
    return os.path.normpath(os.path.abspath(os.path.expanduser(output_dir))), None


def ensure_writable_dir(path: str) -> str | None:
    """Create the folder if needed; return an error message or None."""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        return f"출력 폴더를 만들 수 없습니다: {path} ({e.strerror})"
    if not os.access(path, os.W_OK):
        return f"출력 폴더에 쓸 수 없습니다: {path}"
    return None


def validate(s: AtlasSettings, blend_filepath: str = "", check_output=True) -> tuple[Report, str | None]:
    """Validate settings. Returns (report, resolved output dir)."""
    rep = Report()
    err = naming.validate_name(s.atlas_name)
    if err:
        rep.error(err)
    if s.width not in RESOLUTIONS or s.height not in RESOLUTIONS:
        rep.error(f"지원하지 않는 해상도: {s.width}x{s.height}")
    if s.preset not in PRESETS:
        rep.error(f"알 수 없는 프리셋: {s.preset}")
    if s.density_mode not in DENSITY_MODES:
        rep.error(f"알 수 없는 크기 기준: {s.density_mode}")
    if s.overlap_mode not in OVERLAP_MODES:
        rep.error(f"알 수 없는 겹침 처리: {s.overlap_mode}")
    if s.file_format not in FILE_FORMATS:
        rep.error(f"알 수 없는 포맷: {s.file_format}")
    if s.island_gap_px < 0 or s.bake_margin_px < 0:
        rep.error("간격과 마진은 0 이상이어야 합니다")
    elif s.island_gap_px < 2 * s.bake_margin_px:
        rep.warning(
            f"아일랜드 간격({s.island_gap_px}px)이 베이크 마진의 2배({2 * s.bake_margin_px}px)보다 작습니다. "
            "마진이 이웃 아일랜드에 번질 수 있습니다")
    if s.normal_16bit and s.file_format != "PNG":
        rep.warning("16비트 노멀은 PNG에서만 저장됩니다. TGA는 8비트로 저장합니다")
    if s.samples < 1 or s.ao_samples < 1:
        rep.error("샘플 수는 1 이상이어야 합니다")
    out = None
    if check_output:
        out, err = resolve_output_dir(s.output_dir, blend_filepath)
        if err:
            rep.error(err)
        else:
            err = ensure_writable_dir(out)
            if err:
                rep.error(err)
    return rep, out


# ---------------------------------------------------------------- presets (FR-48)
PRESET_KIND = "garden_atlas_settings"
PRESET_SCHEMA = 1
# Per-asset values that a reusable preset must not carry.
PRESET_EXCLUDE = ("atlas_name",)

_CHOICES = {
    "width": RESOLUTIONS, "height": RESOLUTIONS, "density_mode": DENSITY_MODES,
    "overlap_mode": OVERLAP_MODES, "file_format": FILE_FORMATS,
}


def to_preset(s: AtlasSettings) -> dict:
    values = {f.name: getattr(s, f.name) for f in fields(s) if f.name not in PRESET_EXCLUDE}
    return {"kind": PRESET_KIND, "schema": PRESET_SCHEMA, "settings": values}


def from_preset(data) -> tuple[dict, list]:
    """Validate a preset dict. Returns (values to apply, warnings).

    Unknown keys, wrong types and out-of-range choices are skipped with a
    warning so an older/newer preset still loads what it can.
    """
    if not isinstance(data, dict) or data.get("kind") != PRESET_KIND:
        raise ValueError("Garden Atlas 프리셋 파일이 아닙니다")
    if data.get("schema", 0) > PRESET_SCHEMA:
        raise ValueError(f"지원하지 않는 프리셋 버전: {data.get('schema')}")
    src = data.get("settings")
    if not isinstance(src, dict):
        raise ValueError("프리셋에 settings 항목이 없습니다")
    types = {f.name: f.type for f in fields(AtlasSettings)}
    default = AtlasSettings()
    out, warnings = {}, []
    for key, val in src.items():
        if key in PRESET_EXCLUDE:
            continue
        if key not in types:
            warnings.append(f"알 수 없는 항목을 건너뜀: {key}")
            continue
        want = type(getattr(default, key))
        if want is bool:
            ok = isinstance(val, bool)
        elif want is int:
            ok = isinstance(val, int) and not isinstance(val, bool)
        elif want is float:
            ok = isinstance(val, (int, float)) and not isinstance(val, bool)
        else:
            ok = isinstance(val, want)
        if not ok:
            warnings.append(f"형식이 맞지 않아 건너뜀: {key}={val!r}")
            continue
        if key in _CHOICES and val not in _CHOICES[key]:
            warnings.append(f"허용되지 않는 값이라 건너뜀: {key}={val!r}")
            continue
        if key == "preset" and val not in PRESETS:
            warnings.append(f"알 수 없는 엔진 프리셋이라 건너뜀: {val!r}")
            continue
        out[key] = val
    return out, warnings
