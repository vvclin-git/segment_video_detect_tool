"""Regression tests for backends that report a successful but inaccurate seek."""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from video_reader import ExactVideoCapture


class MisleadingCapture:
    def __init__(self, path):
        self.position = 0
        self.opened = True

    def isOpened(self):
        return self.opened

    def set(self, prop, value):
        raise AssertionError("Must not seek the backend, even to frame zero")

    def get(self, prop):
        return 999  # Neither position nor estimated count is trustworthy.

    def grab(self):
        if not self.opened or self.position >= 40:
            return False
        self.position += 1
        return True

    def read(self):
        if not self.grab():
            return False, None
        return True, np.full((2, 2, 3), self.position - 1, np.uint8)

    def release(self):
        self.opened = False


class ExactReaderTests(unittest.TestCase):
    @patch("video_reader.cv2.VideoCapture", MisleadingCapture)
    def test_forward_backward_cache_miss_and_end_of_stream(self):
        cap = ExactVideoCapture(__file__)
        try:
            self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT), 40)
            # Exercise skipped frames, cached backwards access and reopening.
            for index in [25, 26, 25, 27, 0, *range(1, 22), 0, 39]:
                self.assertTrue(cap.set(cv2.CAP_PROP_POS_FRAMES, index))
                ok, frame = cap.read()
                self.assertTrue(ok)
                self.assertTrue(np.all(frame == index))
                self.assertEqual(cap.get(cv2.CAP_PROP_POS_FRAMES), index + 1)
                frame[:] = 255  # Callers cannot corrupt cached images.
            self.assertFalse(cap.read()[0])
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self.assertTrue(np.all(cap.read()[1] == 0))
        finally:
            cap.release()
        self.assertFalse(cap.read()[0])

    @patch("video_reader.cv2.VideoCapture", MisleadingCapture)
    def test_invalid_seek_leaves_position_unchanged(self):
        cap = ExactVideoCapture(__file__)
        try:
            for index in [-1, 1.5, float("nan"), float("inf")]:
                self.assertFalse(cap.set(cv2.CAP_PROP_POS_FRAMES, index))
            self.assertEqual(cap.get(cv2.CAP_PROP_POS_FRAMES), 0)
        finally:
            cap.release()


if __name__ == "__main__":
    unittest.main()
