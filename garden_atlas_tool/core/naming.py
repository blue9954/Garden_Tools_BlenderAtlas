"""Atlas naming rules (spec 7.2)."""

import re

_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

FILE_EXT = {"PNG": ".png", "TARGA": ".tga"}


def validate_name(name: str) -> str | None:
    """Return an error message, or None when the name is valid."""
    if not name:
        return "아틀라스 이름이 비어 있습니다"
    if not _NAME_RE.match(name):
        bad = sorted({c for c in name if not re.match(r"[A-Za-z0-9_]", c)})
        return f"아틀라스 이름에 허용되지 않는 문자: {' '.join(repr(c) for c in bad)} (영문·숫자·밑줄만)"
    return None


def texture_name(name: str, suffix: str) -> str:
    return f"T_{name}_{suffix}"


def texture_file(name: str, suffix: str, file_format: str = "PNG") -> str:
    return texture_name(name, suffix) + FILE_EXT[file_format]


def material_name(name: str) -> str:
    return f"M_{name}"


def mesh_name(name: str) -> str:
    return f"SM_{name}"


def collection_name(name: str) -> str:
    return f"ATLAS_{name}"


def manifest_file(name: str) -> str:
    return f"{name}_atlas.json"


def duplicate_name(source_name: str) -> str:
    return f"{source_name}_atlas"


TEMP_PREFIX = "__gatmp_"


def temp_name(base: str) -> str:
    return TEMP_PREFIX + base
