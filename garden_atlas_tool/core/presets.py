"""Engine presets as data (spec 7.1).

A texture entry maps output channels R/G/B/A to (bake channel, component).
Component 0..2 picks RGB of the baked image; ``None`` as channel means "omit"
(for A: save RGB only). ``when`` names a condition that must hold for the
texture to be written: "always", "emissive" (an emitting source exists).
``alpha`` is optional: the channel placed in A only when it was baked.
"""

PRESETS = {
    "UNREAL": {
        "label": "Unreal",
        "normal_g": "NEG_Y",
        "normal_convention": "DirectX",
        "textures": [
            {
                "suffix": "BC",
                "colorspace": "sRGB",
                "when": "always",
                "rgb": [("BaseColor", 0), ("BaseColor", 1), ("BaseColor", 2)],
                "alpha": ("Opacity", 0),
            },
            {
                "suffix": "N",
                "colorspace": "Linear",
                "when": "always",
                "rgb": [("Normal", 0), ("Normal", 1), ("Normal", 2)],
            },
            {
                "suffix": "ORM",
                "colorspace": "Linear",
                "when": "always",
                "rgb": [("AO", 0), ("Roughness", 0), ("Metallic", 0)],
            },
            {
                "suffix": "E",
                "colorspace": "sRGB",
                "when": "emissive",
                "rgb": [("Emissive", 0), ("Emissive", 1), ("Emissive", 2)],
            },
        ],
    },
}


def preset_items():
    return [(k, v["label"], "") for k, v in PRESETS.items()]


def get_preset(key: str) -> dict:
    return PRESETS[key]
