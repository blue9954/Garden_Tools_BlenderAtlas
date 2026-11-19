"""Bake target images, numpy I/O and saving (FR-32, FR-40)."""

import os

import bpy
import numpy as np

COLORSPACE = {"sRGB": "sRGB", "Non-Color": "Non-Color", "Linear": "Non-Color"}
BL_FORMAT = {"PNG": "PNG", "TARGA": "TARGA"}


def new_image(name, width, height, colorspace, fill, tracker, alpha=True, float_buffer=False):
    img = bpy.data.images.new(name, width, height, alpha=alpha, float_buffer=float_buffer)
    tracker.add("images", img)
    img.colorspace_settings.name = COLORSPACE[colorspace]
    write(img, np.broadcast_to(np.asarray(fill, np.float32), (height, width, 4)))
    return img


def read(img):
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    return a.reshape(h, w, 4)


def write(img, arr):
    img.pixels.foreach_set(np.ascontiguousarray(arr, dtype=np.float32).ravel())
    img.update()


def save(img, path, file_format):
    """Save without view transform (image.save, spec 13-7)."""
    img.filepath_raw = path
    img.file_format = BL_FORMAT[file_format]
    img.save()


def load(path, colorspace):
    img = bpy.data.images.load(path, check_existing=False)
    img.colorspace_settings.name = COLORSPACE[colorspace]
    return img


def safe_remove(img):
    try:
        if img is not None and img.name in bpy.data.images:
            bpy.data.images.remove(img)
    except ReferenceError:
        pass


def file_exists(path):
    return os.path.isfile(path)
