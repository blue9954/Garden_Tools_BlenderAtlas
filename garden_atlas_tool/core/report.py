"""Report messages and manifest construction."""

from dataclasses import dataclass, field

ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"
LEVELS = (ERROR, WARNING, INFO)


@dataclass
class Message:
    level: str
    text: str
    obj: str = ""

    def __str__(self):
        where = f"[{self.obj}] " if self.obj else ""
        return f"{self.level}: {where}{self.text}"


@dataclass
class Report:
    messages: list = field(default_factory=list)

    def add(self, level, text, obj=""):
        self.messages.append(Message(level, text, obj))

    def error(self, text, obj=""):
        self.add(ERROR, text, obj)

    def warning(self, text, obj=""):
        self.add(WARNING, text, obj)

    def info(self, text, obj=""):
        self.add(INFO, text, obj)

    def extend(self, msgs):
        self.messages.extend(msgs)

    def count(self, level):
        return sum(1 for m in self.messages if m.level == level)

    @property
    def has_errors(self):
        return self.count(ERROR) > 0

    def of(self, level):
        return [m for m in self.messages if m.level == level]


def build_manifest(*, name, blender_version, preset_key, resolution, textures,
                   sources, stats, warnings, tool_version=""):
    """Build the ``<name>_atlas.json`` dict (spec 7.4).

    textures: {suffix: {"file":..., "colorspace":..., ...}}
    sources: [{"object", "result", "materials": [{"name","class","bake"}]}]
    """
    return {
        "schema": 1,
        "name": name,
        "tool_version": tool_version,
        "blender_version": blender_version,
        "preset": preset_key,
        "resolution": [int(resolution[0]), int(resolution[1])],
        "textures": textures,
        "sources": sources,
        "stats": stats,
        "warnings": list(warnings),
    }


def texture_entry(spec, file_name, has_alpha, convention, bit_depth=8):
    entry = {"file": file_name, "colorspace": spec["colorspace"], "bit_depth": bit_depth}
    names = [c for c, _ in spec["rgb"]]
    if len(set(names)) == 1:
        entry["channels"] = "RGBA" if has_alpha else "RGB"
    else:
        entry["channels"] = dict(zip("RGB", names))
        if has_alpha:
            entry["channels"]["A"] = spec["alpha"][0]
    if names[0] == "Normal":
        entry["convention"] = convention
    return entry
