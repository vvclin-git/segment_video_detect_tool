from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import batch_core
from delivery_tool.collage import render_collage
from delivery_tool.dataset import validate_config
from delivery_tool.parsing import history_run_mapping, parse_filename


class DeliveryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.filename = "20260918_Test01_A_P1_Camera1_FirstDetection_Frame000120.png"
        self.images = self.root / "images" / "2026-09-18"
        self.images.mkdir(parents=True)
        self.base = np.full((768, 960, 3), (100, 100, 100), dtype=np.uint8)
        Image.fromarray(self.base).save(self.images / self.filename)
        self.annotations = self.root / "annotations"
        self.annotations.mkdir()
        self.mask_root = self.root / "masks"
        self.mask_root.mkdir()
        label = {"imagePath": self.filename, "imageWidth": 960, "imageHeight": 768,
                 "shapes": [{"label": "ship", "shape_type": "polygon",
                             "points": [[100, 100], [200, 100], [200, 200], [100, 200]]}]}
        (self.annotations / "event.json").write_text(json.dumps(label), encoding="utf-8")
        mask = np.zeros((768, 960), dtype=np.uint8)
        mask[120:180, 120:180] = 255
        Image.fromarray(mask, mode="L").save(self.mask_root / "mask.png")
        self.eval_csv = self.root / "evaluation.csv"
        with self.eval_csv.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(output, fieldnames=["image", "date", "camera", "test", "run_letter", "phase", "event", "frame", "mIoU", "檢出距離_m", "fps"])
            writer.writeheader()
            writer.writerow({"image": self.filename, "date": "2026-09-18", "camera": "Camera 1", "test": "Test01",
                             "run_letter": "A", "phase": "P1", "event": "FirstDetection", "frame": 120,
                             "mIoU": "0.75", "檢出距離_m": "12.5", "fps": "30"})
        self.attachment_csv = self.root / "attachments.csv"
        with self.attachment_csv.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(output, fieldnames=["image", "labelme_json", "mask_asset"])
            writer.writeheader()
            writer.writerow({"image": self.filename, "labelme_json": "event.json", "mask_asset": "mask.png"})
        self.config = {"schema_version": 1, "_config_dir": str(self.root), "test_dates": ["2026-09-18"],
                       "batch_id": "fixture", "projects": [], "pairings": [],
                       "evaluation_csvs": [str(self.eval_csv)], "attachment_index": str(self.attachment_csv),
                       "attachment_root": str(self.annotations), "image_root": str(self.images),
                       "mask_root": str(self.mask_root), "video_root": "", "output_dir": str(self.root / "out"),
                       "include_videos": False, "pairing_run_id_map": {}, "event_links": {},
                       "report": {"pdf_layout": "overview", "font_path": "", "cover": True,
                                  "summary_table": True, "missing_appendix": False}}

    def tearDown(self):
        self.temp.cleanup()

    def test_filename_parser_and_historical_run_letter(self):
        parsed = parse_filename(self.filename)
        self.assertEqual(parsed["date"], "2026-09-18")
        self.assertEqual(parsed["camera"], "Camera 1")
        self.assertEqual(parsed["event"], "FirstDetection")
        self.assertEqual(parsed["frame"], 120)
        historical = parse_filename("20260922_Test1_A_P3_Camera2_StableStart_Frame300.png")
        self.assertEqual(history_run_mapping(historical)["run_letter"], "C")

    def test_validation_keeps_original_metrics_and_aligned_assets(self):
        result = validate_config(self.config)
        record = result["manifest"]["records"][0]
        self.assertEqual(record["iou"], 0.75)
        self.assertEqual(record["distance_m"], 12.5)
        self.assertEqual(record["nominal_time_s"], 119 / 30)
        self.assertEqual(record["attachment"]["gt_status"], "valid")
        self.assertEqual(record["attachment"]["mask_status"], "valid")
        self.assertFalse(any(e["code"] == "invalid_attachment_coordinates" for e in result["errors"]))

    def test_collage_uses_same_unresized_image_and_blends_mask_foreground(self):
        result = validate_config(self.config)
        record = result["manifest"]["records"][0]
        output = self.root / "collage.png"
        overlay = self.root / "overlay.png"
        render_collage(record, output, overlay_output=overlay)
        with Image.open(overlay) as image:
            self.assertEqual(image.size, (960, 768))
            self.assertEqual(image.getpixel((700, 700)), tuple(self.base[700, 700]))
        collage = np.asarray(Image.open(output).convert("RGB"))
        h, w = 768, 960
        self.assertTrue(np.array_equal(collage[48:48 + h, :w], self.base))
        self.assertTrue(np.array_equal(collage[48 + 700, 2 * (w + 28) + 700], self.base[700, 700]))
        self.assertTrue(np.array_equal(collage[48 + 140, 2 * (w + 28) + 140], (54, 187, 93)))

    def test_seg_panel_and_dimension_validation(self):
        seg_dir = self.images.parent / "Seg"
        seg_dir.mkdir()
        seg = seg_dir / self.filename
        Image.new("RGB", (960, 768), (12, 34, 56)).save(seg)
        result = validate_config(self.config)
        self.assertTrue(result["ok"])
        record = result["manifest"]["records"][0]
        self.assertEqual(record["attachment"]["seg_status"], "valid")
        output = self.root / "triple.png"
        render_collage(record, output)
        with Image.open(output) as image:
            self.assertEqual(image.size, (2936, 860))
            self.assertEqual(image.getpixel((988 + 400, 48 + 400)), (12, 34, 56))
        Image.new("RGB", (100, 100)).save(seg)
        result = validate_config(self.config)
        self.assertTrue(any(e["code"] == "invalid_seg_image" for e in result["errors"]))

    def test_pdf_combines_unlinked_camera_with_unique_voyage(self):
        from delivery_tool.pdf_report import _run_rows
        common = dict(batch_id="b", date="2026-09-22", test="1", run_letter="A", phase="P1")
        rows = [dict(common, camera="Camera 1", analysis_link={"pairing_run_id": "run1"}),
                dict(common, camera="Camera 2", analysis_link={})]
        self.assertEqual(len(_run_rows(rows)), 1)
        rows.append(dict(common, camera="Camera 1", analysis_link={"pairing_run_id": "run2"}))
        self.assertEqual(len(_run_rows(rows)), 3)

    def test_wrong_mask_dimensions_are_a_blocking_error(self):
        Image.new("L", (320, 240), 255).save(self.mask_root / "mask.png")
        result = validate_config(self.config)
        self.assertFalse(result["ok"])
        self.assertTrue(any(e["code"] == "invalid_attachment_coordinates" for e in result["errors"]))

    def test_duplicate_key_and_invalid_metrics_are_blocking(self):
        with self.eval_csv.open("w", newline="", encoding="utf-8-sig") as output:
            fields = ["image", "date", "camera", "test", "run_letter", "phase", "event", "frame", "mIoU", "檢出距離_m"]
            writer = csv.DictWriter(output, fieldnames=fields); writer.writeheader()
            row = {"image": self.filename, "date": "2026-09-18", "camera": "Camera 1", "test": "Test01",
                   "run_letter": "A", "phase": "P1", "event": "FirstDetection", "frame": 120, "mIoU": 1.2, "檢出距離_m": -3}
            writer.writerow(row); writer.writerow(row)
        result = validate_config(self.config)
        codes = {e["code"] for e in result["errors"]}
        self.assertTrue({"duplicate_key", "iou_out_of_range", "negative_distance"}.issubset(codes))

    def test_project_link_checks_event_and_frame_and_keeps_both_sources(self):
        settings = {**batch_core.DEFAULTS, "lower": list(batch_core.DEFAULTS["lower"]),
                    "upper": list(batch_core.DEFAULTS["upper"])}
        item = {"id": "item-1", "settings": settings, "manual_events": {
                    "manual_first_detection": {"frame": 120, "timestamp": 119 / 30, "fps": 30, "settings": settings},
                    "manual_stable_confirmation": {"frame": 123, "timestamp": 122 / 30, "fps": 30, "settings": settings}},
                "pairing_run_id": "run-1", "pairing_camera_id": "cam-1", "camera": "Camera 1",
                "status": "completed", "latest_run": {"settings": settings,
                    "metadata": {"fps": 30, "frame_count": 300},
                    "summary": {"first_detection_frame": 120, "first_sustained_stable_start_frame": 120}}}
        project = batch_core.new_project(); project["items"] = [item]
        project_path = self.root / "project.json"
        project_path.write_text(json.dumps(project), encoding="utf-8")
        pair_path = self.root / "pairing.json"
        pair_path.write_text(json.dumps({"cameras": [{"id": "cam-1", "name": "Camera 1"}],
                                         "runs": [{"id": "run-1", "cameraMatches": {}}]}), encoding="utf-8")
        self.config["projects"] = [str(project_path)]; self.config["pairings"] = [str(pair_path)]
        self.config["pairing_run_id_map"] = {"2026-09-18|Test01|A|P1|Camera 1": "run-1"}
        result = validate_config(self.config)
        record = result["manifest"]["records"][0]
        self.assertEqual(record["analysis_link"]["status"], "linked")
        self.assertEqual(record["analysis_link"]["event_sources"], ["automatic", "manual"])

    def test_changed_project_frame_is_not_accepted_by_nearest_match(self):
        settings = {**batch_core.DEFAULTS, "lower": list(batch_core.DEFAULTS["lower"]),
                    "upper": list(batch_core.DEFAULTS["upper"])}
        project = batch_core.new_project()
        project["items"] = [{"id": "item-1", "settings": settings, "manual_events": {},
            "pairing_run_id": "run-1", "camera": "Camera 1", "status": "completed",
            "latest_run": {"settings": settings, "metadata": {"fps": 30, "frame_count": 300},
                           "summary": {"first_detection_frame": 121}}}]
        project_path = self.root / "project.json"; project_path.write_text(json.dumps(project), encoding="utf-8")
        self.config["projects"] = [str(project_path)]
        self.config["pairing_run_id_map"] = {"2026-09-18|Test01|A|P1|Camera 1": "run-1"}
        result = validate_config(self.config)
        self.assertTrue(any(e["code"] == "project_event_mismatch" for e in result["errors"]))

    def test_summary_table_does_not_add_blank_continuation_page(self):
        from delivery_tool.pdf_report import _draw_summary

        class RecordingCanvas:
            def __init__(self):
                self.pages = 0

            def showPage(self):
                self.pages += 1

            def __getattr__(self, _name):
                return lambda *_args, **_kwargs: None

        def records_for(group_count):
            return [{"date": f"2026-09-{index + 1:02d}", "test": str(index), "batch_id": "fixture",
                     "image_path": "", "attachment": {}, "analysis_link": {}}
                    for index in range(group_count)]

        canvas = RecordingCanvas()
        _draw_summary(canvas, {}, records_for(20), 2)
        self.assertEqual(canvas.pages, 1)

        canvas = RecordingCanvas()
        _draw_summary(canvas, {}, records_for(21), 2)
        self.assertEqual(canvas.pages, 2)

    @unittest.skipUnless(importlib.util.find_spec("reportlab"), "ReportLab is not installed in this runtime")
    def test_build_outputs_both_pdf_layouts_and_relative_manifest_paths(self):
        from delivery_tool.builder import build

        self.config["report"]["pdf_layout"] = "both"
        self.config["report"]["comparison_overlay"] = {
            "opacity": 0.4,
            "colors": {"overlap": "#123456", "gt_only": "#654321", "prediction_only": "#00AABB"},
        }
        result = build(self.config)
        self.assertEqual(len(result["pdfs"]), 2)
        self.assertTrue((result["delivery_dir"] / "index.html").is_file())
        self.assertTrue((result["delivery_dir"] / "result_summary.csv").is_file())
        manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
        record = manifest["records"][0]
        self.assertEqual(record["comparison_counts"]["tp"], 3600)
        self.assertGreater(record["comparison_counts"]["fn"], 0)
        self.assertEqual(record["comparison_counts"]["fp"], 0)
        self.assertFalse(Path(record["image_path"]).is_absolute())
        self.assertFalse(Path(record["collage"]).is_absolute())
        self.assertFalse(Path(record["overlay_image"]).is_absolute())
        self.assertTrue((result["delivery_dir"] / record["overlay_image"]).is_file())
        self.assertEqual(record["iou"], 0.75)
        self.assertEqual(manifest["comparison_overlay"]["mode"], "three_color_gt_prediction")
        self.assertEqual(manifest["comparison_overlay"]["opacity"], 0.4)
        self.assertEqual(manifest["comparison_overlay"]["colors"], {
            "overlap": "#123456", "gt_only": "#654321", "prediction_only": "#00AABB"})
        build_config = json.loads((result["delivery_dir"] / "internal" / "build_config.json").read_text(encoding="utf-8"))
        self.assertEqual(build_config["report"]["comparison_overlay"], {
            "opacity": 0.4, "colors": {"overlap": "#123456", "gt_only": "#654321", "prediction_only": "#00AABB"}})
        html = (result["delivery_dir"] / "index.html").read_text(encoding="utf-8")
        self.assertIn("#123456", html)
        report_js = (result["delivery_dir"] / "report.js").read_text(encoding="utf-8")
        self.assertIn("['overlap', 'TP', counts.tp]", report_js)
        self.assertIn("['gt_only', 'FN', counts.fn]", report_js)
        self.assertIn("['prediction_only', 'FP', counts.fp]", report_js)
        self.assertNotIn("疊圖不透明度", report_js)
        self.assertLess(report_js.index("link.append(image)"), report_js.index("panel.append(title)"))

        from delivery_tool.pdf_report import _draw_comparison_legend

        class RecordingCanvas:
            def __init__(self):
                self.current_color, self.swatch_colors, self.labels = None, [], []

            def setFillColor(self, color):
                self.current_color = color

            def setFont(self, *_args):
                pass

            def rect(self, *_args, **_kwargs):
                self.swatch_colors.append(self.current_color)

            def drawString(self, _x, _y, value):
                self.labels.append(value)

        pdf_legend = RecordingCanvas()
        _draw_comparison_legend(pdf_legend, self.config)
        self.assertEqual([color.hexval().upper().replace("0X", "#") for color in pdf_legend.swatch_colors],
                         ["#123456", "#654321", "#00AABB"])
        self.assertIn("疊圖不透明度：40%", pdf_legend.labels[-1])


if __name__ == "__main__":
    unittest.main()
