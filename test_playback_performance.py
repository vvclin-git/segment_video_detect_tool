"""Correctness contracts for playback work reduction."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import cv2
import numpy as np

from frame_render import segment_frame, compose_frame
from app import HSVVideoTester


class PlaybackAnalysisTests(unittest.TestCase):
    def test_scheduler_subtracts_processing_time_and_stops_cleanly(self):
        delays = []
        player = HSVVideoTester.__new__(HSVVideoTester)
        player.playing, player.frame_index, player.frame_count, player.fps = True, 0, 100, 30
        player.root = SimpleNamespace(after=lambda delay, callback: delays.append(delay))
        player.read_frame = lambda index: None
        with patch("app.perf_counter", side_effect=[1.0, 1.02]):
            player._play_tick()
        self.assertEqual(delays, [13])
        player.read_frame = lambda index: setattr(player, "playing", False)
        with patch("app.perf_counter", return_value=2.0):
            player._play_tick()
        self.assertEqual(delays, [13])

    def test_roi_optimization_matches_full_frame_reference(self):
        frame = np.random.default_rng(7).integers(0, 256, (110, 160, 3), dtype=np.uint8)
        for roi in (None, [13, 7, 88, 92], [0, 0, 1, 1], [158, 100, 200, 200]):
            settings = dict(lower=[30, 90, 80], upper=[120, 255, 255], min_area=2, roi=roi)
            expected = cv2.inRange(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV),
                                   np.array(settings["lower"], np.uint8), np.array(settings["upper"], np.uint8))
            if roi:
                restricted = np.zeros_like(expected)
                x1, y1, x2, y2 = roi
                restricted[y1:y2, x1:x2] = expected[y1:y2, x1:x2]
                expected = restricted
            count, _, stats, _ = cv2.connectedComponentsWithStats(expected, connectivity=8)
            boxes = sorted([tuple(map(int, stats[i])) for i in range(1, count)
                            if stats[i, 4] >= settings["min_area"]], key=lambda b: b[4], reverse=True)
            actual = segment_frame(frame, settings)
            np.testing.assert_array_equal(actual[0], expected)
            self.assertEqual(actual[1], boxes)
            self.assertEqual(actual[2], cv2.countNonZero(expected))
            for mode in ("Mask", "Overlay"):
                reference = compose_frame(frame, settings, mode, boxes=boxes, highlight_indices=[])
                with patch("frame_render.segment_frame", side_effect=AssertionError("Duplicate analysis")):
                    reused = compose_frame(frame, settings, mode, boxes=boxes,
                                           highlight_indices=[], analysis=actual)
                np.testing.assert_array_equal(reused, reference)


if __name__ == "__main__":
    unittest.main()
