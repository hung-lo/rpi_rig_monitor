"""Thread-safe in-memory telemetry state."""

import copy
import time
from collections import deque
from threading import RLock
from typing import Any, Deque, Dict, Optional


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
        self._recent: Deque[Dict[str, Any]] = deque(maxlen=history_limit)

    def update(self, packet: Dict[str, Any], received_at: Optional[float] = None) -> bool:
        """Apply a recognized telemetry packet. Return False for invalid packets."""
        if not isinstance(packet, dict) or not isinstance(packet.get("type"), str):
            return False
        message_type = packet["type"]
        if message_type not in ("state", "trial_complete", "session"):
            return False

        with self._lock:
            session_id = packet.get("session_id")
            if (session_id is not None and self._data["session_id"] is not None
                    and session_id != self._data["session_id"]):
                self._recent.clear()
                for key in ("phase", "trial", "total_trials", "block", "total_blocks",
                            "image", "stimulus_role", "eta_sec", "last_trial", "recent_trials"):
                    self._data[key] = [] if key == "recent_trials" else None

            for key in ("protocol", "session_id", "phase", "trial", "total_trials",
                        "block", "total_blocks", "image", "stimulus_role", "eta_sec"):
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
            result.update({
                "connected": connected,
                "status": "LIVE" if connected else "STALE",
                "last_packet_age_sec": age,
            })
            return result
