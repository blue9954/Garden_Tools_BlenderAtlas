"""UV analysis: islands, tiling, overlap, texel density, usage (no bpy).

Inputs are flat numpy arrays as produced by ``foreach_get``:
  face_start, face_total : per polygon loop_start / loop_total
  loop_vert              : per loop vertex index
  loop_uv                : (L, 2) per loop UV
  tris                   : (T, 3) loop indices of triangles, tri_face : (T,) polygon index
"""

import math

import numpy as np

UV_EPS = 1e-5


# ---------------------------------------------------------------- components
def connected_components(n, a, b):
    """Label n nodes connected by edges (a[i], b[i]). Returns labels 0..k-1."""
    labels = np.arange(n, dtype=np.int64)
    a = np.asarray(a, dtype=np.int64)
    b = np.asarray(b, dtype=np.int64)
    if len(a):
        while True:
            la, lb = labels[a], labels[b]
            m = np.minimum(la, lb)
            before = labels.copy()
            np.minimum.at(labels, la, m)
            np.minimum.at(labels, lb, m)
            while True:  # pointer jumping
                nxt = labels[labels]
                if np.array_equal(nxt, labels):
                    break
                labels = nxt
            if np.array_equal(before, labels):
                break
    _, compact = np.unique(labels, return_inverse=True)
    return compact.astype(np.int64)


def face_islands(face_start, face_total, loop_vert, loop_uv, eps=UV_EPS):
    """Per-face UV island label. Faces are connected when they share an edge
    whose two UV corners match."""
    face_start = np.asarray(face_start, dtype=np.int64)
    face_total = np.asarray(face_total, dtype=np.int64)
    nf = len(face_start)
    nl = int(face_total.sum())
    if nf == 0:
        return np.zeros(0, dtype=np.int64)
    loop_face = np.repeat(np.arange(nf), face_total)
    idx_in_face = np.arange(nl) - np.repeat(face_start, face_total)
    nxt = np.repeat(face_start, face_total) + (idx_in_face + 1) % np.repeat(face_total, face_total)
    q = np.rint(np.asarray(loop_uv, dtype=np.float64) / eps).astype(np.int64)
    v0, v1 = np.asarray(loop_vert, dtype=np.int64), np.asarray(loop_vert, dtype=np.int64)[nxt]
    uv0, uv1 = q, q[nxt]
    swap = v0 > v1
    va = np.where(swap, v1, v0)
    vb = np.where(swap, v0, v1)
    ua = np.where(swap[:, None], uv1, uv0)
    ub = np.where(swap[:, None], uv0, uv1)
    key = np.column_stack([va, vb, ua, ub])
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    order = np.argsort(inv, kind="stable")
    s_inv = inv[order]
    same = s_inv[1:] == s_inv[:-1]
    fa = loop_face[order[:-1][same]]
    fb = loop_face[order[1:][same]]
    return connected_components(nf, fa, fb)


# ---------------------------------------------------------------- geometry
def tri_area_2d(p):
    """p: (T, 3, 2) -> (T,) unsigned area."""
    d1 = p[:, 1] - p[:, 0]
    d2 = p[:, 2] - p[:, 0]
    return 0.5 * np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])


def tri_area_3d(p):
    """p: (T, 3, 3) -> (T,) area."""
    c = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    return 0.5 * np.linalg.norm(c, axis=1)


def tiling_repeats(uv):
    """How many times the UV range spans the 0..1 tile (1 = no tiling)."""
    uv = np.asarray(uv, dtype=np.float64)
    if len(uv) == 0:
        return 1
    lo = uv.min(axis=0)
    hi = uv.max(axis=0)
    spans = []
    for k in range(2):
        a = math.floor(lo[k] + UV_EPS)
        b = math.ceil(hi[k] - UV_EPS)
        spans.append(max(1, b - a))
    return int(max(spans))


def texel_density(area3d, area_uv, width, height):
    """px per metre, per element. area3d <= 0 -> nan."""
    area3d = np.asarray(area3d, dtype=np.float64)
    area_uv = np.asarray(area_uv, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.sqrt(area_uv * width * height / area3d)
    d[area3d <= 0] = np.nan
    return d


def density_stats(area3d, area_uv, width, height):
    """Area-weighted mean plus min / max over elements with real area."""
    d = texel_density(area3d, area_uv, width, height)
    ok = np.isfinite(d) & (np.asarray(area3d) > 1e-12)
    if not ok.any():
        return {"min": 0.0, "avg": 0.0, "max": 0.0}
    w = np.asarray(area3d)[ok]
    return {
        "min": float(np.min(d[ok])),
        "avg": float(np.sum(d[ok] * w) / np.sum(w)),
        "max": float(np.max(d[ok])),
    }


# ---------------------------------------------------------------- raster
def rasterize(tri_uv, width, height, origin=(0.0, 0.0), size=(1.0, 1.0)):
    """Boolean coverage of triangle set; pixel centres inside a triangle.

    tri_uv: (T, 3, 2). The raster spans [origin, origin+size) in UV.
    Returns (height, width) bool, row 0 = v of origin (bottom).
    """
    mask = np.zeros((height, width), dtype=bool)
    if len(tri_uv) == 0:
        return mask
    p = (np.asarray(tri_uv, dtype=np.float64) - np.asarray(origin)) / np.asarray(size)
    p = p * np.array([width, height])
    lo = np.floor(p.min(axis=1) - 0.5).astype(np.int64)
    hi = np.ceil(p.max(axis=1) - 0.5).astype(np.int64)
    lo = np.maximum(lo, 0)
    hi[:, 0] = np.minimum(hi[:, 0], width - 1)
    hi[:, 1] = np.minimum(hi[:, 1], height - 1)
    for t in range(len(p)):
        x0, y0 = lo[t]
        x1, y1 = hi[t]
        if x1 < x0 or y1 < y0:
            continue
        xs = np.arange(x0, x1 + 1) + 0.5
        ys = np.arange(y0, y1 + 1) + 0.5
        gx, gy = np.meshgrid(xs, ys)
        a, b, c = p[t]
        den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(den) < 1e-12:
            continue
        w0 = ((b[1] - c[1]) * (gx - c[0]) + (c[0] - b[0]) * (gy - c[1])) / den
        w1 = ((c[1] - a[1]) * (gx - c[0]) + (a[0] - c[0]) * (gy - c[1])) / den
        w2 = 1.0 - w0 - w1
        e = -1e-9
        inside = (w0 >= e) & (w1 >= e) & (w2 >= e)
        mask[y0:y1 + 1, x0:x1 + 1] |= inside
    return mask


def uv_usage(tri_uv, res=512):
    """Fraction of the 0..1 square covered by triangles."""
    return float(rasterize(tri_uv, res, res).mean())


def overlap_fraction(tri_uv, tri_island, max_cells=256 * 256):
    """Fraction of covered area where two or more different islands overlap.

    Works in the bounding box of the UVs (handles tiled UVs). Returns 0..1.
    """
    tri_uv = np.asarray(tri_uv, dtype=np.float64)
    tri_island = np.asarray(tri_island)
    if len(tri_uv) == 0:
        return 0.0
    lo = tri_uv.reshape(-1, 2).min(axis=0)
    hi = tri_uv.reshape(-1, 2).max(axis=0)
    size = np.maximum(hi - lo, 1e-6)
    aspect = size[0] / size[1]
    h = max(8, int(math.sqrt(max_cells / aspect)))
    w = max(8, int(max_cells / h))
    count = np.zeros((h, w), dtype=np.int32)
    for isl in np.unique(tri_island):
        count += rasterize(tri_uv[tri_island == isl], w, h, lo, size)
    covered = (count > 0).sum()
    if covered == 0:
        return 0.0
    return float((count > 1).sum() / covered)


# ---------------------------------------------------------------- distance
def _point_seg_dist(pts, s0, s1):
    """Min distance from each point (P,2) to each segment (S,2)-(S,2). -> (P,S)."""
    d = s1 - s0
    dd = np.maximum((d * d).sum(axis=1), 1e-30)
    rel = pts[:, None, :] - s0[None, :, :]
    t = np.clip((rel * d[None]).sum(axis=2) / dd[None], 0.0, 1.0)
    proj = s0[None] + t[..., None] * d[None]
    return np.linalg.norm(pts[:, None, :] - proj, axis=2)


def min_island_distance(tri_uv, tri_island):
    """Smallest distance (UV units) between triangles of different islands.

    Returns (distance, (island_a, island_b)). Overlap gives 0.
    """
    tri_uv = np.asarray(tri_uv, dtype=np.float64)
    tri_island = np.asarray(tri_island)
    ids = np.unique(tri_island)
    groups = {}
    for i in ids:
        t = tri_uv[tri_island == i]
        segs0 = t.reshape(-1, 2)
        segs1 = t[:, [1, 2, 0], :].reshape(-1, 2)
        groups[int(i)] = (t, segs0, segs1, segs0.min(axis=0), segs0.max(axis=0))
    best = (math.inf, None)
    keys = list(groups)
    for ai, a in enumerate(keys):
        ta, a0, a1, alo, ahi = groups[a]
        for b in keys[ai + 1:]:
            tb, b0, b1, blo, bhi = groups[b]
            gap = np.maximum(np.maximum(alo - bhi, blo - ahi), 0.0)
            if np.linalg.norm(gap) >= best[0]:
                continue
            # bounding boxes overlap -> check real overlap via a coarse test
            d = min(_point_seg_dist(a0, b0, b1).min(), _point_seg_dist(b0, a0, a1).min())
            if d < best[0]:
                best = (float(d), (a, b))
    return best


# ---------------------------------------------------------------- capacity (M3)
def atlas_capacity(source_pixels, width, height, packing_usage=0.7):
    """Does one atlas have room for the source texel budget?

    source_pixels: texels the sources actually use (sum over materials of
    image_res^2 x source UV area of that material's faces, tiling included).
    Returns dict(ratio, sheets, suggested_res) where ratio > 1 means the atlas
    must downsample the sources by sqrt(ratio) in each axis.
    """
    capacity = float(width) * float(height) * packing_usage
    ratio = float(source_pixels) / capacity if capacity > 0 else math.inf
    sheets = max(1, math.ceil(ratio - 1e-9))
    side = math.sqrt(max(source_pixels, 1.0) / packing_usage)
    suggested = 512
    while suggested < side and suggested < 8192:
        suggested *= 2
    return {"ratio": ratio, "sheets": sheets, "suggested_res": suggested}
