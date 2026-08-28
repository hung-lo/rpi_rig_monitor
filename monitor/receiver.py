"""UDP JSON telemetry receiver."""

import json
import logging
import socket
import threading
from typing import Any, Dict, Optional

from .state import MonitorState

LOGGER = logging.getLogger(__name__)


def decode_packet(payload: bytes) -> Optional[Dict[str, Any]]:
    """Decode a UTF-8 JSON object, returning None for malformed input."""
    try:
        packet = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        return None
    if not isinstance(packet, dict) or not isinstance(packet.get("type"), str):
        return None
    return packet


class UDPReceiver(object):
    def __init__(self, state: MonitorState, host: str = "0.0.0.0", port: int = 5055) -> None:
        self.state = state
        self.host = host
        self.port = port
        self._stop_event = threading.Event()
        self._socket: Optional[socket.socket] = None

    def serve_forever(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket = sock
        sock.settimeout(0.5)
        sock.bind((self.host, self.port))
        try:
            while not self._stop_event.is_set():
                try:
                    payload, _address = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                packet = decode_packet(payload)
                if packet is not None:
                    self.state.update(packet)
        finally:
            sock.close()
            self._socket = None

    def stop(self) -> None:
        self._stop_event.set()
