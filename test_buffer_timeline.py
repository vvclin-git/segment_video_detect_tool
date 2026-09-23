import tkinter as tk
import unittest
from types import SimpleNamespace

from buffer_timeline import BufferTimeline
import test_batch_ui as ui_tests


class BufferStripTests(unittest.TestCase):
    def test_disjoint_ranges_resize_and_hover(self):
        root = tk.Tk()
        try:
            root.geometry("600x100")
            bar = BufferTimeline(root)
            bar.pack(fill="x")
            root.update()
            bar.set_state(100, 51, ((1, 3), (50, 55)))
            self.assertEqual(len(bar.canvas.find_withtag("cached")), 2)
            self.assertIn("9 幀", bar.caption.get())
            self.assertIn("F50–F55", bar.caption.get())
            left, right = bar._bounds()
            bar._hover(SimpleNamespace(x=left + .505 * (right - left)))
            self.assertIn("F51 · 已快取", bar.caption.get())
            bar._hover(SimpleNamespace(x=left + .2 * (right - left)))
            self.assertIn("未快取", bar.caption.get())
            bar.set_state(1, 1, ())
            self.assertFalse(bar.canvas.find_withtag("cached"))
            self.assertEqual(len(bar.canvas.find_withtag("cursor")), 1)
        finally:
            root.destroy()


class BufferDesktopTests(unittest.TestCase):
    setUp = ui_tests.DesktopWorkflowTests.setUp
    tearDown = ui_tests.DesktopWorkflowTests.tearDown

    def test_preview_and_independent_viewer_track_their_own_cache(self):
        self.app.open_editor(self.item)
        self.root.update()
        editor = self.app.editor
        editor.read_frame(12)
        self.assertEqual(editor.buffer_timeline.ranges, ((1, 1), (13, 13)))
        self.assertEqual(editor.buffer_timeline.current, 13)
        editor.open_frame_viewer()
        self.root.update()
        viewer = editor.frame_viewers[-1]
        viewer.read_frame(13)
        self.assertEqual(viewer.buffer_timeline.ranges, ((14, 14),))
        self.assertEqual(editor.buffer_timeline.ranges, ((1, 1), (13, 13)))
        viewer.close()
        self.assertFalse(self.errors, self.errors)
