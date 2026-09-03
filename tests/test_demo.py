import unittest

import json
import sys
from unittest import mock

from tools import send_demo_telemetry
from tools import send_spout_demo_telemetry


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
        argv = ["send_demo_telemetry.py", "--legacy", "--interval-sec", "0", "--total-trials", "1", "--session-id", "demo_test"]
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

    def test_partial_reversal_demo_maps_current_reward_by_phase(self):
        class FakeSocket(object):
            def __init__(self):
                self.packets = []

            def sendto(self, payload, _address):
                self.packets.append(json.loads(payload.decode("utf-8")))

            def close(self):
                pass

        for contingency_phase in ("acquisition", "reversal"):
            fake_socket = FakeSocket()
            argv = ["send_demo_telemetry.py", "--interval-sec", "0", "--total-trials", "14",
                    "--session-id", "partial_test", "--contingency-phase", contingency_phase]
            with mock.patch.object(send_demo_telemetry.socket, "socket", return_value=fake_socket), \
                    mock.patch.object(send_demo_telemetry.time, "sleep"), \
                    mock.patch.object(sys, "argv", argv):
                send_demo_telemetry.main()
            completed = [packet for packet in fake_socket.packets if packet["type"] == "trial_complete"]
            self.assertEqual(completed[0]["protocol_version"], send_demo_telemetry.PROTOCOL_VERSION)
            self.assertEqual(completed[0]["contingency_phase"], contingency_phase)
            for packet in completed[:9]:
                trajectory = packet["reward_trajectory"]
                expected = trajectory in (("R_to_R", "R_to_U") if contingency_phase == "acquisition" else ("R_to_R", "U_to_R"))
                self.assertEqual(packet["reward_eligible"], expected)
            omissions = [packet for packet in completed if packet["reward_omission"]]
            self.assertTrue(omissions)
            self.assertTrue(all(packet["reward_eligible"] for packet in omissions))

    def test_spout_demo_has_manual_bait_and_finite_scheduled_flow(self):
        class FakeSocket(object):
            def __init__(self):
                self.packets = []

            def sendto(self, payload, _address):
                self.packets.append(json.loads(payload.decode("utf-8")))

            def close(self):
                pass

        fake_socket = FakeSocket()
        argv = ["send_spout_demo_telemetry.py", "--interval-sec", "0", "--session-id", "spout_test"]
        with mock.patch.object(send_spout_demo_telemetry.socket, "socket", return_value=fake_socket), \
                mock.patch.object(send_spout_demo_telemetry.time, "sleep"), \
                mock.patch.object(sys, "argv", argv):
            send_spout_demo_telemetry.main()
        self.assertEqual(fake_socket.packets[0]["protocol"], "spout_training")
        self.assertIn("MANUAL_BAIT", [packet.get("phase") for packet in fake_socket.packets])
        completed = [packet for packet in fake_socket.packets if packet["type"] == "trial_complete"]
        self.assertEqual(len(completed), 5)
        scheduled = [packet for packet in fake_socket.packets if packet.get("phase") in ("WAITING_REWARD", "REWARD")]
        self.assertTrue(scheduled)
        for packet in scheduled:
            self.assertFalse(packet["manual_bait_active"])
            self.assertFalse(packet["manual_start_requested"])
            self.assertFalse(packet["manual_abort_requested"])
            self.assertFalse(packet["bait_reward_ready"])
        final = fake_socket.packets[-1]
        self.assertEqual(final["phase"], "COMPLETE")
        self.assertEqual(final["total_water_ul_session"], 40.0)
        self.assertFalse(final["manual_bait_active"])
        self.assertFalse(final["manual_start_requested"])
        self.assertFalse(final["manual_abort_requested"])
        self.assertFalse(final["bait_reward_ready"])


if __name__ == "__main__":
    unittest.main()
