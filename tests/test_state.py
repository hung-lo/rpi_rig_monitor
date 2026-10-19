import unittest

from monitor.state import (
    NEW_REWARD_PROTOCOL_COUNTER_DEFAULTS,
    SESSION_COUNTER_DEFAULTS,
    SESSION_STATE_DEFAULTS,
    SPOUT_SESSION_DEFAULTS,
    MonitorState,
)


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

    def test_generic_state_keeps_arbitrary_protocol_and_health_fields(self):
        state = MonitorState()
        packet = {
            "type": "state", "session": {"id": "generic1", "protocol": "retinotopy",
                                            "state": "RUNNING", "elapsed_s": 12.5, "eta_s": 30},
            "hardware": {"display": "ok", "camera_1": "recording", "storage": "ok"},
            "integrity": {"writer_errors": 2, "custom_counter": 9},
            "protocol_state": {"orientation_deg": 45, "arbitrary": {"level": 2}},
        }
        self.assertTrue(state.update(packet, received_at=2.0))
        rig = state.snapshot(now=2.0)["rig"]
        self.assertEqual(rig["session"], {"id": "generic1", "protocol": "retinotopy", "state": "RUNNING",
                                           "elapsed_s": 12.5, "eta_s": 30})
        self.assertEqual(rig["hardware"]["camera_1"], "recording")
        self.assertEqual(rig["integrity"]["writer_errors"], 2)
        self.assertEqual(rig["integrity"]["custom_counter"], 9)
        self.assertEqual(rig["protocol"]["arbitrary"], {"level": 2})

    def test_events_are_bounded_and_new_session_clears_protocol_history(self):
        state = MonitorState(history_limit=2)
        state.update({"type": "state", "session_id": "a", "protocol": "natural_images",
                      "protocol_state": {"image": "old.png"}}, received_at=1.0)
        for index in range(3):
            state.update({"type": "event", "session_id": "a", "event": f"event-{index}"},
                         received_at=2.0 + index)
        snapshot = state.snapshot(now=4.0)
        self.assertEqual([event["event"] for event in snapshot["recent_events"]], ["event-2", "event-1"])
        state.update({"type": "state", "session_id": "b", "protocol": "drifting_gratings"}, received_at=5.0)
        snapshot = state.snapshot(now=5.0)
        self.assertEqual(snapshot["recent_events"], [])
        self.assertEqual(snapshot["rig"]["protocol"], {})
        self.assertIsNone(snapshot["rig"]["integrity"]["writer_errors"])
        self.assertEqual(snapshot["rig"]["session"]["id"], "b")

    def test_new_generic_session_clears_previous_protocol_name(self):
        state = MonitorState()
        state.update({"type": "state", "session_id": "old", "protocol": "natural_images"}, received_at=1.0)
        state.update({"type": "session", "session": {"id": "new"}}, received_at=2.0)
        snapshot = state.snapshot(now=2.0)
        self.assertIsNone(snapshot["protocol"])
        self.assertIsNone(snapshot["rig"]["session"]["protocol"])

    def test_cumulative_fields_are_retained_without_local_reconstruction(self):
        state = MonitorState()
        values = dict(SESSION_COUNTER_DEFAULTS)
        values.update({"task_reward_trains_verified_session": 18,
                       "task_reward_trains_contacted_session": 15,
                       "task_water_delivered_ul_session": 54.0,
                       "task_water_likely_consumed_ul_session": 45.0,
                       "reward_volume_ul_per_train": 3.0})
        packet = {"type": "trial_complete", "session_id": "s1"}
        packet.update(values)
        state.update(packet, received_at=1.0)
        snapshot = state.snapshot(now=1.0)
        for key, value in values.items():
            self.assertEqual(snapshot[key], value)

    def test_new_session_resets_cumulative_fields_before_partial_update(self):
        state = MonitorState()
        state.update({"type": "state", "session_id": "old",
                      "task_reward_trains_verified_session": 18,
                      "task_reward_trains_contacted_session": 15,
                      "task_water_delivered_ul_session": 54.0,
                      "task_water_likely_consumed_ul_session": 45.0,
                      "reward_volume_ul_per_train": 3.0,
                      "task_rewarded_high_cue_trials_completed_session": 20,
                      "task_rewarded_high_cue_anticipatory_lick_trials_session": 17}, received_at=1.0)
        state.update({"type": "session", "session_id": "new"}, received_at=2.0)
        snapshot = state.snapshot(now=2.0)
        for key, value in SESSION_COUNTER_DEFAULTS.items():
            self.assertEqual(snapshot[key], value)

    def test_spout_fields_are_retained(self):
        state = MonitorState()
        values = {
            "maximum_training_rewards": 60, "training_reward_index": 17,
            "completed_bait_reward_count": 19, "bait_water_ul_session": 95.0,
            "recent_lick_count_2s": 1, "last_lick_age_sec": 0.12,
            "licking_active": True, "retrieval_success_count_session": 13,
            "completed_training_reward_count": 17, "recent_20_success_count": 16,
            "criterion_evaluable": True, "training_passed": True,
            "training_pass_reward_index": 23, "task_water_delivered_ul_session": 54.0,
            "total_water_ul_session": 149.0,
        }
        packet = {"type": "state", "protocol": "spout_training", "session_id": "spout1", "phase": "MANUAL_BAIT"}
        packet.update(values)
        state.update(packet, received_at=1.0)
        snapshot = state.snapshot(now=1.0)
        for key, value in values.items():
            self.assertEqual(snapshot[key], value)

    def test_partial_reversal_fields_are_retained(self):
        state = MonitorState()
        values = {
            "contingency_phase": "reversal",
            "protocol_version": "exposure_reward_partial_reversal_v1",
            "task_r_plus_cue_trials_completed_session": 220,
            "task_r_plus_cue_anticipatory_lick_trials_session": 184,
            "task_r_minus_cue_trials_completed_session": 280,
            "task_r_minus_cue_anticipatory_lick_trials_session": 32,
            "task_r_plus_omission_trials_completed_session": 22,
            "task_r_plus_omission_anticipatory_lick_trials_session": 19,
            "task_high_r_plus_cue_trials_completed_session": 160,
            "task_high_r_plus_cue_anticipatory_lick_trials_session": 141,
            "task_high_r_minus_cue_trials_completed_session": 160,
            "task_high_r_minus_cue_anticipatory_lick_trials_session": 16,
            "task_medium_r_plus_cue_trials_completed_session": 60,
            "task_medium_r_plus_cue_anticipatory_lick_trials_session": 47,
            "task_medium_r_minus_cue_trials_completed_session": 60,
            "task_medium_r_minus_cue_anticipatory_lick_trials_session": 8,
            "task_low_r_minus_cue_trials_completed_session": 60,
            "task_low_r_minus_cue_anticipatory_lick_trials_session": 5,
        }
        packet = {"type": "state", "protocol": "reward_conditioning", "session_id": "new-protocol"}
        packet.update(values)
        state.update(packet, received_at=1.0)
        snapshot = state.snapshot(now=1.0)
        for key, value in values.items():
            self.assertEqual(snapshot[key], value)
        self.assertTrue(snapshot["new_reward_protocol_behavior_available"])

    def test_missing_partial_reversal_counters_are_not_reconstructed(self):
        state = MonitorState()
        state.update({"type": "session", "protocol": "reward_conditioning", "session_id": "no-reconstruction"}, received_at=1.0)
        for trial in range(1, 4):
            state.update({"type": "trial_complete", "session_id": "no-reconstruction", "trial": trial,
                          "reward_eligible": trial != 2, "anticipatory_lick": True}, received_at=float(trial))
        snapshot = state.snapshot(now=3.0)
        for key in NEW_REWARD_PROTOCOL_COUNTER_DEFAULTS:
            self.assertEqual(snapshot[key], 0)
        self.assertFalse(snapshot["new_reward_protocol_behavior_available"])

    def test_new_session_resets_partial_reversal_fields(self):
        state = MonitorState()
        state.update({"type": "state", "protocol": "reward_conditioning", "session_id": "old",
                      "contingency_phase": "reversal", "protocol_version": "exposure_reward_partial_reversal_v1",
                      "task_r_plus_cue_trials_completed_session": 20,
                      "task_r_plus_omission_trials_completed_session": 2,
                      "task_high_r_plus_cue_trials_completed_session": 10}, received_at=1.0)
        state.update({"type": "session", "session_id": "new"}, received_at=2.0)
        snapshot = state.snapshot(now=2.0)
        for key, value in SESSION_COUNTER_DEFAULTS.items():
            self.assertEqual(snapshot[key], value)
        for key, value in SESSION_STATE_DEFAULTS.items():
            self.assertEqual(snapshot[key], value)

    def test_new_session_resets_spout_fields(self):
        state = MonitorState()
        state.update({"type": "state", "protocol": "spout_training", "session_id": "old",
                      "completed_bait_reward_count": 19, "recent_lick_count_2s": 4,
                      "training_passed": True, "total_water_ul_session": 149.0}, received_at=1.0)
        state.update({"type": "session", "protocol": "reward_conditioning", "session_id": "new"}, received_at=2.0)
        snapshot = state.snapshot(now=2.0)
        for key, value in SPOUT_SESSION_DEFAULTS.items():
            self.assertEqual(snapshot[key], value)
        self.assertEqual(snapshot["protocol"], "reward_conditioning")


if __name__ == "__main__":
    unittest.main()
