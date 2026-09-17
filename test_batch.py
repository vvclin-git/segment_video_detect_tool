import copy
import csv
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import batch_core as core
from app import calculate_stable_states, find_first_sustained_stable_start


def synthetic_video(path, detections, fps=10):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (96, 64))
    if not writer.isOpened():
        raise RuntimeError("MJPG encoder unavailable")
    frames = []
    for detected in detections:
        frame = np.zeros((64, 96, 3), np.uint8)
        if detected:
            color = cv2.cvtColor(np.uint8([[[170, 240, 240]]]), cv2.COLOR_HSV2BGR)[0, 0]
            frame[20:40, 30:50] = color
        writer.write(frame)
        frames.append(frame)
    writer.release()
    return frames


class StabilityContractTests(unittest.TestCase):
    def track(self, detections, **settings):
        config = dict(core.DEFAULTS, **settings)
        tracker = core.StableTracker(config)
        return [tracker.push(d) for d in detections]

    def test_confirmation_and_hysteresis(self):
        rows = self.track([1, 1, 1, 1, 0, 1, 1, 0, 0, 0, 0])
        self.assertEqual([r["stable_detected"] for r in rows], [0]*6 + [1]*4 + [0])
        self.assertEqual(rows[4]["on_qualifying_streak"], 1)
        self.assertEqual(rows[6]["on_qualifying_streak"], 3)
        self.assertEqual(rows[9]["rolling_hit_count"], 2)

    def test_warmup_and_candidate_reset(self):
        rows = self.track([1, 1, 0, 0, 1, 0, 1, 1], window_n=3, stable_on_m=2,
                          stable_off_count=0, stable_confirm_frames=3)
        self.assertEqual(rows[0]["rolling_rate"], 1)
        self.assertEqual(rows[0]["window_ready"], 0)
        self.assertEqual(rows[3]["on_qualifying_streak"], 0)
        self.assertFalse(any(r["stable_detected"] for r in rows))

    def test_randomized_legacy_equivalence(self):
        rng = np.random.default_rng(44)
        for _ in range(80):
            n = int(rng.integers(1, 15))
            on = int(rng.integers(1, n + 1))
            off = int(rng.integers(0, on))
            k = int(rng.integers(1, 5))
            ds = rng.integers(0, 2, 150).tolist()
            actual = self.track(ds, window_n=n, stable_on_m=on, stable_off_count=off, stable_confirm_frames=k)
            rates, states, misses = calculate_stable_states(ds, n, on, off, k)
            self.assertEqual([r["rolling_rate"] for r in actual], rates)
            self.assertEqual([r["stable_detected"] for r in actual], states)
            self.assertEqual([r["consecutive_miss"] for r in actual], misses)
            index = next((i for i, value in enumerate(states) if value), None)
            expected_start = index - k + 1 if index is not None else None
            self.assertEqual(find_first_sustained_stable_start(ds, n, on, off, k), expected_start)

    def test_bounds_and_validation(self):
        self.assertEqual(core.analysis_bounds(core.DEFAULTS, 11), (0, 11))
        config = dict(core.DEFAULTS, start_percent=20, end_percent=70)
        self.assertEqual(core.analysis_bounds(config, 11), (2, 8))
        for settings in (dict(core.DEFAULTS, stable_off_count=4), dict(core.DEFAULTS, end_percent=0),
                         dict(core.DEFAULTS, start_percent=float("nan")), dict(core.DEFAULTS, lower=[180, 0, 0]),
                         dict(core.DEFAULTS, roi=[1, 1, 0, 0])):
            with self.assertRaises(ValueError):
                core.validate_settings(settings)


class BatchWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "測試.avi"
        self.ds = [1, 1, 1, 1, 0, 1, 1, 0, 0, 0, 0]
        synthetic_video(self.path, self.ds)
        self.item = core.new_item(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def analyze(self, item=None):
        item = item or self.item
        run = core.analyze_item(item, self.root / "runs")
        item.update(latest_run=run, status="completed")
        item["runs"].append(run["directory"])
        return run

    def test_real_video_events_and_exports(self):
        run = self.analyze()
        rows = core.load_rows(run)
        self.assertEqual([r["raw_detected"] for r in rows], self.ds)
        self.assertEqual(run["summary"]["first_detection_frame"], 1)
        self.assertEqual(run["summary"]["first_stable_confirmation_frame"], 7)
        self.assertEqual(run["summary"]["first_sustained_stable_start_frame"], 5)
        self.assertEqual(rows[0]["timestamp"], 0)
        self.assertEqual(rows[6]["timestamp"], .6)
        out, summaries = core.export_batch([self.item], self.root)
        self.assertEqual(summaries[0]["export_status"], "completed")
        with (out / "event_images_manifest.csv").open(encoding="utf-8-sig") as source:
            records = list(csv.DictReader(source))
        self.assertEqual(len(records), 3)
        for record in records:
            raw = cv2.imdecode(np.fromfile(out / record["raw_filename"], np.uint8), cv2.IMREAD_COLOR)
            expected = core.read_exact_frame(self.path, int(record["frame"]))
            np.testing.assert_array_equal(raw, expected)
        self.assertTrue(records[0]["annotated_filename"])
        with next(out.rglob("run_summary.csv")).open(encoding="utf-8-sig") as source:
            metrics = {row["metric"]: row["value"] for row in csv.DictReader(source)}
        self.assertEqual(metrics["export_status"], "completed")

    def test_stable_event_without_bbox(self):
        self.item["settings"]["stable_confirm_frames"] = 1
        run = self.analyze()
        self.assertEqual(run["summary"]["first_stable_confirmation_frame"], 5)
        self.assertEqual(run["summary"]["first_stable_confirmation_bbox_valid"], 0)
        self.assertIsNone(run["summary"]["first_stable_confirmation_bbox_x"])
        self.assertEqual(run["summary"]["first_sustained_stable_start_frame"], 5)

    def test_backtracked_start_ignores_unconfirmed_candidate(self):
        synthetic_video(self.path, [1, 1, 0, 0, 0, 1, 1, 1, 1])
        self.item["settings"].update(window_n=3, stable_on_m=2, stable_off_count=0, stable_confirm_frames=2)
        run = self.analyze()
        self.assertEqual(run["summary"]["first_detection_frame"], 1)
        self.assertEqual(run["summary"]["first_sustained_stable_start_frame"], 7)
        self.assertEqual(run["summary"]["first_stable_confirmation_frame"], 8)
        out, _ = core.export_batch([self.item], self.root)
        with (out / "batch_summary.csv").open(encoding="utf-8-sig") as source:
            row = next(csv.DictReader(source))
        self.assertEqual(row["first_sustained_stable_start_frame"], "7")
        self.assertAlmostEqual(float(row["first_sustained_stable_start_timestamp"]), .6)
        self.assertTrue(list(out.rglob("first_sustained_stable_start_frame_000007_raw.png")))

    def test_range_reset_no_detection_and_no_stable(self):
        self.item["settings"].update(start_percent=65, end_percent=100)
        run = self.analyze()
        self.assertEqual(run["analysis_start_frame"], 8)
        self.assertEqual(run["summary"]["detection_outcome"], "no_detection")
        self.assertEqual(core.load_rows(run)[0]["rolling_window_length"], 1)
        out, summaries = core.export_batch([self.item], self.root)
        self.assertEqual(summaries[0]["first_detection_frame"], None)
        self.assertFalse(list(out.rglob("*.png")))
        self.item["settings"].update(start_percent=0, end_percent=30)
        self.assertEqual(self.analyze()["summary"]["detection_outcome"], "not_stable")
        self.assertIsNone(self.item["latest_run"]["summary"]["first_sustained_stable_start_frame"])

    def test_project_roundtrip_and_stale_recompute(self):
        run = self.analyze()
        project = core.new_project()
        project["items"] = [self.item]
        path = self.root / "project.json"
        core.save_project(path, project)
        restored = core.load_project(path)["items"][0]
        self.assertEqual(restored, self.item)
        self.assertEqual(core.stale_reason(restored), "")
        restored["settings"]["stable_confirm_frames"] = 1
        self.assertTrue(core.stale_reason(restored))
        with patch("batch_core.segment_frame", side_effect=AssertionError("Should reuse raw data")):
            new = core.analyze_item(restored, self.root / "runs")
        self.assertEqual(new["recomputed_from"], run["run_id"])
        self.assertEqual(new["summary"]["first_stable_confirmation_frame"], 5)
        self.assertEqual(core.read_json(Path(run["directory"]) / "run.json")["summary"]["first_stable_confirmation_frame"], 7)

    def test_roi_changes_force_decode_and_can_remove_detection(self):
        old = self.analyze()
        self.item["settings"]["roi"] = [0, 0, 10, 10]
        new = self.analyze()
        self.assertNotIn("recomputed_from", new)
        self.assertEqual(new["summary"]["detection_outcome"], "no_detection")
        self.assertNotEqual(old["run_id"], new["run_id"])

    def test_cancel_and_early_decode_failure(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(core.Cancelled):
            core.analyze_item(self.item, self.root / "runs", stop)
        original = core.probe_video(self.path)
        with patch("batch_core.probe_video", return_value=dict(original, frame_count=100)):
            with self.assertRaisesRegex(ValueError, "提早解碼失敗"):
                core.analyze_item(self.item, self.root / "runs")
        self.assertFalse(list((self.root / "runs").glob("*/run.json")))

    def test_missing_source_and_same_names_preserved(self):
        self.analyze()
        other = core.new_item(self.path)
        self.analyze(other)
        missing = core.new_item(self.root / "missing.avi")
        out, summaries = core.export_batch([self.item, other, missing], self.root)
        self.assertEqual(len(summaries), 3)
        self.assertEqual(len(list(out.rglob("frame_analysis.csv"))), 2)
        self.assertEqual(summaries[2]["export_status"], "no_results")
        self.path.unlink()
        self.assertTrue(core.stale_reason(self.item))
        out, summaries = core.export_batch([self.item], self.root)
        self.assertEqual(summaries[0]["export_status"], "partial")
        self.assertTrue(list(out.rglob("frame_analysis.csv")))
        self.assertFalse(list(out.rglob("*.png")))

    def test_pairing_import_and_relocation(self):
        path = self.root / "pairing.json"
        core.atomic_json(path, dict(cameras=[dict(id="cam", name="Camera 1")], runs=[dict(id="run1", sequence=4,
            cameraMatches=dict(cam=dict(path="D:\\old\\測試.avi", duration=1.1,
                                       runStartInVideoSec=.2, runEndInVideoSec=1.0)))]))
        imported = core.import_pairing(path, self.root)
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["path"], str(self.path.resolve()))
        self.assertEqual(imported[0]["camera"], "Camera 1")
        self.assertEqual(imported[0]["session"], "4")
        self.assertAlmostEqual(imported[0]["settings"]["start_percent"], 100 * .2 / 1.1)

    def test_missing_result_files_are_reported_and_recomputed(self):
        run = self.analyze()
        (Path(run["directory"]) / "frames.json").unlink()
        out, summaries = core.export_batch([self.item], self.root)
        self.assertEqual(summaries[0]["export_status"], "failed")
        with (out / "event_images_manifest.csv").open(encoding="utf-8-sig") as source:
            records = list(csv.DictReader(source))
        self.assertEqual(len(records), 3)
        self.assertTrue(all(row["status"] == "failed" for row in records))
        new = core.analyze_item(self.item, self.root / "runs")
        self.assertNotIn("recomputed_from", new)
        self.assertEqual(new["summary"]["first_stable_confirmation_frame"], 7)


if __name__ == "__main__":
    unittest.main()
