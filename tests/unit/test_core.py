"""L1 unit tests for garden_atlas_tool.core (no bpy). Runs under pytest or unittest."""

import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "garden_atlas_tool"))

from core import naming, packing, plan, presets, report, settings, uvmath, version  # noqa: E402


class TestNaming(unittest.TestCase):
    def test_valid(self):
        self.assertIsNone(naming.validate_name("Props_A1"))

    def test_invalid(self):
        for bad in ("", "Props A", "소품", "a-b", "x.y"):
            self.assertIsNotNone(naming.validate_name(bad), bad)

    def test_names(self):
        self.assertEqual(naming.texture_file("Props_A", "BC"), "T_Props_A_BC.png")
        self.assertEqual(naming.texture_file("P", "N", "TARGA"), "T_P_N.tga")
        self.assertEqual(naming.material_name("P"), "M_P")
        self.assertEqual(naming.mesh_name("P"), "SM_P")
        self.assertEqual(naming.collection_name("P"), "ATLAS_P")
        self.assertEqual(naming.manifest_file("P"), "P_atlas.json")


class TestSettings(unittest.TestCase):
    def test_spacing_table(self):
        self.assertEqual(settings.default_spacing(512, 512), (4, 8))
        self.assertEqual(settings.default_spacing(1024, 512), (4, 8))
        self.assertEqual(settings.default_spacing(2048, 2048), (8, 16))
        self.assertEqual(settings.default_spacing(4096, 1024), (16, 32))
        self.assertEqual(settings.default_spacing(8192, 8192), (32, 64))

    def test_gap_warning(self):
        s = settings.AtlasSettings(island_gap_px=10, bake_margin_px=8)
        rep, _ = settings.validate(s, check_output=False)
        self.assertFalse(rep.has_errors)
        self.assertEqual(rep.count(report.WARNING), 1)

    def test_bad_name_is_error(self):
        rep, _ = settings.validate(settings.AtlasSettings(atlas_name="a b"), check_output=False)
        self.assertTrue(rep.has_errors)

    def test_relative_unsaved_is_error(self):
        rep, out = settings.validate(settings.AtlasSettings(output_dir="//tex/"), blend_filepath="")
        self.assertTrue(rep.has_errors)
        self.assertIsNone(out)

    def test_relative_resolves_and_creates(self):
        with tempfile.TemporaryDirectory() as d:
            rep, out = settings.validate(settings.AtlasSettings(output_dir="//tex/"),
                                         blend_filepath=os.path.join(d, "a.blend"))
            self.assertFalse(rep.has_errors, rep.messages)
            self.assertEqual(out, os.path.join(d, "tex"))
            self.assertTrue(os.path.isdir(out))

    def test_from_object(self):
        class P:
            atlas_name = "X"
            width = "1024"
            height = "512"
            junk = 1
        s = settings.AtlasSettings.from_object(P)
        self.assertEqual((s.atlas_name, s.width, s.height), ("X", 1024, 512))


class TestPresetJSON(unittest.TestCase):
    def test_roundtrip_excludes_name(self):
        s = settings.AtlasSettings(atlas_name="Props", width=2048, samples=16, normal_16bit=True)
        d = settings.to_preset(s)
        self.assertNotIn("atlas_name", d["settings"])
        vals, warn = settings.from_preset(d)
        self.assertEqual(warn, [])
        self.assertEqual((vals["width"], vals["samples"], vals["normal_16bit"]), (2048, 16, True))

    def test_bad_values_skipped(self):
        d = {"kind": settings.PRESET_KIND, "schema": 1, "settings": {
            "width": 3000, "samples": "8", "use_normal": 1, "bogus": 1, "density_mode": "SOURCE",
            "atlas_name": "x", "preset": "UNITY"}}
        vals, warn = settings.from_preset(d)
        self.assertEqual(vals, {"density_mode": "SOURCE"})
        self.assertEqual(len(warn), 5)

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            settings.from_preset({"kind": "other"})
        with self.assertRaises(ValueError):
            settings.from_preset({"kind": settings.PRESET_KIND, "schema": 99, "settings": {}})


class TestPlan(unittest.TestCase):
    def test_normal_16bit_flag(self):
        p = plan.make_plan(settings.AtlasSettings(normal_16bit=True))
        self.assertTrue(next(x for x in p if x.channel == "Normal").float_buffer)
        self.assertFalse(next(x for x in p if x.channel == "BaseColor").float_buffer)
        tex = {t["suffix"]: t for t in presets.get_preset("UNREAL")["textures"]}
        self.assertTrue(plan.texture_is_float(tex["N"], p))
        self.assertFalse(plan.texture_is_float(tex["ORM"], p))

    def test_default_plan(self):
        p = plan.make_plan(settings.AtlasSettings())
        self.assertEqual([x.channel for x in p], ["BaseColor", "Roughness", "Metallic", "Normal", "AO"])
        ao = p[-1]
        self.assertTrue(ao.is_fill_only)
        self.assertEqual(ao.fill[:3], (1.0, 1.0, 1.0))

    def test_optional_channels(self):
        s = settings.AtlasSettings(use_ao_bake=True, use_emissive=True)
        p = plan.make_plan(s, has_alpha=True, has_emission=True)
        chans = [x.channel for x in p]
        self.assertIn("Opacity", chans)
        self.assertIn("Emissive", chans)
        ao = next(x for x in p if x.channel == "AO")
        self.assertEqual((ao.bake_type, ao.samples), ("AO", 64))
        bc = p[0]
        self.assertEqual((bc.bake_type, bc.proxy_socket, bc.complex_mode), ("EMIT", "Base Color", plan.COMPLEX_DIFFUSE))

    def test_emissive_only_with_source(self):
        p = plan.make_plan(settings.AtlasSettings(use_emissive=True), has_emission=False)
        self.assertNotIn("Emissive", [x.channel for x in p])
        tex = plan.output_textures(presets.get_preset("UNREAL"), p)
        self.assertEqual([t["suffix"] for t in tex], ["BC", "N", "ORM"])

    def test_disabled_channel_is_fill(self):
        p = plan.make_plan(settings.AtlasSettings(use_roughness=False))
        r = next(x for x in p if x.channel == "Roughness")
        self.assertTrue(r.is_fill_only)
        self.assertEqual(r.fill[0], 0.5)


class TestPacking(unittest.TestCase):
    def test_orm(self):
        spec = next(t for t in presets.get_preset("UNREAL")["textures"] if t["suffix"] == "ORM")
        ch = {"AO": packing.filled(4, 2, (0.1, 0, 0, 1)),
              "Roughness": packing.filled(4, 2, (0.2, 0.2, 0.2, 1)),
              "Metallic": packing.filled(4, 2, (0.3, 0, 0, 1))}
        out = packing.pack_texture(spec, ch, with_alpha=False)
        self.assertEqual(out.shape, (2, 4, 4))
        np.testing.assert_allclose(out[0, 0], [0.1, 0.2, 0.3, 1.0], atol=1e-6)

    def test_bc_alpha(self):
        spec = presets.get_preset("UNREAL")["textures"][0]
        ch = {"BaseColor": packing.filled(2, 2, (1, 0.5, 0, 1)),
              "Opacity": packing.filled(2, 2, (0.25, 0.25, 0.25, 1))}
        out = packing.pack_texture(spec, ch, with_alpha=True)
        np.testing.assert_allclose(out[1, 1], [1, 0.5, 0, 0.25])

    def test_dilate(self):
        img = np.zeros((5, 5, 4), np.float32)
        mask = np.zeros((5, 5), bool)
        img[2, 2] = 1
        mask[2, 2] = True
        out, m = packing.dilate(img, mask, 1)
        self.assertEqual(m.sum(), 5)
        self.assertEqual(out[1, 2, 0], 1)
        self.assertEqual(out[1, 1, 0], 0)
        out, m = packing.dilate(img, mask, 10)
        self.assertTrue(m.all())

    def test_composite(self):
        base = packing.filled(2, 1, (0, 0, 0, 1))
        top = packing.filled(2, 1, (1, 1, 1, 1))
        mask = np.array([[True, False]])
        out = packing.composite(base, [(top, mask)])
        self.assertEqual(out[0, 0, 0], 1)
        self.assertEqual(out[0, 1, 0], 0)

    def test_srgb_roundtrip(self):
        x = np.linspace(0, 1, 11)
        np.testing.assert_allclose(packing.srgb_to_linear(packing.linear_to_srgb(x)), x, atol=1e-9)
        self.assertEqual(packing.to_uint8(packing.linear_to_srgb(0.5)), 188)


def _quad_grid():
    """Two separate quads (2 islands) + one quad sharing an edge with the first."""
    # verts: q0 0-3, q1 4-7, q2 shares verts 1,2 with q0
    face_start = np.array([0, 4, 8])
    face_total = np.array([4, 4, 4])
    loop_vert = np.array([0, 1, 2, 3, 4, 5, 6, 7, 1, 8, 9, 2])
    loop_uv = np.array([
        [0, 0], [0.2, 0], [0.2, 0.2], [0, 0.2],
        [0.5, 0.5], [0.7, 0.5], [0.7, 0.7], [0.5, 0.7],
        [0.2, 0], [0.4, 0], [0.4, 0.2], [0.2, 0.2],
    ], dtype=float)
    return face_start, face_total, loop_vert, loop_uv


class TestUVMath(unittest.TestCase):
    def test_islands(self):
        lab = uvmath.face_islands(*_quad_grid())
        self.assertEqual(lab[0], lab[2])
        self.assertNotEqual(lab[0], lab[1])
        self.assertEqual(len(set(lab.tolist())), 2)

    def test_islands_split_seam(self):
        fs, ft, lv, uv = _quad_grid()
        uv = uv.copy()
        uv[8:] += [0.0, 0.25]  # same verts, different UVs -> seam
        lab = uvmath.face_islands(fs, ft, lv, uv)
        self.assertEqual(len(set(lab.tolist())), 3)

    def test_components_chain(self):
        n = 1000
        lab = uvmath.connected_components(n, np.arange(n - 1), np.arange(1, n))
        self.assertTrue((lab == 0).all())

    def test_tiling(self):
        self.assertEqual(uvmath.tiling_repeats([[0, 0], [1, 1]]), 1)
        self.assertEqual(uvmath.tiling_repeats([[0, 0], [3, 1]]), 3)
        self.assertEqual(uvmath.tiling_repeats([[0.1, 0.1], [0.9, 0.5]]), 1)

    def test_density(self):
        st = uvmath.density_stats([1.0, 4.0], [0.25, 0.25], 1024, 1024)
        self.assertAlmostEqual(st["max"], 512.0)
        self.assertAlmostEqual(st["min"], 256.0)

    def test_raster_and_usage(self):
        tri = np.array([[[0, 0], [1, 0], [1, 1]], [[0, 0], [1, 1], [0, 1]]], float)
        self.assertAlmostEqual(uvmath.uv_usage(tri, 64), 1.0)
        self.assertAlmostEqual(uvmath.uv_usage(tri[:1], 64), 0.5, delta=0.02)

    def test_overlap(self):
        a = np.array([[[0, 0], [1, 0], [0, 1]]], float)
        b = a + 0.0
        self.assertGreater(uvmath.overlap_fraction(np.concatenate([a, b]), [0, 1]), 0.9)
        c = a * 0.4 + 0.6
        self.assertEqual(uvmath.overlap_fraction(np.concatenate([a * 0.4, c]), [0, 1]), 0.0)

    def test_capacity(self):
        c = uvmath.atlas_capacity(2048 * 2048, 4096, 4096)
        self.assertLess(c["ratio"], 1)
        self.assertEqual(c["sheets"], 1)
        c = uvmath.atlas_capacity(4 * 4096 * 4096, 4096, 4096)
        self.assertEqual(c["sheets"], 6)
        self.assertEqual(c["suggested_res"], 8192)

    def test_min_distance(self):
        a = np.array([[[0, 0], [0.2, 0], [0, 0.2]]], float)
        b = a + [0.5, 0]
        d, pair = uvmath.min_island_distance(np.concatenate([a, b]), [0, 1])
        self.assertAlmostEqual(d, 0.3)
        self.assertEqual(pair, (0, 1))


class TestVersion(unittest.TestCase):
    def test_matches_manifest_and_changelog(self):
        v = version.tool_version()
        self.assertRegex(v, r"^\d+\.\d+\.\d+$")
        changelog = open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
        self.assertIn(f"## [{v}]", changelog)


class TestReport(unittest.TestCase):
    def test_counts(self):
        r = report.Report()
        r.error("e")
        r.warning("w")
        r.info("i")
        self.assertTrue(r.has_errors)
        self.assertEqual(r.count(report.WARNING), 1)

    def test_manifest(self):
        pre = presets.get_preset("UNREAL")
        tex = {t["suffix"]: report.texture_entry(t, naming.texture_file("P", t["suffix"]), False,
                                                 pre["normal_convention"]) for t in pre["textures"][:3]}
        m = report.build_manifest(name="P", blender_version="5.1.2", preset_key="UNREAL",
                                  resolution=(4096, 4096), textures=tex, sources=[], stats={},
                                  warnings=["w"])
        self.assertEqual(m["schema"], 1)
        self.assertEqual(m["textures"]["ORM"]["channels"], {"R": "AO", "G": "Roughness", "B": "Metallic"})
        self.assertEqual(m["textures"]["N"]["convention"], "DirectX")
        self.assertEqual(m["textures"]["BC"]["channels"], "RGB")


if __name__ == "__main__":
    unittest.main()
