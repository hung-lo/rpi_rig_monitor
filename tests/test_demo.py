import unittest

import json
import sys
from unittest import mock

from tools import send_demo_telemetry


class DemoTelemetryTests(unittest.TestCase):
    def test_finite_demo_patterns_include_zero_and_nonzero_lick_trials(self):
        patterns = [send_demo_telemetry.demo_lick_times(trial) for trial in range(1, 21)]
        self.assertIn([], patterns)
        self.assertIn([0.21, 0.48], patterns)
        self.assertIn([-0.12, 1.27], patterns)
        for lick_times in patterns:
            self.assertEqual(send_demo_telemetry.is_anticipatory(lick_times), lick_times == [0.21, 0.48])

    def test_anticipatory_window_is_half_open(self):
        self.assertFalse(send_demo_telemetry.is_anticipatory([1.0]))
        self.assertTrue(send_demo_telemetry.is_anticipatory([0.999999]))

    def test_iti_repeats_post_trial_cumulative_fields(self):
        class FakeSocket(object):
            def __init__(self):
                self.packets = []

            def sendto(self, payload, _address):
                self.packets.append(json.loads(payload.decode("utf-8")))

            def close(self):
                pass

        fake_socket = FakeSocket()
        argv = ["send_demo_telemetry.py", "--interval-sec", "0", "--total-trials", "1", "--session-id", "demo_test"]
        with mock.patch.object(send_demo_telemetry.socket, "socket", return_value=fake_socket), \
                mock.patch.object(send_demo_telemetry.time, "sleep"), \
                mock.patch.object(sys, "argv", argv):
            send_demo_telemetry.main()
        completed = next(packet for packet in fake_socket.packets if packet["type"] == "trial_complete")
        iti = next(packet for packet in fake_socket.packets if packet["type"] == "state" and packet["phase"] == "ITI")
        self.assertEqual(completed["task_reward_trains_verified_session"], 1)
        self.assertEqual(completed["task_reward_trains_contacted_session"], 1)
        self.assertEqual(completed["task_rewarded_high_cue_trials_completed_session"], 1)
        self.assertEqual(iti["task_reward_trains_verified_session"], completed["task_reward_trains_verified_session"])
        self.assertEqual(iti["task_reward_trains_contacted_session"], completed["task_reward_trains_contacted_session"])
        self.assertEqual(iti["task_rewarded_high_cue_trials_completed_session"], completed["task_rewarded_high_cue_trials_completed_session"])


if __name__ == "__main__":
    unittest.main()
