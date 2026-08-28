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
        self._healthy = False
        self._failed = False

    def bind(self) -> None:
        """Bind synchronously so startup cannot advertise a dead receiver."""
        if self._socket is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(0.5)
            sock.bind((self.host, self.port))
        except OSError:
            sock.close()
            self._failed = True
            raise
        self._socket = sock
        self._healthy = True

    def health_status(self) -> str:
        if self._healthy:
            return "ok"
        if self._failed:
            return "failed"
        return "starting"

    def serve_forever(self) -> None:
        self.bind()
        sock = self._socket
        try:
            while not self._stop_event.is_set():
                try:
                    payload, _address = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                packet = decode_packet(payload)
                if packet is not None:
                    self.state.update(packet)
        except OSError:
            if not self._stop_event.is_set():
                self._failed = True
                LOGGER.exception("UDP receiver stopped unexpectedly")
        finally:
            if sock is not None:
                sock.close()
            self._socket = None
            self._healthy = False

    def stop(self) -> None:
        self._stop_event.set()
