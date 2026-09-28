import tempfile
import unittest
from pathlib import Path

from delivery_tool.dataset import _video_metadata
from delivery_tool.builder import _video_for_record


class VideoSearchDiagnosticsTests(unittest.TestCase):
    def test_project_conflicts_and_unconfirmed_links_do_not_select_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = {"key": "event", "camera": "Camera 1", "parsed": {}, "analysis_link": {
                "status": "linked", "pairing_run_id": "run1", "video_path": str(root / "project.mp4")}}
            pairings = [{"run": {"id": "run1", "cameraMatches": {"cam1": {"path": str(root / "other.mp4")}}},
                         "cameras": {"cam1": "Camera 1"}}]
            errors, warnings = [], []
            config = {"video_root": str(root), "include_videos": True}
            result = _video_metadata(record, pairings, config, errors, warnings)
            self.assertEqual(result["status"], "ambiguous")
            self.assertEqual(errors[0]["code"], "video_source_conflict")
            self.assertIsNone(_video_for_record(record, pairings, config, errors, warnings))
            record["analysis_link"]["status"] = "mismatch"
            warnings = []
            result = _video_metadata(record, [], config, [], warnings)
            self.assertEqual(result["status"], "source_unverified")
            self.assertNotIn("project.mp4", warnings[0]["message"])

    def test_search_failures_explain_paths_and_keep_each_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {"video_root": str(root / "honda_data"), "include_videos": True}
            record = {"key": "event1", "camera": "Camera 1", "parsed": {},
                      "analysis_link": {"pairing_run_id": "run1"}}
            source = str(root / "honda data" / "clip.mp4")
            pairs = [{"run": {"id": "run1", "cameraMatches": {"cam1": {"path": source}}},
                      "cameras": {"cam1": "Camera 1"}}]
            warnings, errors = [], []
            for key in ("event1", "event2"):
                record["key"] = key
                result = _video_metadata(record, pairs, config, errors, warnings)
                self.assertEqual(result["status"], "source_unverified")
            self.assertEqual([w["key"] for w in warnings], ["event1", "event2"])
            self.assertIn("不是可用資料夾", warnings[0]["message"])
            for expected in ("clip.mp4", source, config["video_root"], "run1", "Camera 1"):
                self.assertIn(expected, warnings[0]["message"])
            _video_for_record(record, pairs, config, errors, warnings)
            self.assertIn(source, warnings[-1]["message"])
            self.assertIn(config["video_root"], warnings[-1]["message"])

            Path(config["video_root"]).mkdir()
            for folder in ("a", "b"):
                target = Path(config["video_root"]) / folder / "clip.mp4"
                target.parent.mkdir()
                target.touch()
            result = _video_metadata(record, pairs, config, errors, warnings)
            self.assertEqual(result["status"], "ambiguous")
            for folder in ("a", "b"):
                self.assertIn(str(Path(config["video_root"]) / folder / "clip.mp4"), errors[-1]["message"])

    def test_pairing_failures_and_disabled_search_are_distinct(self):
        record = {"key": "event", "camera": "Camera 1", "parsed": {},
                  "analysis_link": {"pairing_run_id": "run1"}}
        config = {"video_root": ".", "include_videos": False}
        pairs = [{"run": {"id": "run2", "cameraMatches": {}}, "cameras": {}}]
        for pairings, expected in (([], "沒有可讀取的 Pairing"), (pairs, "找不到對應航次 ID")):
            warnings = []
            _video_metadata(record, pairings, config, [], warnings)
            self.assertIn(expected, warnings[0]["message"])
        pairs[0]["run"]["id"] = "run1"
        warnings = []
        _video_metadata(record, pairs, config, [], warnings)
        self.assertIn("沒有符合相機的影片 path", warnings[0]["message"])
        config["video_root"] = ""
        warnings = []
        _video_metadata(record, [], config, [], warnings)
        self.assertIn("因此未搜尋影片", warnings[0]["message"])


if __name__ == "__main__":
    unittest.main()
