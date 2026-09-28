from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
import json
from unittest.mock import patch

from delivery_tool.builder import _package_assets, _video_package_plan, build
from delivery_tool.config import DEFAULT_CONFIG, load_config
from delivery_tool.exporters import write_csv, write_html
from delivery_tool.video_naming import (DEFAULT_VIDEO_FILENAME_TEMPLATE, paired_video_filename_values,
                                        render_video_filename_stem, validate_video_filename_template)


class VideoNamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.camera = "Camera 1"

    def tearDown(self):
        self.temp.cleanup()

    def pairing(self, run_id: str, video: Path, *, angle="A", scenario=1, voyage_date="2026-09-22"):
        return {"run": {"id": run_id, "angle": angle, "scenarioId": scenario,
                         "voyage": {"date": voyage_date},
                         "cameraMatches": {"cam01": {"path": str(video), "name": video.name}}},
                "cameras": {"cam01": self.camera}}

    def record(self, key: str, run_id: str, video: Path, sequence_id="sequence-1"):
        return {"key": key, "camera": self.camera, "video_source_path": str(video),
                "analysis_link": {"pairing_run_id": run_id, "sequence_id": sequence_id, "project_path": ""},
                "parsed": {"filename": "P1_FirstDetection_Frame120_5kt", "event": "FirstDetection",
                           "frame": 120, "distance_m": 5}}

    def test_default_and_custom_templates(self):
        values = {"camera": "1", "test": "1", "run": "A", "date": date(2026, 9, 22)}
        self.assertEqual(render_video_filename_stem(DEFAULT_VIDEO_FILENAME_TEMPLATE, values),
                         "Camera1_Test1_A_260922")
        self.assertEqual(render_video_filename_stem(
            "{date:%Y-%m-%d}+{test}+{camera}+{run}", values), "2026-09-22+1+1+A")
        legacy_config = self.root / "legacy.json"
        legacy_config.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
        self.assertEqual(load_config(legacy_config)["video_filename_template"],
                         DEFAULT_CONFIG["video_filename_template"])

    def test_values_come_from_paired_voyage_not_keyframe_tokens(self):
        video = self.root / "source.mp4"
        video.touch()
        record = self.record("event", "paired-run", video)
        pairing = self.pairing("paired-run", video)
        values = paired_video_filename_values(record, [pairing], video)
        self.assertEqual(values, {"camera": "1", "test": "1", "run": "A", "date": date(2026, 9, 22)})
        self.assertEqual(render_video_filename_stem(DEFAULT_VIDEO_FILENAME_TEMPLATE, values),
                         "Camera1_Test1_A_260922")

    def test_invalid_template_and_output_filename_are_rejected(self):
        for template in ("", "{unknown}", "{date", "{camera!r}", "bad/name-{camera}",
                         "{date:%Q}", "{date:%Y/%m/%d}"):
            with self.subTest(template=template), self.assertRaises(ValueError):
                validate_video_filename_template(template)
        with self.assertRaisesRegex(ValueError, "非法字元"):
            render_video_filename_stem("{camera}", {"camera": "1/2"})
        with self.assertRaisesRegex(ValueError, "副檔名"):
            render_video_filename_stem("{camera}.mp4", {"camera": "1"})
        result = {"errors": [], "warnings": [], "manifest": {"records": [], "analysis_sequences": []}}
        _video_package_plan(result, [], {"video_filename_template": "bad/name", "include_videos": False})
        self.assertEqual(result["errors"][0]["code"], "invalid_video_filename_template")

    def test_missing_paired_fields_are_reported(self):
        video = self.root / "source.mp4"
        video.touch()
        result = {"errors": [], "warnings": [], "manifest": {
            "records": [self.record("event", "paired-run", video)], "analysis_sequences": []}}
        plan = _video_package_plan(result, [self.pairing("paired-run", video, scenario="")],
                                   {"include_videos": True})
        self.assertEqual(plan["sources"], {})
        self.assertEqual(result["errors"][0]["code"], "missing_video_filename_data")
        self.assertIn("test", result["errors"][0]["message"])

    def test_shared_source_is_copied_once_and_both_manifest_paths_match(self):
        source = self.root / "source.mp4"
        source.write_bytes(b"video-bytes")
        records = [self.record("event-a", "paired-run", source), self.record("event-b", "paired-run", source)]
        records[1]["analysis_link"]["sequence_id"] = "sequence-1"
        pairings = [self.pairing("paired-run", source)]
        sequence = {"sequence_id": "sequence-1", "pairing_run_id": "paired-run", "camera": self.camera,
                    "status": "disabled", "project_id": "project", "item_id": "item", "run_id": "analysis"}
        result = {"errors": [], "warnings": [], "manifest": {"records": records, "analysis_sequences": [sequence]},
                  "analysis_frames": {}}
        config = {"include_videos": True, "video_filename_template": DEFAULT_VIDEO_FILENAME_TEMPLATE,
                  "report": {}}
        plan = _video_package_plan(result, pairings, config)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(plan["sources"]), 1)
        self.assertEqual(plan["record_paths"]["event-a"], plan["record_paths"]["event-b"])

        output = self.root / "delivery"
        output.mkdir()
        _package_assets(result, output, pairings, config, plan)
        relative = "videos/2026-09-22/Camera1_Test1_A_260922.mp4"
        self.assertEqual(records[0]["video"], relative)
        self.assertEqual(records[1]["video"], relative)
        self.assertEqual(sequence["video"], relative)
        self.assertEqual((output / Path(relative)).read_bytes(), b"video-bytes")
        csv_path = write_csv(output / "result_summary.csv", result["manifest"])
        self.assertIn(relative, csv_path.read_text(encoding="utf-8-sig"))
        write_html(output / "index.html", result["manifest"], config, {})
        self.assertIn(relative, (output / "index.html").read_text(encoding="utf-8"))
        self.assertIn("video.src = sequence.video", (output / "report.js").read_text(encoding="utf-8"))

    def test_different_sources_with_same_name_block_the_plan(self):
        first, second = self.root / "first.mp4", self.root / "second.mp4"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        records = [self.record("event-a", "run-a", first), self.record("event-b", "run-b", second)]
        result = {"errors": [], "warnings": [], "manifest": {"records": records, "analysis_sequences": []}}
        pairings = [self.pairing("run-a", first), self.pairing("run-b", second)]
        plan = _video_package_plan(result, pairings, {"include_videos": True})
        self.assertTrue(any(error["code"] == "video_filename_conflict" for error in result["errors"]))
        self.assertEqual(plan["sources"], {str(first.resolve()).casefold(): first.resolve()})
        self.assertEqual(first.read_bytes(), b"first")
        self.assertEqual(second.read_bytes(), b"second")
        output_parent = self.root / "must-not-be-created"
        build_result = {"ok": True, "errors": [], "warnings": [], "pairings": pairings,
                        "manifest": {"records": [self.record("event-a", "run-a", first),
                                                   self.record("event-b", "run-b", second)],
                                     "analysis_sequences": []}}
        with patch("delivery_tool.builder.validate_config", return_value=build_result):
            with self.assertRaisesRegex(ValueError, "video_filename_conflict"):
                build({"include_videos": True, "output_dir": str(output_parent), "report": {}})
        self.assertFalse(output_parent.exists())


if __name__ == "__main__":
    unittest.main()
