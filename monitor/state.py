"""Thread-safe in-memory telemetry state."""

import copy
import time
from collections import deque
from threading import RLock
from typing import Any, Deque, Dict, Optional

COMMON_SESSION_COUNTER_DEFAULTS = {
    "reward_volume_ul_per_train": None,
    "task_reward_trains_verified_session": 0,
    "task_reward_trains_contacted_session": 0,
    "task_water_delivered_ul_session": None,
    "task_water_likely_consumed_ul_session": None,
}

NEW_REWARD_PROTOCOL_COUNTER_DEFAULTS = {
    "task_r_plus_cue_trials_completed_session": 0,
    "task_r_plus_cue_anticipatory_lick_trials_session": 0,
    "task_r_minus_cue_trials_completed_session": 0,
    "task_r_minus_cue_anticipatory_lick_trials_session": 0,
    "task_r_plus_omission_trials_completed_session": 0,
    "task_r_plus_omission_anticipatory_lick_trials_session": 0,
    "task_high_r_plus_cue_trials_completed_session": 0,
    "task_high_r_plus_cue_anticipatory_lick_trials_session": 0,
    "task_high_r_minus_cue_trials_completed_session": 0,
    "task_high_r_minus_cue_anticipatory_lick_trials_session": 0,
    "task_medium_r_plus_cue_trials_completed_session": 0,
    "task_medium_r_plus_cue_anticipatory_lick_trials_session": 0,
    "task_medium_r_minus_cue_trials_completed_session": 0,
    "task_medium_r_minus_cue_anticipatory_lick_trials_session": 0,
    "task_low_r_minus_cue_trials_completed_session": 0,
    "task_low_r_minus_cue_anticipatory_lick_trials_session": 0,
}

LEGACY_REWARD_PROTOCOL_COUNTER_DEFAULTS = {
    "task_rewarded_high_cue_trials_completed_session": 0,
    "task_rewarded_high_cue_anticipatory_lick_trials_session": 0,
    "task_unrewarded_high_cue_trials_completed_session": 0,
    "task_unrewarded_high_cue_anticipatory_lick_trials_session": 0,
    "task_low_probability_cue_trials_completed_session": 0,
    "task_low_probability_cue_anticipatory_lick_trials_session": 0,
}

SESSION_COUNTER_DEFAULTS = {}
SESSION_COUNTER_DEFAULTS.update(COMMON_SESSION_COUNTER_DEFAULTS)
SESSION_COUNTER_DEFAULTS.update(NEW_REWARD_PROTOCOL_COUNTER_DEFAULTS)
SESSION_COUNTER_DEFAULTS.update(LEGACY_REWARD_PROTOCOL_COUNTER_DEFAULTS)

SESSION_STATE_DEFAULTS = {
    "contingency_phase": None,
    "protocol_version": None,
    "new_reward_protocol_behavior_available": False,
}

HARDWARE_DEFAULTS = {
    "display": None,
    "camera_1": None,
    "camera_2": None,
    "storage": None,
    "gpio": None,
    "irig": None,
    "chrony": None,
    "audio": None,
}

INTEGRITY_DEFAULTS = {
    "event_drops": None,
    "writer_errors": None,
    "kms_completed": None,
    "kms_long_intervals": None,
    "camera_errors": None,
}

SPOUT_SESSION_DEFAULTS = {
    "maximum_training_rewards": None,
    "training_reward_index": None,
    "attempted_training_reward_count": 0,
    "completed_training_reward_count": 0,
    "retrieval_success_count_session": 0,
    "retrieval_failure_count_session": 0,
    "recent_20_success_count": None,
    "recent_20_success_fraction": None,
    "criterion_evaluable": False,
    "training_passed": False,
    "training_pass_reward_index": None,
    "criterion_window_rewards": None,
    "criterion_success_fraction": None,
    "reward_volume_ul": None,
    "task_water_delivered_ul_session": None,
    "bait_water_ul_session": None,
    "total_water_ul_session": None,
    "total_lick_onset_count_session": 0,
    "bait_mode": None,
    "manual_bait_active": False,
    "bait_index": None,
    "bait_total": None,
    "completed_bait_reward_count": 0,
    "completed_bait_suction_count": 0,
    "bait_contacted_count_session": 0,
    "last_bait_contacted": None,
    "last_bait_first_lick_latency_sec": None,
    "recent_lick_count_2s": 0,
    "last_lick_age_sec": None,
    "licking_active": False,
    "bait_reward_ready": False,
    "manual_start_requested": False,
    "manual_abort_requested": False,
    "manual_bait_max_water_ul": None,
    "next_reward_in_sec": None,
    "planned_remaining_eta_sec": None,
}


class MonitorState(object):
    """Holds the latest telemetry and a bounded trial history."""

    def __init__(self, stale_after_sec: float = 5.0, history_limit: int = 50) -> None:
        self.stale_after_sec = stale_after_sec
        self.history_limit = history_limit
        self._lock = RLock()
        self._last_received: Optional[float] = None
        self._data: Dict[str, Any] = {
            "protocol": None,
            "session_id": None,
            "phase": None,
            "trial": None,
            "total_trials": None,
            "block": None,
            "total_blocks": None,
            "image": None,
            "stimulus_role": None,
            "eta_sec": None,
            "last_trial": None,
            "recent_trials": [],
        }
        self._data.update(copy.deepcopy(SESSION_STATE_DEFAULTS))
        self._data.update(copy.deepcopy(SESSION_COUNTER_DEFAULTS))
        self._data.update(copy.deepcopy(SPOUT_SESSION_DEFAULTS))
        self._recent: Deque[Dict[str, Any]] = deque(maxlen=history_limit)
        self._recent_events: Deque[Dict[str, Any]] = deque(maxlen=history_limit)
        self._generic = {
            "session": {"id": None, "protocol": None, "state": "IDLE",
                        "elapsed_s": None, "eta_s": None},
            "hardware": copy.deepcopy(HARDWARE_DEFAULTS),
            "integrity": copy.deepcopy(INTEGRITY_DEFAULTS),
            "protocol": {},
        }

    def update(self, packet: Dict[str, Any], received_at: Optional[float] = None) -> bool:
        """Apply a recognized telemetry packet. Return False for invalid packets."""
        if not isinstance(packet, dict) or not isinstance(packet.get("type"), str):
            return False
        message_type = packet["type"]
        if message_type not in ("state", "trial_complete", "session", "event"):
            return False

        with self._lock:
            generic_session = packet.get("session")
            session_id = packet.get("session_id")
            if session_id is None and isinstance(generic_session, dict):
                session_id = generic_session.get("id")
            if (session_id is not None and self._data["session_id"] is not None
                    and session_id != self._data["session_id"]):
                self._recent.clear()
                self._recent_events.clear()
                for key in ("protocol", "phase", "trial", "total_trials", "block", "total_blocks",
                            "image", "stimulus_role", "eta_sec", "last_trial", "recent_trials"):
                    self._data[key] = [] if key == "recent_trials" else None
                self._data.update(copy.deepcopy(SESSION_STATE_DEFAULTS))
                self._data.update(copy.deepcopy(SESSION_COUNTER_DEFAULTS))
                self._data.update(copy.deepcopy(SPOUT_SESSION_DEFAULTS))
                self._generic["session"] = {
                    "id": session_id, "protocol": None, "state": "IDLE",
                    "elapsed_s": None, "eta_s": None,
                }
                self._generic["integrity"] = copy.deepcopy(INTEGRITY_DEFAULTS)
                self._generic["protocol"] = {}

            if isinstance(generic_session, dict):
                self._generic["session"].update(copy.deepcopy(generic_session))
            if session_id is not None:
                self._data["session_id"] = session_id
                self._generic["session"]["id"] = session_id
            protocol_name = packet.get("protocol")
            if not isinstance(protocol_name, str) and isinstance(generic_session, dict):
                protocol_name = generic_session.get("protocol")
            if isinstance(protocol_name, str):
                self._data["protocol"] = protocol_name
                self._generic["session"]["protocol"] = protocol_name
            lifecycle = packet.get("lifecycle", packet.get("state"))
            if lifecycle is None and isinstance(generic_session, dict):
                lifecycle = generic_session.get("state")
            if isinstance(lifecycle, str):
                self._generic["session"]["state"] = lifecycle.upper()
            elapsed = packet.get("elapsed_s")
            if elapsed is None and isinstance(generic_session, dict):
                elapsed = generic_session.get("elapsed_s")
            if elapsed is not None:
                self._generic["session"]["elapsed_s"] = copy.deepcopy(elapsed)
            eta = packet.get("eta_s", packet.get("eta_sec"))
            if eta is None and isinstance(generic_session, dict):
                eta = generic_session.get("eta_s")
            if (eta is not None or "eta_s" in packet or "eta_sec" in packet or
                    (isinstance(generic_session, dict) and "eta_s" in generic_session)):
                self._generic["session"]["eta_s"] = copy.deepcopy(eta)
            for name in ("hardware", "integrity"):
                values = packet.get(name)
                if isinstance(values, dict):
                    self._generic[name].update(copy.deepcopy(values))
            if isinstance(packet.get("counters"), dict):
                self._generic["integrity"].update(copy.deepcopy(packet["counters"]))
            if isinstance(packet.get("cameras"), list):
                self._generic["hardware"]["cameras"] = copy.deepcopy(packet["cameras"])
            elif isinstance(packet.get("camera_states"), list):
                self._generic["hardware"]["cameras"] = copy.deepcopy(packet["camera_states"])
            protocol_state = packet.get("protocol_state", packet.get("protocol_data"))
            if isinstance(protocol_state, dict):
                self._generic["protocol"].update(copy.deepcopy(protocol_state))
            envelope = {"type", "schema_version", "telemetry_schema_version", "protocol",
                        "session_id", "session", "hardware", "integrity", "protocol_state",
                        "protocol_data", "lifecycle", "state", "elapsed_s", "eta_s", "eta_sec"}
            self._generic["protocol"].update({
                key: copy.deepcopy(value) for key, value in packet.items() if key not in envelope
            })
            if message_type in ("event", "trial_complete"):
                self._recent_events.appendleft(copy.deepcopy(packet))

            for key in ("protocol", "protocol_version", "session_id", "phase", "contingency_phase",
                        "trial", "total_trials", "block", "total_blocks", "image",
                        "stimulus_role", "eta_sec"):
                if key in packet:
                    self._data[key] = copy.deepcopy(packet[key])
            if any(key in packet for key in NEW_REWARD_PROTOCOL_COUNTER_DEFAULTS):
                self._data["new_reward_protocol_behavior_available"] = True
            for key in SESSION_COUNTER_DEFAULTS:
                if key in packet:
                    self._data[key] = copy.deepcopy(packet[key])
            for key in SPOUT_SESSION_DEFAULTS:
                if key in packet:
                    self._data[key] = copy.deepcopy(packet[key])

            if message_type == "trial_complete":
                trial = copy.deepcopy(packet)
                self._recent.appendleft(trial)
                self._data["last_trial"] = trial
                self._data["recent_trials"] = list(self._recent)

            self._last_received = time.monotonic() if received_at is None else received_at
        return True

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        with self._lock:
            current_time = time.monotonic() if now is None else now
            age = None if self._last_received is None else max(0.0, current_time - self._last_received)
            connected = age is not None and age <= self.stale_after_sec
            result = copy.deepcopy(self._data)
            result["rig"] = copy.deepcopy(self._generic)
            result["recent_events"] = list(self._recent_events)
            result.update({
                "connected": connected,
                "status": "LIVE" if connected else "STALE",
                "last_packet_age_sec": age,
            })
            return result
