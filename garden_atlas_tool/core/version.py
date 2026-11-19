"""Tool version, read from blender_manifest.toml (single source of truth)."""

import os
import re

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "blender_manifest.toml")


def tool_version() -> str:
    try:
        with open(_MANIFEST, encoding="utf-8") as f:
            for line in f:
                m = re.match(r'^\s*version\s*=\s*"([^"]+)"', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return "unknown"
