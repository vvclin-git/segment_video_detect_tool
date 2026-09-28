from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from delivery_tool.collage import (load_comparison_layers, render_collage,
                                   render_comparison, render_comparison_paths)
from delivery_tool.config import (DEFAULT_COMPARISON_OVERLAY, comparison_overlay_settings,
                                  load_config, save_config)


class ComparisonOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = np.full((8, 8, 3), 100, dtype=np.uint8)
        self.image_path = self.root / "raw.png"
        Image.fromarray(self.base).save(self.image_path)
        self.gt_path = self.root / "labels.json"
        self.mask_path = self.root / "mask.png"
        self.set_shapes([{"label": "ship", "shape_type": "polygon",
                          "points": [[1.9, 1.9], [4.9, 1.9], [4.9, 4.9], [1.9, 4.9]]}])
        mask = np.zeros((8, 8), dtype=np.uint8)
        mask[2, 2] = 128
        mask[3, 3] = 127
        mask[3, 5] = 255
        Image.fromarray(mask).save(self.mask_path)

    def tearDown(self):
        self.temp.cleanup()

    def set_shapes(self, shapes):
        self.gt_path.write_text(json.dumps({"imagePath": "raw.png", "imageWidth": 8,
                                           "imageHeight": 8, "shapes": shapes}), encoding="utf-8")

    def test_old_partial_and_complete_settings_merge_and_round_trip(self):
        old = self.root / "old.json"
        old.write_text('{"schema_version":1,"report":{"report_name":"legacy"}}', encoding="utf-8")
        loaded = load_config(old)
        self.assertEqual(loaded["report"]["comparison_overlay"], comparison_overlay_settings())

        partial = comparison_overlay_settings({"comparison_overlay": {
            "opacity": 0.3, "colors": {"overlap": "#aabbcc"}}})
        self.assertEqual(partial, {"opacity": 0.3, "colors": {
            "overlap": "#AABBCC", "gt_only": "#FF282D", "prediction_only": "#14D2FF"}})
        complete = {"schema_version": 1, "report": {"comparison_overlay": {
            "opacity": 1, "colors": {"overlap": "#abcdef", "gt_only": "#000000",
                                      "prediction_only": "#FFFFFF"}}}}
        target = self.root / "roundtrip.json"
        save_config(target, complete)
        saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(saved["report"]["comparison_overlay"]["colors"]["overlap"], "#ABCDEF")
        self.assertEqual(load_config(target)["report"]["comparison_overlay"], {
            "opacity": 1.0, "colors": {"overlap": "#ABCDEF", "gt_only": "#000000",
                                        "prediction_only": "#FFFFFF"}})

    def test_invalid_hex_opacity_and_types_have_field_errors(self):
        invalid = [
            ({"colors": {"gt_only": "red"}}, "colors.gt_only"),
            ({"opacity": -0.01}, "opacity"),
            ({"opacity": 1.01}, "opacity"),
            ({"opacity": float("nan")}, "opacity"),
            ({"opacity": float("inf")}, "opacity"),
            ({"opacity": "65%"}, "opacity"),
            ({"opacity": True}, "opacity"),
            ({"colors": ["#FFFFFF"]}, "colors"),
        ]
        with self.assertRaisesRegex(ValueError, "report.comparison_overlay"):
            comparison_overlay_settings([])
        with self.assertRaisesRegex(ValueError, "opacity"):
            comparison_overlay_settings({"opacity": 10 ** 1000})
        for value, field in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, field):
                comparison_overlay_settings({"comparison_overlay": value})

    def test_three_regions_thresholds_background_and_reference_blend(self):
        base, regions = load_comparison_layers(self.image_path, self.gt_path, self.mask_path)
        self.assertFalse(np.any(regions["overlap"] & regions["gt_only"]))
        self.assertTrue(regions["overlap"][2, 2])  # 128 is foreground.
        self.assertTrue(regions["gt_only"][3, 3])  # 127 is background.
        self.assertTrue(regions["prediction_only"][3, 5])
        self.assertFalse(regions["gt_only"][0, 0])
        result = np.asarray(render_comparison(base, regions))
        self.assertTupleEqual(tuple(result[2, 2]), (54, 187, 93))
        self.assertTupleEqual(tuple(result[3, 3]), (200, 61, 64))
        self.assertTupleEqual(tuple(result[3, 5]), (48, 171, 200))
        self.assertTupleEqual(tuple(result[0, 0]), (100, 100, 100))
        # int(x), int(y) truncates the 1.9 polygon edge to pixel coordinate 1.
        self.assertTrue(regions["gt_only"][2, 1])

    def test_zero_and_full_opacity_and_mutually_exclusive_classes(self):
        base, regions = load_comparison_layers(self.image_path, self.gt_path, self.mask_path)
        self.assertTrue(np.array_equal(np.asarray(render_comparison(base, regions, {"opacity": 0})), base))
        full = np.asarray(render_comparison(base, regions, {"opacity": 1}))
        self.assertTupleEqual(tuple(full[2, 2]), (30, 235, 90))
        self.assertTupleEqual(tuple(full[3, 3]), (255, 40, 45))
        self.assertTupleEqual(tuple(full[3, 5]), (20, 210, 255))
        self.assertEqual(sum(int(region.sum()) for region in regions.values()),
                         int(np.logical_or.reduce(tuple(regions.values())).sum()))

    def test_empty_masks_multiple_polygons_missing_and_size_mismatch(self):
        self.set_shapes([])
        Image.new("L", (8, 8), 0).save(self.mask_path)
        base, regions = load_comparison_layers(self.image_path, self.gt_path, self.mask_path)
        self.assertFalse(any(region.any() for region in regions.values()))

        mask = np.zeros((8, 8), dtype=np.uint8); mask[0, 0] = 255
        Image.fromarray(mask).save(self.mask_path)
        regions = load_comparison_layers(self.image_path, self.gt_path, self.mask_path)[1]
        self.assertTrue(regions["prediction_only"][0, 0])
        self.assertFalse(regions["gt_only"].any())

        self.set_shapes([
            {"label": "ship", "shape_type": "polygon", "points": [[1, 1], [2, 1], [2, 2]]},
            {"label": "buoy", "shape_type": "polygon", "points": [[5, 5], [6, 5], [6, 6]]},
        ])
        Image.new("L", (8, 8), 0).save(self.mask_path)
        regions = load_comparison_layers(self.image_path, self.gt_path, self.mask_path)[1]
        self.assertTrue(regions["gt_only"][1, 1])
        self.assertTrue(regions["gt_only"][5, 5])

        Image.new("L", (7, 8), 0).save(self.mask_path)
        with self.assertRaisesRegex(ValueError, "Mask 座標尺寸"):
            load_comparison_layers(self.image_path, self.gt_path, self.mask_path)
        with self.assertRaises(FileNotFoundError):
            load_comparison_layers(self.image_path, self.gt_path, self.root / "missing.png")

    def test_preview_and_official_png_share_identical_pixels_and_iou_is_unchanged(self):
        settings = {"opacity": 0.3, "colors": {"overlap": "#123456"}}
        base, regions = load_comparison_layers(self.image_path, self.gt_path, self.mask_path)
        preview = np.asarray(render_comparison(base, regions, settings))
        iou = regions["overlap"].sum() / max(1, sum(region.sum() for region in regions.values()))
        settings["opacity"] = 1
        render_comparison(base, regions, settings)
        self.assertEqual(iou, regions["overlap"].sum() / max(1, sum(region.sum() for region in regions.values())))

        settings["opacity"] = 0.3
        record = {"image_path": str(self.image_path), "attachment": {
            "gt_status": "valid", "mask_status": "valid", "labelme_path": str(self.gt_path),
            "mask_path": str(self.mask_path), "seg_status": "missing"}}
        collage_path = self.root / "collage.png"
        overlay_path = self.root / "official.png"
        render_collage(record, collage_path, overlay_output=overlay_path, comparison_overlay=settings)
        with Image.open(overlay_path) as official:
            self.assertTrue(np.array_equal(np.asarray(official), preview))
        self.assertTrue(np.array_equal(np.asarray(render_comparison_paths(
            self.image_path, self.gt_path, self.mask_path, settings)), preview))


if __name__ == "__main__":
    unittest.main()
