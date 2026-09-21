import csv
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

import batch_core as core
from test_batch import synthetic_video


class FrameOutputTests(unittest.TestCase):
    def test_modes_keep_original_resolution_and_plain_original_pixels(self):
        frame = np.zeros((300, 400, 3), np.uint8)
        frame[80:140, 140:220] = (20, 80, 180)
        settings = dict(lower=[0, 0, 0], upper=[179, 255, 255], min_area=10, roi=None)
        original = core.compose_frame(frame, settings, "Original")
        np.testing.assert_array_equal(original, frame)
        for mode in ("Original", "Mask", "Overlay"):
            image = core.compose_frame(frame, settings, mode,
                                       boxes=[(140, 80, 80, 60, 4800)],
                                       selected_indices=[0], include_bbox=True)
            self.assertEqual(image.shape, frame.shape)
        self.assertEqual(core.frame_filename("scene.mp4", 123, "Mask", False),
                         "scene_frame_000123_mask_plain.png")
        self.assertEqual(core.frame_filename("scene.mp4", 123, "Overlay", True),
                         "scene_frame_000123_overlay_annotated.png")


class OfflineIndexTests(unittest.TestCase):
    def test_export_writes_self_contained_index_with_relative_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "demo.avi"
            synthetic_video(video, [1, 1, 1, 1, 0, 1, 1, 0, 0, 0, 0])
            item = core.new_item(video, scenario_id="特殊 <scenario>", phase="P1", camera="Cam A")
            item["pairing_run"] = {"sequence": 42}
            run = core.analyze_item(item, root / "runs")
            item.update(latest_run=run, status="completed")
            output, _summaries = core.export_batch([item], root)
            index = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("index.html", (output / "index.html").name)
            self.assertIn("42", index)
            self.assertIn("\\u003cscenario\\u003e", index)
            self.assertNotIn("fetch(", index)
            self.assertIn("frame_analysis.csv", index)
            self.assertTrue((output / "event_images_manifest.csv").is_file())
            self.assertFalse("http://" in index or "https://" in index)


if __name__ == "__main__":
    unittest.main()
