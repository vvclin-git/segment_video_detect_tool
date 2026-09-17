import unittest

from app import calculate_stable_states, find_first_sustained_stable_start


class StabilityEventTests(unittest.TestCase):
    def test_sustained_start_precedes_confirmation(self) -> None:
        detections = [0, 0, 1, 1, 1, 1]
        rates, states, _misses = calculate_stable_states(
            detections, window=3, on_count=2, off_count=0, confirm_frames=3
        )

        self.assertEqual(find_first_sustained_stable_start(
            detections, window=3, on_count=2, off_count=0, confirm_frames=3
        ), 3)
        self.assertEqual(states, [0, 0, 0, 0, 0, 1])
        self.assertEqual(rates[3:], [2 / 3, 1.0, 1.0])

    def test_unconfirmed_candidate_is_not_reported(self) -> None:
        detections = [0, 0, 1, 1, 1, 0, 1, 1]

        self.assertIsNone(find_first_sustained_stable_start(
            detections, window=3, on_count=3, off_count=0, confirm_frames=2
        ))


if __name__ == "__main__":
    unittest.main()
