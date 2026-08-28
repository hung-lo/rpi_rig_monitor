import unittest

from tools.send_demo_telemetry import demo_lick_times


class DemoTelemetryTests(unittest.TestCase):
    def test_finite_demo_patterns_include_zero_and_nonzero_lick_trials(self):
        patterns = [demo_lick_times(trial) for trial in range(1, 21)]
        self.assertIn([], patterns)
        self.assertIn([0.21, 0.48], patterns)
        self.assertIn([-0.12, 1.27], patterns)
        for lick_times in patterns:
            self.assertEqual(any(0.0 <= value <= 1.0 for value in lick_times), lick_times == [0.21, 0.48])


if __name__ == "__main__":
    unittest.main()
