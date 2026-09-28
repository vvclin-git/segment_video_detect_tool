from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from delivery_tool.builder import _package_assets
from delivery_tool.config import load_config
from delivery_tool.frame_analysis import build_analysis_sequence, sequence_id, validate_rows


def sample_rows(start=5, count=3):
    return [
        {"frame": frame, "timestamp": (frame - 1) / 10, "raw_detected": int(frame == 6),
         "rolling_rate": 0.0 if frame == 5 else 0.5, "stable_detected": 0,
         "window_ready": int(frame != 5), "selected_pixels": 12,
         "largest_blob_area": 9, "bbox_valid": int(frame == 6),
         "bbox_x": 10 if frame == 6 else None, "bbox_y": 20 if frame == 6 else None,
         "bbox_width": 3 if frame == 6 else 0, "bbox_height": 4 if frame == 6 else 0}
        for frame in range(start, start + count)
    ]


class FrameRowsTest(unittest.TestCase):
    def test_validates_and_keeps_warmup_timestamp_and_optional_metrics(self):
        rows = validate_rows(sample_rows(), 5, 7, 10)
        self.assertEqual([row["frame"] for row in rows], [5, 6, 7])
        self.assertEqual(rows[0]["window_ready"], 0)
        self.assertEqual(rows[1]["timestamp"], 0.5)
        self.assertEqual(rows[1]["largest_blob_area"], 9)
        self.assertEqual(rows[1]["bbox_x"], 10)

    def test_does_not_invent_fps_when_timestamp_is_missing(self):
        row = sample_rows(5, 1)[0]
        self.assertEqual(validate_rows([row], 5, 5, None)[0]["timestamp"], 0.4)
        row.pop("timestamp")
        with self.assertRaisesRegex(ValueError, "timestamp"):
            validate_rows([row], 5, 5, None)

    def test_rejects_incomplete_or_invalid_sequences(self):
        variants = []
        variants.append((sample_rows()[:-1], 5, 7))
        gap = sample_rows(); gap[1]["frame"] = 7; variants.append((gap, 5, 7))
        missing = sample_rows(); del missing[1]["raw_detected"]; variants.append((missing, 5, 7))
        invalid_state = sample_rows(); invalid_state[0]["stable_detected"] = 2; variants.append((invalid_state, 5, 7))
        invalid_rate = sample_rows(); invalid_rate[1]["rolling_rate"] = float("nan"); variants.append((invalid_rate, 5, 7))
        out_of_range = sample_rows(); out_of_range[1]["rolling_rate"] = 1.1; variants.append((out_of_range, 5, 7))
        for rows, start, end in variants:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    validate_rows(rows, start, end, 10)
        with self.assertRaisesRegex(ValueError, "筆數"):
            validate_rows(sample_rows(), 5, 8, 10)

    def test_single_frame_is_valid(self):
        self.assertEqual(len(validate_rows(sample_rows(42, 1), 42, 42, None)), 1)

    def test_missing_timestamp_uses_only_supplied_valid_fps(self):
        row = sample_rows(5, 1)[0]
        row.pop("timestamp")
        self.assertEqual(validate_rows([row], 5, 5, 10)[0]["timestamp"], 0.4)


class FrameSourceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project_path = self.root / "same_name.json"
        self.run_id = "run-current"
        self.item = {"id": "item-1", "camera": "Camera 1", "scenario_id": 1, "phase": "P1",
                     "pairing_run_id": "pair-1", "settings": {}, "manual_events": {},
                     "latest_run": {"run_id": self.run_id, "analysis_start_frame": 5, "analysis_end_frame": 7,
                                    "directory": str(self.root / "legacy" / self.run_id),
                                    "metadata": {"fps": None, "timestamp_basis": "frame timestamps"},
                                    "settings": {"window_n": 5, "stable_on_m": 4, "stable_off_count": 1,
                                                 "stable_confirm_frames": 3},
                                    "summary": {"first_detection_frame": 6}}}
        self.project_row = {"item": self.item, "project_path": str(self.project_path), "project_id": "project-1"}
        self.config = {"include_frame_charts": True, "project_assets_roots": {}, "_config_dir": str(self.root)}
        self.project_path.write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_rows(self, directory, rows=None):
        path = directory / "runs" / self.run_id / "frames.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sample_rows() if rows is None else rows), encoding="utf-8")
        return path

    def test_configured_root_precedes_adjacent_and_latest_run(self):
        configured = self.root / "configured"
        adjacent = self.project_path.with_suffix(".assets")
        self.write_rows(configured)
        adjacent_file = self.write_rows(adjacent)
        self.config["project_assets_roots"] = {"project-1": str(configured)}
        sequence, rows = build_analysis_sequence(self.project_row, self.config)
        self.assertEqual(sequence["status"], "available")
        self.assertEqual(Path(sequence["_source_path"]), configured / "runs" / self.run_id / "frames.json")
        self.assertNotEqual(Path(sequence["_source_path"]), adjacent_file)

    def test_corrupt_higher_priority_file_does_not_fall_back(self):
        configured = self.root / "configured"
        chosen = configured / "runs" / self.run_id / "frames.json"
        chosen.parent.mkdir(parents=True)
        chosen.write_text("{broken", encoding="utf-8")
        self.write_rows(self.project_path.with_suffix(".assets"))
        self.config["project_assets_roots"] = {"project-1": str(configured)}
        sequence, rows = build_analysis_sequence(self.project_row, self.config)
        self.assertEqual(sequence["status"], "invalid")
        self.assertIsNone(rows)
        self.assertIn(str(chosen), sequence["reason"])

    def test_missing_configured_root_falls_through_to_project_adjacent(self):
        configured = self.root / "missing-config-root"
        adjacent = self.project_path.with_suffix(".assets")
        chosen = self.write_rows(adjacent)
        self.config["project_assets_roots"] = {"project-1": str(configured)}
        sequence, rows = build_analysis_sequence(self.project_row, self.config)
        self.assertEqual(sequence["status"], "available")
        self.assertEqual(Path(sequence["_source_path"]), chosen)

    def test_latest_run_directory_is_final_fallback(self):
        latest = self.root / "legacy" / self.run_id
        chosen = latest / "frames.json"
        latest.mkdir(parents=True)
        chosen.write_text(json.dumps(sample_rows()), encoding="utf-8")
        sequence, _ = build_analysis_sequence(self.project_row, self.config)
        self.assertEqual(sequence["status"], "available")
        self.assertEqual(Path(sequence["_source_path"]), chosen)

    def test_same_named_projects_keep_independent_sequence_identity_and_assets(self):
        roots, paths = [], []
        for project_id in ("project-A", "project-B"):
            root = self.root / project_id
            paths.append(root / "runs" / self.run_id / "frames.json")
            self.write_rows(root)
            roots.append({"item": self.item, "project_path": str(self.project_path), "project_id": project_id})
            self.config["project_assets_roots"][project_id] = str(root)
        sequences = [build_analysis_sequence(row, self.config)[0] for row in roots]
        self.assertNotEqual(sequences[0]["sequence_id"], sequences[1]["sequence_id"])
        self.assertEqual(Path(sequences[0]["_source_path"]), paths[0])
        self.assertEqual(Path(sequences[1]["_source_path"]), paths[1])
        self.assertEqual(sequence_id("project-A", "item-1", self.run_id), sequences[0]["sequence_id"])

    def test_frame_chart_toggle_disables_reading(self):
        self.config["include_frame_charts"] = False
        sequence, rows = build_analysis_sequence(self.project_row, self.config)
        self.assertEqual(sequence["status"], "disabled")
        self.assertIsNone(rows)

    def test_package_writes_one_safe_local_data_script(self):
        assets = self.root / "assets"
        self.write_rows(assets)
        self.config["project_assets_roots"] = {"project-1": str(assets)}
        sequence, rows = build_analysis_sequence(self.project_row, self.config)
        sequence["events"] = [{"event":"</script>&\u2028", "frame":6, "source":"automatic"}]
        sequence["video_reason"] = "未打包影片"
        result = {"manifest": {"records": [], "project_completeness": [], "analysis_sequences": [sequence]},
                  "analysis_frames": {sequence["sequence_id"]: rows}, "errors": [], "warnings": []}
        out = self.root / "delivery"
        out.mkdir()
        _package_assets(result, out, [], {"include_videos":False}, {"sources": {}, "source_paths": {}})
        scripts = list((out / "analysis").glob("*.js"))
        self.assertEqual(len(scripts), 1)
        text = scripts[0].read_text(encoding="utf-8")
        self.assertIn(sequence["sequence_id"], text)
        self.assertNotIn("</script>", text)
        self.assertIn("\\u003c/script\\u003e\\u0026\\u2028", text)
        self.assertEqual(sequence["data_path"], f"analysis/{scripts[0].name}")
        self.assertFalse(sequence.get("_source_path"))

    def test_legacy_config_defaults_frame_charts_on(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.json"
            path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
            config = load_config(path)
        self.assertTrue(config["include_frame_charts"])
        self.assertEqual(config["project_assets_roots"], {})


if __name__ == "__main__":
    unittest.main()
