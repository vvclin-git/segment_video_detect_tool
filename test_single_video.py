"""Regression coverage for the original standalone --single desktop tool."""
import csv
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from app import FrameResult, HSVVideoTester


class SingleVideoRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.video = self.directory / "single_video.avi"
        self.detections = [0, 0, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1]
        writer = cv2.VideoWriter(str(self.video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (96, 64))
        self.assertTrue(writer.isOpened())
        color = cv2.cvtColor(np.uint8([[[170, 240, 240]]]), cv2.COLOR_HSV2BGR)[0, 0]
        for detected in self.detections:
            frame = np.zeros((64, 96, 3), np.uint8)
            if detected:
                frame[20:40, 30:50] = color
            writer.write(frame)
        writer.release()
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = HSVVideoTester(self.root)
        self.root.update()
        with patch("app.filedialog.askopenfilename", return_value=str(self.video)):
            self.app.open_video()
        self.app.window_size.set(3)
        self.app.stable_on.set(2)
        self.app.stable_off.set(0)
        self.app.stable_confirm_frames.set(2)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_raw_rolling_stable_events_and_start_frame(self):
        self.app.analyze_video()
        rows = self.app.last_results
        self.assertEqual([row.raw_detected for row in rows], self.detections)
        self.assertEqual([row.stable_detected for row in rows], [0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 1])
        self.assertAlmostEqual(rows[3].rolling_rate, 2 / 3)
        summary = self.app._summary()
        self.assertEqual(summary["first_detection_frame"], 3)
        self.assertEqual(summary["first_sustained_stable_start_frame"], 4)
        self.assertEqual(summary["first_stable_confirmation_frame"], 5)
        self.assertEqual(summary["first_stable_detection_frame"], 5)
        self.assertEqual(summary["stabilization_latency_frames"], 2)
        self.assertEqual(summary["longest_consecutive_miss"], 3)
        self.assertGreater(summary["first_detection_bbox_width"], 0)
        self.app.analysis_start_frame.set(4)
        self.app.analyze_video()
        self.assertEqual([row.frame for row in self.app.last_results], list(range(4, 13)))
        self.assertEqual(self.app.last_results[0].timestamp, .3)
        self.assertEqual(self.app.last_results[0].stable_detected, 0)
        self.assertEqual(self.app._summary()["first_sustained_stable_start_frame"], 6)
        self.assertEqual(self.app._summary()["first_stable_confirmation_frame"], 7)
        self.assertFalse(self.errors, self.errors)

    def test_preview_navigation_histogram_roi_and_no_detection(self):
        self.app.target_frame.set(5)
        self.app.go_to_frame()
        self.assertEqual(self.app.frame_index, 4)
        self.app.step(-1)
        self.assertEqual(self.app.frame_index, 3)
        self.app.seek("2")
        self.assertEqual(self.app.frame_index, 2)
        for mode in ("Original", "Mask", "Overlay"):
            self.app.view_mode.set(mode)
            self.app.render()
            self.assertIsNotNone(self.app.photo)
            self.assertTrue(self.app.hist_canvas.find_all())
            self.assertTrue(self.app.tree.get_children())
        self.app.toggle_play()
        self.assertTrue(self.app.playing)
        self.assertEqual(self.app.frame_index, 3)
        self.app.toggle_play()
        self.assertFalse(self.app.playing)
        # Define a region away from the colored target using the actual drag handlers.
        self.root.update()
        ox, oy = self.app.display_offset
        scale = self.app.display_scale
        from types import SimpleNamespace
        self.app.roi_press(SimpleNamespace(x=ox, y=oy))
        self.app.roi_release(SimpleNamespace(x=ox + round(10 * scale), y=oy + round(10 * scale)))
        self.app.analyze_video()
        self.assertFalse(any(row.raw_detected for row in self.app.last_results))
        self.assertEqual(self.app._summary()["first_detection_frame"], "")
        self.assertEqual(self.app._summary()["first_sustained_stable_start_frame"], "")
        self.app.clear_roi()
        self.assertIsNone(self.app.roi)
        self.assertEqual(self.app.last_results, [])
        self.assertFalse(self.errors, self.errors)

    def test_csv_schema_and_all_event_raw_mask_exports(self):
        self.app.analyze_video()
        csv_path = self.directory / "single_frame_analysis.csv"
        with patch("app.filedialog.asksaveasfilename", return_value=str(csv_path)), patch("app.messagebox.showinfo"):
            self.app.export_csv()
        with csv_path.open(encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            self.assertEqual(reader.fieldnames, list(FrameResult.__dataclass_fields__))
            rows = list(reader)
        self.assertEqual(len(rows), len(self.detections))
        self.assertEqual(rows[4]["stable_detected"], "1")
        with (self.directory / "single_run_summary.csv").open(encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            self.assertEqual(reader.fieldnames, ["metric", "value"])
            summary = {row["metric"]: row["value"] for row in reader}
        self.assertEqual(summary["first_stable_detection_frame"], "5")
        self.assertEqual(summary["first_sustained_stable_start_frame"], "4")
        with patch("app.filedialog.askdirectory", return_value=str(self.directory)), patch("app.messagebox.showinfo"):
            self.app.export_event_images()
        output = self.directory / "single_video_event_frames"
        with (output / "event_images_manifest.csv").open(encoding="utf-8-sig") as source:
            events = list(csv.DictReader(source))
        self.assertEqual({row["event"]: int(row["frame"]) for row in events},
                         dict(first_detection=3, first_sustained_stable_start=4, first_stable_confirmation=5))
        cap = cv2.VideoCapture(str(self.video))
        try:
            for event in events:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(event["frame"]) - 1)
                ok, expected = cap.read()
                self.assertTrue(ok)
                raw = cv2.imdecode(np.fromfile(output / event["raw_filename"], np.uint8), cv2.IMREAD_COLOR)
                mask = cv2.imdecode(np.fromfile(output / event["mask_filename"], np.uint8), cv2.IMREAD_GRAYSCALE)
                np.testing.assert_array_equal(raw, expected)
                np.testing.assert_array_equal(mask, self.app.create_event_mask(expected, self.app.analysis_config))
        finally:
            cap.release()
        # Changing uncommitted stable controls must not rewrite completed event results.
        self.app.stable_confirm_frames.set(4)
        self.assertEqual(self.app._summary()["first_sustained_stable_start_frame"], 4)
        self.assertFalse(self.errors, self.errors)

    def test_single_entry_does_not_load_batch_modules(self):
        code = '''
import runpy, sys, tkinter as tk
def check(root):
    root.update()
    assert root.title() == "ECU Segmentation Video Validator"
    assert "batch_ui" not in sys.modules
    assert "batch_core" not in sys.modules
    root.destroy()
tk.Tk.mainloop = check
sys.argv = ["app.py", "--single"]
runpy.run_path("app.py", run_name="__main__")
print("single entry isolated")
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("single entry isolated", result.stdout)


if __name__ == "__main__":
    unittest.main()
