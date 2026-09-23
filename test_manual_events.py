import copy
import csv
import unittest
from pathlib import Path

import cv2
import numpy as np

import batch_core as core
import test_batch as batch_tests
import test_batch_ui as ui_tests


class ManualEventTests(unittest.TestCase):
    setUp = batch_tests.BatchWorkflowTests.setUp
    tearDown = batch_tests.BatchWorkflowTests.tearDown
    analyze = batch_tests.BatchWorkflowTests.analyze

    def test_manual_only_roundtrip_export_and_boundaries(self):
        for invalid in (0, 12, 1.5, True):
            with self.assertRaises(ValueError):
                core.set_manual_event(self.item, "manual_first_detection", invalid)
        core.set_manual_event(self.item, "manual_first_detection", 1, "人工確認浮球")
        core.set_manual_event(self.item, "manual_stable_confirmation", 11)
        project = core.new_project()
        project["items"] = [self.item]
        core.save_project(self.root / "project.json", project)
        restored = core.load_project(self.root / "project.json")["items"][0]
        self.assertEqual(restored["manual_events"], self.item["manual_events"])
        restored["settings"]["roi"] = [0, 0, 1, 1]  # Exports must retain the annotation snapshot.
        out, summaries = core.export_batch([restored], self.root)
        self.assertEqual(summaries[0]["manual_first_detection_frame"], 1)
        self.assertEqual(summaries[0]["manual_stable_confirmation_timestamp"], 1)
        self.assertEqual(summaries[0]["export_status"], "manual_only")
        with (out / "event_images_manifest.csv").open(encoding="utf-8-sig") as source:
            records = [r for r in csv.DictReader(source) if r.get("event_source") == "manual"]
        self.assertEqual(len(records), 3)
        self.assertEqual(summaries[0]["manual_sustained_stable_start_frame"], 9)
        for record in records:
            self.assertEqual(record["status"], "completed")
            image = cv2.imdecode(np.fromfile(out / record["raw_filename"], np.uint8), cv2.IMREAD_COLOR)
            np.testing.assert_array_equal(image, core.read_exact_frame(self.path, int(record["frame"])))
            annotation = core.resolved_manual_events(restored)[record["event"]]
            settings = core.manual_event_settings(restored, annotation)
            mask, boxes, _ = core.segment_frame(image, settings)
            actual_mask = cv2.imdecode(np.fromfile(out / record["mask_filename"], np.uint8), cv2.IMREAD_GRAYSCALE)
            np.testing.assert_array_equal(actual_mask, mask)
            expected_overlay = core.compose_frame(image, settings, "Overlay", boxes=boxes,
                                                 include_bbox=True, include_roi=True,
                                                 bbox_style=core.LEGACY_BBOX_STYLE)
            overlay = cv2.imdecode(np.fromfile(out / record["overlay_filename"], np.uint8), cv2.IMREAD_COLOR)
            np.testing.assert_array_equal(overlay, expected_overlay)
        self.assertEqual(len(list(out.rglob("*.png"))), 9)
        index = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn("manual_sustained_stable_start", index)
        for record in records:
            self.assertIn(record["overlay_filename"], index)

    def test_derived_start_snapshot_edit_clear_and_legacy(self):
        core.set_manual_event(self.item, "manual_stable_confirmation", 8)
        self.item["settings"]["stable_confirm_frames"] = 7
        self.assertEqual(core.resolved_manual_events(self.item)[core.MANUAL_START]["frame"], 6)
        core.set_manual_event(self.item, "manual_stable_confirmation", 9)
        self.assertEqual(core.resolved_manual_events(self.item)[core.MANUAL_START]["frame"], 3)
        core.set_manual_event(self.item, "manual_stable_confirmation", 2)
        start = core.resolved_manual_events(self.item)[core.MANUAL_START]
        self.assertEqual(start["frame"], 1)
        self.assertEqual(start["timestamp"], 0)
        self.assertTrue(start["clamped_to_first_frame"])
        confirmation = self.item["manual_events"]["manual_stable_confirmation"]
        confirmation.pop("settings")
        confirmation.pop("fps")
        self.assertEqual(core.resolved_manual_events(self.item)[core.MANUAL_START]["frame"], 1)
        self.item["manual_events"].clear()
        self.assertNotIn(core.MANUAL_START, core.resolved_manual_events(self.item))

    def test_rerun_and_outside_analysis_do_not_replace_manual_or_auto(self):
        run = self.analyze()
        snapshot = copy.deepcopy(run["summary"])
        core.set_manual_event(self.item, "manual_first_detection", 9, "人工與自動不同")
        self.assertEqual(core.stale_reason(self.item), "")
        self.assertEqual(self.item["latest_run"]["summary"], snapshot)
        self.item["settings"]["end_percent"] = 60
        self.analyze()
        self.assertEqual(self.item["manual_events"]["manual_first_detection"]["frame"], 9)
        out, summaries = core.export_batch([self.item], self.root)
        self.assertEqual(summaries[0]["manual_first_detection_frame"], 9)
        self.assertEqual(summaries[0]["first_detection_frame"], 1)
        self.assertTrue(list(out.rglob("manual_first_detection_frame_000009_raw.png")))

    def test_changed_source_rejected_missing_source_preserves_annotation(self):
        identity = core.source_identity(self.path)
        core.set_manual_event(self.item, "manual_first_detection", 2)
        annotation = copy.deepcopy(self.item["manual_events"])
        with self.path.open("ab") as output:
            output.write(b"changed")
        with self.assertRaises(ValueError):
            core.set_manual_event(self.item, "manual_first_detection", 3, expected_identity=identity)
        self.assertTrue(core.manual_event_reason(self.item, annotation["manual_first_detection"]))
        self.path.unlink()
        out, summaries = core.export_batch([self.item], self.root)
        self.assertEqual(summaries[0]["export_status"], "partial")
        self.assertEqual(self.item["manual_events"], annotation)
        self.assertFalse(list(out.rglob("*.png")))
        self.assertTrue(list(out.rglob("manual_events.json")))

    def test_old_project_loads_without_annotations(self):
        self.item.pop("manual_events")
        project = core.new_project()
        project["items"] = [self.item]
        core.save_project(self.root / "old.json", project)
        self.assertEqual(core.load_project(self.root / "old.json")["items"][0]["manual_events"], {})


class ManualDesktopTests(unittest.TestCase):
    setUp = ui_tests.DesktopWorkflowTests.setUp
    tearDown = ui_tests.DesktopWorkflowTests.tearDown
    wait_worker = ui_tests.DesktopWorkflowTests.wait_worker

    def test_mark_edit_review_plot_jump_clear_and_rerun(self):
        self.app.open_editor(self.item)
        self.root.update()
        editor = self.app.editor
        editor.read_frame(10)
        editor.manual_note.set("由人工確認")
        self.assertTrue(editor.mark_current())
        self.assertEqual(self.item["manual_events"]["manual_first_detection"]["frame"], 11)
        editor.close()
        self.app.start_batch(True)
        self.wait_worker()
        self.assertEqual(self.item["manual_events"]["manual_first_detection"]["frame"], 11)
        self.app.open_editor(self.item, True, 11)
        self.root.update()
        editor = self.app.editor
        self.assertEqual(editor.manual_frame.get(), "11")
        self.assertTrue(editor.review_plot.find_withtag("manual_event"))
        editor.manual_frame.set("12")
        self.assertTrue(editor.save_manual())
        editor.read_frame(0)
        editor.jump_manual()
        self.assertEqual(editor.frame_index, 11)
        editor.manual_kind.set("人工穩定確認")
        editor.load_manual_fields()
        editor.manual_frame.set("15")
        self.assertTrue(editor.save_manual())
        self.assertEqual(len(self.item["manual_events"]), 2)
        start = core.resolved_manual_events(self.item)[core.MANUAL_START]
        self.assertEqual(start["frame"], 13)
        self.assertIn("人工穩定起點 F13", editor.manual_status.get())
        editor.jump_manual_start()
        self.assertEqual(editor.frame_index, 12)
        editor.clear_manual()
        self.assertNotIn("manual_stable_confirmation", self.item["manual_events"])
        self.assertNotIn(core.MANUAL_START, core.resolved_manual_events(self.item))
        editor.manual_kind.set("人工首次檢出")
        editor.load_manual_fields()
        editor.clear_manual()
        self.assertFalse(editor.review_plot.find_withtag("manual_event"))
        self.assertEqual(core.load_project(self.app.project_path)["items"][0]["manual_events"], {})
        self.assertFalse(self.errors, self.errors)


if __name__ == "__main__":
    unittest.main()
