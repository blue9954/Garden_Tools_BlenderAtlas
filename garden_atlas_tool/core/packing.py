"""numpy channel packing and image helpers (no bpy).

Images are float32 arrays shaped (H, W, 4), values in stored (encoded) space,
matching ``Image.pixels`` layout (bottom row first).
"""

import numpy as np


def filled(width, height, rgba):
    a = np.empty((height, width, 4), dtype=np.float32)
    a[...] = np.asarray(rgba, dtype=np.float32)
    return a


def pack_texture(spec, channels, with_alpha):
    """Assemble one output texture from baked channel images.

    spec: preset texture entry; channels: {channel_name: (H,W,4) array}.
    Returns (H, W, 4) float32. Alpha is 1 unless ``with_alpha``.
    """
    first = next(iter(channels.values()))
    h, w = first.shape[:2]
    out = np.ones((h, w, 4), dtype=np.float32)
    for i, (name, comp) in enumerate(spec["rgb"]):
        out[..., i] = channels[name][..., comp]
    if with_alpha:
        name, comp = spec["alpha"]
        out[..., 3] = channels[name][..., comp]
    return out


def composite(base, layers):
    """Overlay ``layers`` [(image, mask)] onto base in order. mask: (H,W) bool."""
    out = base.copy()
    for img, mask in layers:
        out[mask] = img[mask]
    return out


def dilate(img, mask, iterations):
    """Extend covered pixels outward ``iterations`` times (4-neighbour).

    Pixels outside ``mask`` take the value of a covered neighbour. Returns
    (image, grown_mask). Used to recreate bake margins after compositing.
    """
    img = img.copy()
    mask = mask.copy()
    for _ in range(int(iterations)):
        grown = mask.copy()
        for axis, shift in ((0, 1), (0, -1), (1, 1), (1, -1)):
            src_mask = np.roll(mask, shift, axis=axis)
            src_img = np.roll(img, shift, axis=axis)
            # Do not wrap around the borders.
            if axis == 0:
                edge = 0 if shift == 1 else -1
                src_mask[edge, :] = False
            else:
                edge = 0 if shift == 1 else -1
                src_mask[:, edge] = False
            take = src_mask & ~grown
            img[take] = src_img[take]
            grown |= take
        if grown.sum() == mask.sum():
            break
        mask = grown
    return img, mask


def to_uint8(img):
    return np.clip(np.rint(np.asarray(img) * 255.0), 0, 255).astype(np.uint8)


def linear_to_srgb(x):
    x = np.asarray(x, dtype=np.float64)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(np.maximum(x, 0), 1 / 2.4) - 0.055)


def srgb_to_linear(x):
    x = np.asarray(x, dtype=np.float64)
    return np.where(x <= 0.04045, x / 12.92, np.power((x + 0.055) / 1.055, 2.4))
