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

    def test_legacy_phases_name_event_and_unassessed_videos_as_distinct_runs(self):
        for day in ("2026-09-15", "2026-09-18", "2026-09-22"):
            for embedded in (False, True):
                with self.subTest(day=day, embedded=embedded):
                    records, sequences, pairings = [], [], []
                    for number, letter in enumerate("ABCD", 1):
                        source = self.root / f"{day}-P{number}.mp4"
                        source.write_bytes(letter.encode())
                        pairing = self.pairing(f"run{number}", source, scenario=6, voyage_date=day)
                        pairing["run"]["phase"] = "P1" if day == "2026-09-15" else f"P{number}"
                        pairing["run"]["note"] = f"{number * 5}kt" if day == "2026-09-15" else ""
                        pairings.append(pairing)
                        link = {"status": "linked", "pairing_run_id": f"run{number}",
                                "video_path": str(source), "video_pairing_run": pairing["run"]}
                        if number < 4:
                            for event in ("FirstDetection", "StableStart"):
                                row = self.record(f"{letter}-{event}", f"run{number}", source, f"seq{number}")
                                if embedded:
                                    row["analysis_link"].update(link)
                                records.append(row)
                        sequences.append({"sequence_id": f"seq{number}", "camera": self.camera,
                                          "pairing_run_id": f"run{number}", "_video_link": link})
                    result = {"errors": [], "warnings": [], "manifest": {
                        "records": records, "analysis_sequences": sequences}}
                    plan = _video_package_plan(result, [] if embedded else pairings, {"include_videos": True})
                    self.assertEqual(result["errors"], [])
                    self.assertEqual(len(plan["sources"]), 4)
                    for number, letter in enumerate("ABCD", 1):
                        expected = f"videos/{day}/Camera1_Test6_{letter}_{day.replace('-', '')[2:]}.mp4"
                        self.assertEqual(plan["sequence_paths"][f"seq{number}"], expected)
                        if number < 4:
                            for event in ("FirstDetection", "StableStart"):
                                self.assertEqual(plan["record_paths"][f"{letter}-{event}"], expected)

    def test_legacy_correction_does_not_guess_other_dates_or_angles(self):
        source = self.root / "source.mp4"
        row = self.record("event", "run", source)
        for day, angle, phase, expected in (("2026-09-15", "A", "P2", ""),
                                            ("2026-09-22", "B", "P3", "B"),
                                            ("2026-09-22", "A", "", "A")):
            pairing = self.pairing("run", source, angle=angle, voyage_date=day)
            pairing["run"]["phase"] = phase
            values = paired_video_filename_values(row, [pairing], source)
            self.assertEqual(values["run"], expected)

    def test_september_15_notes_require_an_unambiguous_speed_suffix(self):
        source = self.root / "source.mp4"
        source.touch()
        record = self.record("event", "run", source)
        for note, expected in (("5kt", "A"), ("10KT", "B"), ("航次備註_15 kt ", "C"),
                               ("20kt", "D"), ("", ""), ("25kt", ""), ("15.5kt", ""),
                               ("5kt 10kt", ""), ("10kt 取消", "")):
            with self.subTest(note=note):
                pairing = self.pairing("run", source, voyage_date="2026-09-15")
                pairing["run"].update(note=note, phase="P1")
                self.assertEqual(paired_video_filename_values(record, [pairing], source)["run"], expected)
        result = {"errors": [], "warnings": [], "manifest": {"records": [record], "analysis_sequences": []}}
        plan = _video_package_plan(result, [pairing], {"include_videos": True})
        self.assertEqual(plan["sources"], {})
        self.assertIn("note 尾綴", result["errors"][0]["message"])

    def test_project_item_note_is_available_when_embedded_note_is_empty(self):
        from delivery_tool.dataset import project_video_link
        item = {"path": "clip.mp4", "note": "10kt", "pairing_run": {"note": ""}}
        link = project_video_link({"item": item, "project_path": str(self.root / "project.json")})
        self.assertEqual(link["video_pairing_run"]["note"], "10kt")
        self.assertEqual(item["pairing_run"]["note"], "")

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
