"""Settings -> bake pass list (spec 6)."""

import dataclasses
from dataclasses import dataclass

from .settings import AtlasSettings

# How complex (non-Principled) materials are handled in a proxy pass.
COMPLEX_NATIVE = "NATIVE"      # the native pass type works for every material
COMPLEX_DIFFUSE = "DIFFUSE"    # needs a separate DIFFUSE colour sub-bake
COMPLEX_CONST = "CONST"        # emit a constant value


@dataclass(frozen=True)
class BakePass:
    channel: str                 # BaseColor, Opacity, Roughness, Metallic, Normal, AO, Emissive
    bake_type: str | None        # EMIT / ROUGHNESS / NORMAL / AO; None = fill only
    proxy_socket: str | None     # Principled input wired to Emission for simple materials
    complex_mode: str            # COMPLEX_*
    complex_const: float         # value for COMPLEX_CONST
    colorspace: str              # "sRGB" or "Non-Color"
    fill: tuple                  # RGBA background
    samples: int
    float_buffer: bool = False   # 16-bit capable target (normal_16bit)

    @property
    def is_fill_only(self):
        return self.bake_type is None


CHANNEL_ORDER = ("BaseColor", "Opacity", "Roughness", "Metallic", "Normal", "AO", "Emissive")


def make_plan(s: AtlasSettings, *, has_alpha=False, has_emission=False) -> list[BakePass]:
    """Return the passes in execution order.

    Disabled channels that are still needed by an output texture become
    fill-only passes with the channel's default value.
    """
    smp = s.samples
    p = []

    def add(channel, enabled, bake_type, proxy, cmode, cconst, cs, fill, samples=smp):
        if enabled:
            p.append(BakePass(channel, bake_type, proxy, cmode, cconst, cs, fill, samples))
        else:
            p.append(BakePass(channel, None, None, COMPLEX_NATIVE, 0.0, cs, fill, 0))

    add("BaseColor", s.use_basecolor, "EMIT", "Base Color", COMPLEX_DIFFUSE, 0.0,
        "sRGB", (0.0, 0.0, 0.0, 1.0))
    if s.use_opacity and has_alpha:
        add("Opacity", True, "EMIT", "Alpha", COMPLEX_CONST, 1.0,
            "Non-Color", (1.0, 1.0, 1.0, 1.0))
    add("Roughness", s.use_roughness, "ROUGHNESS", None, COMPLEX_NATIVE, 0.0,
        "Non-Color", (0.5, 0.5, 0.5, 1.0))
    add("Metallic", s.use_metallic, "EMIT", "Metallic", COMPLEX_CONST, 0.0,
        "Non-Color", (0.0, 0.0, 0.0, 1.0))
    add("Normal", s.use_normal, "NORMAL", None, COMPLEX_NATIVE, 0.0,
        "Non-Color", (0.5, 0.5, 1.0, 1.0))
    if s.normal_16bit:
        p[-1] = dataclasses.replace(p[-1], float_buffer=True)
    add("AO", s.use_ao_bake, "AO", None, COMPLEX_NATIVE, 0.0,
        "Non-Color", (1.0, 1.0, 1.0, 1.0), s.ao_samples)
    if s.use_emissive and has_emission:
        add("Emissive", True, "EMIT", None, COMPLEX_NATIVE, 0.0,
            "sRGB", (0.0, 0.0, 0.0, 1.0))
    return p


def output_textures(preset: dict, passes: list[BakePass]) -> list[dict]:
    """Preset texture specs that should be written for these passes."""
    have = {bp.channel for bp in passes}
    out = []
    for spec in preset["textures"]:
        if spec["when"] == "emissive" and "Emissive" not in have:
            continue
        out.append(spec)
    return out


def texture_has_alpha(spec: dict, passes: list[BakePass]) -> bool:
    alpha = spec.get("alpha")
    return bool(alpha) and alpha[0] in {bp.channel for bp in passes}


def texture_is_float(spec: dict, passes: list[BakePass]) -> bool:
    """True when every channel of the texture comes from a float pass."""
    flt = {bp.channel for bp in passes if bp.float_buffer}
    return all(c in flt for c, _ in spec["rgb"])
