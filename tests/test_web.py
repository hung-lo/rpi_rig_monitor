import os
import tempfile
import unittest
from pathlib import Path

from monitor.state import MonitorState
from monitor.supervisor import SupervisorClient, SupervisorError
from monitor.web import create_app


class HealthyReceiver:
    def health_status(self):
        return "ok"


class FailedReceiver:
    def health_status(self):
        return "failed"


class FakeSupervisor:
    def __init__(self):
        self.calls = []
        self.status = {"session_id": None, "protocol": None, "state": "IDLE",
                       "capabilities": {}, "recent_events": [], "process_running": False,
                       "control_connected": False, "recovery_state": "IDLE"}

    def request(self, request):
        self.calls.append(request)
        if request["command"] == "status":
            return {"ok": True, "status": self.status}
        if request["command"] == "protocols":
            return {"ok": True, "protocols": [{
                "protocol_id": "fake", "display_name": "Fake task", "description": "Test task",
                "adapter_type": "unix_json_v1", "available": True, "unavailable_reason": None,
                "runtime_fields": [{"name": "subject_id", "display_name": "Subject / Mouse ID",
                                    "type": "subject_id", "required": False}],
                "presets": [{"preset_id": "standard", "display_name": "Standard",
                             "description": "Test preset", "available": True, "unavailable_reason": None}],
            }]}
        if request["command"] == "control":
            if request["action"] == "prepare":
                self.status.update({"session_id": "fake-1", "protocol": request["protocol_id"],
                                    "state": "READY", "capabilities": {"start": True, "abort": True},
                                    "control_connected": True, "recovery_state": "CONNECTED"})
            return {"ok": True, "result": {"ack": {"ok": True}, "status": self.status}}
        return {"ok": False, "error": "unknown"}


class WebTests(unittest.TestCase):
    def test_dashboard_and_json_routes(self):
        client = create_app(MonitorState(), HealthyReceiver()).test_client()
        dashboard = client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        for element_id in ("reward-contact-rate", "rewarded-cue-anticipatory-rate",
                           "unrewarded-cue-anticipatory-rate", "low-prob-cue-anticipatory-rate",
                           "water-delivered", "water-likely-consumed", "reward-volume",
                           "stimulus-preview-empty"):
            self.assertIn(f'id="{element_id}"'.encode(), dashboard.data)
        for element_id in ("spout-reward-progress", "spout-retrieval-rate", "spout-recent20",
                           "spout-criterion", "spout-training-water", "spout-bait-water",
                           "spout-total-water", "spout-total-licks", "spout-bait-drops",
                           "spout-bait-contact", "spout-recent-licks", "spout-last-lick",
                           "spout-lick-status", "last-panel-title", "history-title"):
            self.assertIn(f'id="{element_id}"'.encode(), dashboard.data)
        for element_id in ("contingency-phase", "phase-contingency", "partial-reversal-behavior-view",
                           "new-reward-contact-rate", "r-plus-anticipatory-rate", "r-minus-anticipatory-rate", "r-discrimination",
                           "r-plus-omission-rate", "reversal-readiness", "high-r-plus-rate", "high-r-minus-rate",
                           "medium-r-plus-rate", "medium-r-minus-rate", "low-r-minus-rate",
                           "partial-reversal-history-head"):
            self.assertIn(f'id="{element_id}"'.encode(), dashboard.data)
        for element_id in ("operator-state", "operator-elapsed", "operator-eta", "hardware-status",
                           "integrity-status", "protocol-status", "recent-events", "protocol-select",
                           "preset-select", "runtime-fields",
                           "prepare-button", "start-button", "stop-button", "abort-button",
                           "bait-left-button", "bait-right-button", "control-token"):
            self.assertIn(f'id="{element_id}"'.encode(), dashboard.data)

        javascript = Path(__file__).resolve().parents[1].joinpath("static", "dashboard.js").read_text()
        css = Path(__file__).resolve().parents[1].joinpath("static", "dashboard.css").read_text()
        self.assertIn(".preview img[hidden] { display: none; }", css)
        self.assertIn('empty.hidden = false', javascript)
        self.assertIn('message.hidden = false', javascript)
        self.assertIn('empty.hidden = true', javascript)
        self.assertIn("var hasSession =", javascript)
        self.assertIn('data.protocol === "spout_training"', javascript)
        self.assertIn('data.protocol_version === "exposure_reward_partial_reversal_v1"', javascript)
        self.assertIn("renderPartialReversalBehavior", javascript)
        self.assertIn("task_r_plus_cue_anticipatory_lick_trials_session", javascript)
        self.assertIn("task_r_minus_cue_anticipatory_lick_trials_session", javascript)
        self.assertIn("reversal-readiness", javascript)
        self.assertIn('var isBaitPhase = data.phase === "MANUAL_BAIT" || data.phase === "MANUAL_START_DELAY";', javascript)
        self.assertIn('var isBaitView = isSpout && (isBaitPhase || (!data.phase && data.manual_bait_active === true));', javascript)
        self.assertNotIn('data.manual_bait_active === true);', javascript)
        self.assertIn('setHidden("spout-behavior-scheduled", isBaitView)', javascript)
        self.assertIn('setHidden("spout-behavior-manual", !isBaitView)', javascript)
        self.assertIn('data.phase === "MANUAL_START_DELAY"', javascript)
        self.assertIn('window.setInterval(poll, 300)', javascript)
        self.assertIn("pollInFlight", javascript)
        self.assertIn("Array.isArray(hardwareValues.cameras)", javascript)
        self.assertIn('if (!camera || typeof camera !== "object") return;', javascript)
        self.assertIn(b"No active stimulus", dashboard.data)
        self.assertIn(b"Stimulus image unavailable", dashboard.data)
        state_response = client.get("/api/state")
        self.assertEqual(state_response.status_code, 200)
        self.assertIn("connected", state_response.get_json())
        self.assertEqual(client.get("/health").get_json(), {"status": "ok", "udp_receiver": "ok", "supervisor": "ok"})
        self.assertIn("rig", state_response.get_json())
        self.assertIn("hardware", state_response.get_json()["rig"])
        self.assertEqual(state_response.get_json()["control_enabled"], False)
        self.assertEqual(client.post("/api/control", json={"command": "prepare"}).status_code, 503)

    def test_reward_volume_display_has_no_train_suffix(self):
        javascript = Path(__file__).resolve().parents[1].joinpath("static", "dashboard.js").read_text()
        self.assertIn('$("reward-volume").textContent = formatWater(data.reward_volume_ul_per_train, false);', javascript)
        self.assertNotIn('formatWater(data.reward_volume_ul_per_train, false) + "/train"', javascript)

    def test_unhealthy_receiver_is_not_reported_as_healthy(self):
        app = create_app(MonitorState(), FailedReceiver())
        response = app.test_client().get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["status"], "unhealthy")

    def test_existing_stimulus_image_is_served(self):
        with tempfile.TemporaryDirectory() as image_dir:
            image_path = os.path.join(image_dir, "test.png")
            with open(image_path, "wb") as image_file:
                image_file.write(b"test image bytes")
            app = create_app(MonitorState(), HealthyReceiver(), image_dir=image_dir)
            response = app.test_client().get("/stimulus-image/test.png")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b"test image bytes")
            response.close()

    def test_missing_stimulus_image_is_404(self):
        with tempfile.TemporaryDirectory() as image_dir:
            response = create_app(MonitorState(), HealthyReceiver(), image_dir=image_dir).test_client().get("/stimulus-image/missing.png")
            self.assertEqual(response.status_code, 404)

    def test_missing_image_directory_does_not_break_monitor(self):
        app = create_app(MonitorState(), HealthyReceiver(), image_dir="/path/that/does/not/exist")
        client = app.test_client()
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/api/state").status_code, 200)
        self.assertEqual(client.get("/stimulus-image/test.png").status_code, 404)

    def test_stimulus_image_cannot_escape_directory(self):
        with tempfile.TemporaryDirectory() as image_dir, tempfile.TemporaryDirectory() as outside_dir:
            with open(os.path.join(outside_dir, "secret.txt"), "wb") as secret:
                secret.write(b"not for the dashboard")
            response = create_app(MonitorState(), HealthyReceiver(), image_dir=image_dir).test_client().get("/stimulus-image/../secret.txt")
            self.assertEqual(response.status_code, 404)

    def test_control_requires_bearer_token_and_forwards_protocol_selection(self):
        supervisor = FakeSupervisor()
        client = create_app(MonitorState(), HealthyReceiver(), supervisor=supervisor,
                            control_token="secret").test_client()
        self.assertEqual(client.get("/api/protocols").get_json()[0]["protocol_id"], "fake")
        body = {"command": "prepare", "protocol_id": "fake", "preset_id": "standard",
                "fields": {"subject_id": "example_mouse"}, "request_id": "request-1"}
        self.assertEqual(client.post("/api/control", json=body).status_code, 401)
        response = client.post("/api/control", json=body,
                               headers={"Authorization": "Bearer secret"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ack"]["ok"])
        self.assertEqual(supervisor.calls[-1], {"command": "control", "action": "prepare",
                                                "protocol_id": "fake", "preset_id": "standard",
                                                "fields": {"subject_id": "example_mouse"},
                                                "request_id": "request-1"})
        headers = {"Authorization": "Bearer secret"}
        for name, value in (("executable", "/bin/sh"), ("argv", ["-c", "false"]),
                            ("cwd", "/tmp"), ("env", {"PATH": "/tmp"}),
                            ("control_socket", "/tmp/evil.sock"), ("config", "/etc/passwd"),
                            ("command_text", "false")):
            malicious = dict(body, request_id="attack-" + name, **{name: value})
            self.assertEqual(client.post("/api/control", json=malicious, headers=headers).status_code, 400)
        for field, value in (("protocol_id", "../../bin/bash"), ("preset_id", "; rm -rf /")):
            malicious = dict(body, request_id="attack-id-" + field, **{field: value})
            self.assertEqual(client.post("/api/control", json=malicious, headers=headers).status_code, 400)
        self.assertEqual(client.post("/api/control", json={"command": "start", "request_id": "start-1",
                                                            "argv": ["/bin/sh"]}, headers=headers).status_code, 400)
        self.assertEqual(client.post("/api/control?token=secret", json=body).status_code, 401)
        self.assertEqual(client.post("/api/control", json=body,
                                     headers={"Authorization": "Bearer invalid"}).status_code, 401)
        self.assertNotIn("secret", client.post("/api/control", json=body,
                                                headers={"Authorization": "Bearer invalid"}).get_data(as_text=True))

    def test_missing_supervisor_is_reported_as_unknown_and_is_not_started(self):
        class UnavailableSupervisor:
            def request(self, _request):
                raise SupervisorError("supervisor unavailable")

        app = create_app(MonitorState(), HealthyReceiver(), supervisor=UnavailableSupervisor())
        response = app.test_client().get("/api/state")
        self.assertEqual(response.get_json()["supervisor"]["state"], "UNKNOWN")
        self.assertEqual(response.get_json()["supervisor"]["recovery_state"], "SUPERVISOR_UNAVAILABLE")
        self.assertEqual(response.get_json()["rig"]["session"]["state"], "IDLE")

        with tempfile.TemporaryDirectory() as directory:
            client = SupervisorClient(directory)
            with self.assertRaises(SupervisorError):
                client.connect()
            self.assertFalse(Path(directory, "supervisor.sock").exists())

    def test_unmanaged_active_telemetry_blocks_duplicate_prepare(self):
        state = MonitorState()
        state.update({"type": "state", "session_id": "external", "protocol": "natural_images",
                      "lifecycle": "RUNNING"}, received_at=1.0)
        supervisor = FakeSupervisor()
        client = create_app(state, HealthyReceiver(), supervisor=supervisor,
                            control_token="secret").test_client()
        response = client.post("/api/control", json={"command": "prepare", "protocol_id": "fake",
                                                      "preset_id": "standard", "fields": {},
                                                      "request_id": "external-session-prepare"},
                               headers={"Authorization": "Bearer secret"})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("control", [call["command"] for call in supervisor.calls])

    def test_stale_active_telemetry_for_managed_session_does_not_block_next_prepare(self):
        state = MonitorState()
        state.update({"type": "state", "session_id": "managed-1", "protocol": "fake",
                      "lifecycle": "RUNNING"}, received_at=1.0)
        supervisor = FakeSupervisor()
        supervisor.status.update({"session_id": "managed-1", "protocol": "fake", "state": "COMPLETE"})
        client = create_app(state, HealthyReceiver(), supervisor=supervisor,
                            control_token="secret").test_client()
        response = client.post("/api/control", json={"command": "prepare", "protocol_id": "fake",
                                                      "preset_id": "standard", "fields": {},
                                                      "request_id": "next-prepare"},
                               headers={"Authorization": "Bearer secret"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(supervisor.calls[-1]["command"], "control")


if __name__ == "__main__":
    unittest.main()
