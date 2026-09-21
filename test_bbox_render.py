import unittest

import numpy as np

from frame_render import (DEFAULT_BBOX_STYLE, BboxStyle, compose_frame,
                          validate_bbox_style)


class BboxRenderStyleTests(unittest.TestCase):
    SETTINGS = dict(lower=[0, 0, 0], upper=[179, 255, 255], min_area=1, roi=None)

    def test_default_style_uses_pillow_annotation_pixels(self):
        frame = np.zeros((300, 400, 3), np.uint8)
        result = compose_frame(
            frame, self.SETTINGS, "Original", boxes=[(100, 100, 80, 60, 4800)],
            selected_indices=[0], include_bbox=True, bbox_style=DEFAULT_BBOX_STYLE,
        )
        # BGR green outline and the fixed dark label background are both
        # visible in original-resolution output.
        self.assertEqual(result[100, 100].tolist(), [0, 255, 0])
        self.assertTrue(np.any(np.all(result == [16, 16, 16], axis=2)))
        self.assertEqual(result.shape, frame.shape)

    def test_style_validation_rejects_invalid_values(self):
        for values in (
            {"color": "green"},
            {"line_width": 0},
            {"font_size": -1},
            {"font_size": "not-a-number"},
        ):
            with self.assertRaises(ValueError):
                validate_bbox_style(values)

    def test_oversized_label_is_an_actionable_error(self):
        frame = np.zeros((40, 50, 3), np.uint8)
        with self.assertRaisesRegex(ValueError, "調小字體"):
            compose_frame(
                frame, self.SETTINGS, "Original", boxes=[(1, 1, 10, 10, 100)],
                selected_indices=[0], include_bbox=True, bbox_style=DEFAULT_BBOX_STYLE,
            )

    def test_plain_original_remains_byte_identical(self):
        frame = np.random.default_rng(4).integers(0, 256, (60, 80, 3), dtype=np.uint8)
        result = compose_frame(frame, self.SETTINGS, "Original", bbox_style=BboxStyle())
        np.testing.assert_array_equal(result, frame)


if __name__ == "__main__":
    unittest.main()
