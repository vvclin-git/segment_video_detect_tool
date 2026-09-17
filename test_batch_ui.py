"""Real Tk integration smoke tests (requires a desktop display)."""
import copy
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import batch_core as core
from batch_ui import BatchApp
from test_batch import synthetic_video


class DesktopWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.video = self.folder / "demo.avi"
        synthetic_video(self.video, [0]*6 + [1, 1, 1, 1, 0, 1, 1] + [1]*15 + [0]*10)
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = BatchApp(self.root)
        self.app.project_path = self.folder / "project.json"
        self.item = core.new_item(self.video)
        self.app.project["items"] = [self.item]
        self.app.changed()
        self.root.update()

    def tearDown(self):
        if self.app.editor:
            self.app.editor.close()
        self.root.destroy()
        self.temp.cleanup()

    def wait_worker(self):
        deadline = time.monotonic() + 20
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.root.update()
        self.assertFalse(self.app.busy, "Background worker timed out")
        self.assertFalse(self.errors, self.errors)

    def test_complete_desktop_workflow(self):
        self.app.open_editor(self.item)
        self.root.update()
        viewer = self.app.editor
        self.assertIsNotNone(viewer.frame)
        viewer.min_area.set(30)  # Numeric input uses the actual applied variable.
        viewer.end_percent.set("90")
        viewer.read_frame(12)
        self.assertEqual(viewer.frame_index, 12)
        viewer.roi = (10, 10, 70, 60)
        viewer.close()
        self.root.update()
        self.assertEqual(self.item["settings"]["min_area"], 30)
        self.assertEqual(self.item["settings"]["roi"], [10, 10, 70, 60])
        self.app.start_batch(True)
        self.wait_worker()
        self.assertEqual(self.item["status"], "completed", self.item.get("error"))
        self.app.tabs.select(self.app.results_tab)
        self.root.update()
        self.app.visible_plots()
        plot = self.app.plots[self.item["id"]]
        self.assertTrue(plot.rows)
        self.assertTrue(plot.find_withtag("first_sustained_stable_start"))
        self.app.event_jump(self.item, "first_sustained_stable_start")
        self.root.update()
        start_number = self.item["latest_run"]["summary"]["first_sustained_stable_start_frame"]
        self.assertEqual(self.app.editor.frame_index + 1, start_number)
        self.assertTrue(self.app.editor.review_plot.find_withtag("first_sustained_stable_start"))
        self.app.event_jump(self.item, "first_stable_confirmation")
        self.root.update()
        viewer = self.app.editor
        number = self.item["latest_run"]["summary"]["first_stable_confirmation_frame"]
        self.assertEqual(viewer.frame_index + 1, number)
        self.assertIn(f"Frame {number}", viewer.detail_text.get())
        self.assertTrue(viewer.review_plot.rows)
        self.assertEqual(viewer.review_plot.cursor, number)
        viewer.step(1)
        self.assertEqual(plot.cursor, number + 1)
        self.assertEqual(viewer.review_plot.cursor, number + 1)
        viewer.close()
        self.app.open_editor(self.item)
        self.root.update()
        self.app.editor.stable_confirm_frames.set(1)
        self.app.editor.close()
        self.assertTrue(core.stale_reason(self.item))
        self.app.start_batch(True)
        self.wait_worker()
        self.assertIn("recomputed_from", self.item["latest_run"])
        with patch("batch_ui.filedialog.askdirectory", return_value=str(self.folder)), patch("batch_ui.messagebox.showinfo"):
            self.app.export(True)
            self.wait_worker()
        self.assertTrue(list(self.folder.glob("batch_export_*/batch_summary.csv")))
        restored = core.load_project(self.app.project_path)
        self.assertEqual(restored["items"][0]["latest_run"]["run_id"], self.item["latest_run"]["run_id"])
        self.assertFalse(self.errors, self.errors)

    def test_worker_continues_after_failure_and_cancel(self):
        missing = core.new_item(self.folder / "missing.avi")
        self.app.project["items"].insert(0, missing)
        self.app.changed()
        self.app.start_batch(True)
        self.wait_worker()
        self.assertEqual(missing["status"], "failed")
        self.assertEqual(self.item["status"], "completed")
        old_run = self.item["latest_run"]["run_id"]
        self.app.start_batch(True)
        self.app.cancel_batch()
        self.wait_worker()
        self.assertEqual(self.item["status"], "cancelled")
        self.assertEqual(self.item["latest_run"]["run_id"], old_run)


if __name__ == "__main__":
    unittest.main()
