import unittest

from monitor.state import MonitorState


class MonitorStateTests(unittest.TestCase):
    def test_state_update(self):
        state = MonitorState()
        state.update({"type": "state", "protocol": "reward_conditioning", "session_id": "s1", "trial": 4, "phase": "TASK", "image": "cue.png"}, received_at=100.0)
        snapshot = state.snapshot(now=101.0)
        self.assertEqual(snapshot["protocol"], "reward_conditioning")
        self.assertEqual(snapshot["session_id"], "s1")
        self.assertEqual(snapshot["trial"], 4)
        self.assertEqual(snapshot["phase"], "TASK")
        self.assertEqual(snapshot["image"], "cue.png")
        self.assertTrue(snapshot["connected"])

    def test_trial_history_is_bounded_and_preserved(self):
        state = MonitorState(history_limit=3)
        for trial in range(5):
            state.update({"type": "trial_complete", "session_id": "s1", "trial": trial, "extra_field": {"safe": True}}, received_at=float(trial))
        snapshot = state.snapshot(now=5.0)
        self.assertEqual(snapshot["last_trial"]["trial"], 4)
        self.assertEqual([item["trial"] for item in snapshot["recent_trials"]], [4, 3, 2])
        self.assertEqual(snapshot["last_trial"]["extra_field"], {"safe": True})

    def test_stale_state_keeps_last_known_values(self):
        state = MonitorState(stale_after_sec=5.0)
        state.update({"type": "state", "session_id": "s1", "trial": 9}, received_at=10.0)
        snapshot = state.snapshot(now=16.0)
        self.assertFalse(snapshot["connected"])
        self.assertEqual(snapshot["status"], "STALE")
        self.assertEqual(snapshot["trial"], 9)

    def test_session_change_clears_history(self):
        state = MonitorState()
        state.update({"type": "state", "session_id": "old", "phase": "TASK", "trial": 8,
                      "total_trials": 20, "block": 2, "total_blocks": 2, "image": "old.png",
                      "stimulus_role": "old_role", "eta_sec": 12}, received_at=1.0)
        state.update({"type": "trial_complete", "session_id": "old", "trial": 8}, received_at=1.0)
        state.update({"type": "state", "session_id": "new", "trial": 1}, received_at=2.0)
        snapshot = state.snapshot(now=2.0)
        self.assertEqual(snapshot["session_id"], "new")
        self.assertIsNone(snapshot["image"])
        self.assertIsNone(snapshot["phase"])
        self.assertIsNone(snapshot["block"])
        self.assertIsNone(snapshot["eta_sec"])
        self.assertIsNone(snapshot["total_trials"])
        self.assertEqual(snapshot["recent_trials"], [])
        self.assertIsNone(snapshot["last_trial"])

    def test_only_recognized_message_types_update(self):
        state = MonitorState()
        self.assertFalse(state.update({"type": "diagnostic", "session_id": "ignored"}))
        self.assertIsNone(state.snapshot()["session_id"])


if __name__ == "__main__":
    unittest.main()
