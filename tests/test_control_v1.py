import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor.fake_task import _handle
from monitor.supervisor import SupervisorError, SupervisorServer


class ControlV1Tests(unittest.TestCase):
    def test_fake_task_requires_and_echoes_v1_envelope(self):
        state = {"session_id": "s1", "protocol": "fake", "state": "READY",
                 "elapsed_s": 0.0, "eta_s": None, "capabilities": {"start": True},
                 "_ack_cache": {}, "_ack_order": []}
        reply = _handle({"schema_version": 1, "request_id": "status-1", "command": "status"}, state)
        self.assertEqual(reply["schema_version"], 1)
        self.assertEqual(reply["request_id"], "status-1")
        self.assertEqual(reply["status"]["state"], "READY")

        for request in (
            {"schema_version": 2, "request_id": "bad-version", "command": "status"},
            {"request_id": "missing-version", "command": "status"},
            {"schema_version": 1, "command": "status"},
        ):
            with self.subTest(request=request):
                rejected = _handle(request, state)
                self.assertFalse(rejected["ok"])
                self.assertEqual(rejected["schema_version"], 1)
                self.assertEqual(rejected["request_id"], request.get("request_id"))

    def test_engine_request_has_v1_fields_and_bad_replies_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            server = SupervisorServer(directory, Path(directory) / "supervisor.sock")
            server.record["control_endpoint"] = Path(directory) / "task.sock"
            invalid_responses = (
                ({"request_id": "response-id", "ok": True}, "schema version"),
                ({"schema_version": 2, "request_id": "response-id", "ok": True}, "schema version"),
                ({"schema_version": 1, "ok": True}, "request_id mismatch"),
                ({"schema_version": 1, "request_id": "other-id", "ok": True}, "request_id mismatch"),
            )
            for response, message in invalid_responses:
                with self.subTest(response=response):
                    with patch("monitor.supervisor._rpc", return_value=response) as rpc, \
                            self.assertRaisesRegex(SupervisorError, message):
                        server._task_request({"command": "status"})
                    request = rpc.call_args[0][1]
                    self.assertEqual(request["schema_version"], 1)
                    self.assertIsInstance(request["request_id"], str)

            with patch("monitor.supervisor._rpc", return_value={
                "schema_version": 1, "request_id": "mutating-1", "ok": True,
            }) as rpc:
                server._task_request({"command": "start", "request_id": "mutating-1"})
            self.assertEqual(rpc.call_args[0][1], {
                "schema_version": 1, "command": "start", "request_id": "mutating-1",
            })


if __name__ == "__main__":
    unittest.main()
