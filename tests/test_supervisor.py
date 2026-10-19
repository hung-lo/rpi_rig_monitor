import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from monitor.state import MonitorState
from monitor.supervisor import SupervisorClient, SupervisorError
from monitor.web import create_app


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.client = SupervisorClient(self.temporary.name, telemetry_port=0)
        self.client.ensure_running()

    def tearDown(self):
        try:
            try:
                status = self._status()
            except SupervisorError:
                status = {"state": "IDLE"}
            if status["state"] in ("PREPARING", "READY", "RUNNING", "FINISHING"):
                self._control("abort")
            self.client.request({"command": "shutdown-supervisor"})
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    self.client.request({"command": "status"})
                except SupervisorError:
                    break
                time.sleep(0.05)
            if self.client._daemon_process is not None:
                self.client._daemon_process.wait(timeout=2)
        finally:
            self.temporary.cleanup()

    def _response(self, action, protocol=None):
        request = {"command": "control", "action": action, "request_id": uuid.uuid4().hex}
        if protocol is not None:
            request["protocol"] = protocol
        return self.client.request(request)

    def _control(self, action, protocol=None):
        response = self._response(action, protocol)
        self.assertTrue(response.get("ok"), response.get("error"))
        return response.get("result")

    def _status(self):
        return self.client.request({"command": "status"})["status"]

    def _wait_for(self, expected, timeout=4.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._status()
            if status["state"] == expected:
                return status
            time.sleep(0.05)
        self.fail(f"supervisor did not reach {expected}; last state was {status['state']}")

    def test_fake_task_lifecycle_capabilities_control_and_logs(self):
        self.assertFalse(self._response("start")["ok"])
        prepared = self._control("prepare", "fake")
        self.assertEqual(prepared["state"], "READY")
        self.assertTrue(prepared["capabilities"]["start"])
        self.assertTrue(prepared["capabilities"]["bait_left"])
        self.assertFalse(prepared["capabilities"].get("bait_center", False))
        self.assertFalse(self._response("prepare", "fake")["ok"], "duplicate launch must be rejected")
        pid = prepared["pid"]
        self.assertEqual(os.getsid(pid), pid, "experiment must have its own process session")

        bait = self._control("bait_left")
        self.assertTrue(bait["ack"]["ok"])
        self.assertEqual(bait["ack"]["accepted"], "operator_bait")
        repeated = {"command": "control", "action": "bait_left", "request_id": "same-bait-request"}
        first_ack = self.client.request(repeated)
        second_ack = self.client.request(repeated)
        self.assertEqual(first_ack["result"]["ack"], second_ack["result"]["ack"])
        reused = dict(repeated, action="bait_right")
        self.assertFalse(self.client.request(reused)["ok"])
        self.assertFalse(self._response("bait_center")["ok"])
        running = self._control("start")
        self.assertEqual(running["ack"]["state"], "RUNNING")
        self.assertFalse(self._response("bait_left")["ok"], "bait is unavailable after READY")
        self.assertEqual(self._status()["state"], "RUNNING")

        self._control("stop")
        complete = self._wait_for("COMPLETE")
        self.assertEqual(complete["exit_status"], 0)
        self.assertFalse(complete["process_running"])
        self.assertTrue(Path(complete["stdout_path"]).is_file())
        self.assertTrue(Path(complete["stderr_path"]).is_file())
        stdout = Path(complete["stdout_path"]).read_text()
        self.assertEqual(stdout.count("OPERATOR_REWARD channel=left"), 2)
        self.assertIn("graceful stop requested", stdout)

    def test_backend_reconnect_and_telemetry_loss_do_not_stop_task(self):
        prepared = self._control("prepare", "fake")
        pid = prepared["pid"]
        self._control("start")
        old_backend = create_app(MonitorState(), supervisor=self.client).test_client()
        self.assertEqual(old_backend.get("/api/state").get_json()["supervisor"]["state"], "RUNNING")
        restarted_backend = SupervisorClient(self.temporary.name, telemetry_port=0)
        restarted_backend.ensure_running()
        new_backend = create_app(MonitorState(), supervisor=restarted_backend).test_client()
        self.assertEqual(new_backend.get("/api/state").get_json()["supervisor"]["state"], "RUNNING")
        reconnected = restarted_backend.request({"command": "status"})["status"]
        self.assertEqual(reconnected["pid"], pid)
        self.assertEqual(reconnected["state"], "RUNNING")

        telemetry = MonitorState(stale_after_sec=1.0).snapshot(now=100.0)
        self.assertEqual(telemetry["status"], "STALE")
        self.assertEqual(restarted_backend.request({"command": "status"})["status"]["state"], "RUNNING")
        self.assertFalse(restarted_backend.request({"command": "control", "action": "prepare",
                                                    "protocol": "fake"})["ok"])
        response = restarted_backend.request({"command": "control", "action": "abort"})
        self.assertTrue(response["ok"], response.get("error"))
        self.assertEqual(self._wait_for("ABORTED")["state"], "ABORTED")

    def test_task_exit_is_detected_and_invalid_ready_transition_is_rejected(self):
        prepared = self._control("prepare", "fake")
        self.assertFalse(self._response("stop")["ok"])
        result = self._control("start")
        self.assertEqual(result["ack"]["state"], "RUNNING")
        self._control("stop")
        complete = self._wait_for("COMPLETE")
        self.assertIsNotNone(complete["exit_status"])
        self.assertFalse(complete["process_running"])
        self.assertEqual(complete["session_id"], prepared["session_id"])


if __name__ == "__main__":
    unittest.main()
