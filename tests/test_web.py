import hashlib
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
import zlib

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


def _png_bytes():
    def chunk(kind, data):
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xffffffff)

    header = struct.pack(">IIBBBBB", 90, 68, 8, 0, 0, 0, 0)
    pixels = b"".join(b"\x00" + bytes([127]) * 90 for _ in range(68))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def _write_selected_session(session_root, session_id, filename, image_bytes):
    session = session_root / session_id
    session.mkdir(parents=True)
    digest = hashlib.sha256(image_bytes).hexdigest()
    (session / "plan.json").write_text(json.dumps({
        "metadata": {"selected_images": [filename]},
    }))
    (session / "selected_image_table.json").write_text(json.dumps({
        "images": [{"index": 1, "image": filename, "sha256": digest}],
    }))
    (session / "asset_manifest.json").write_text(json.dumps({
        "entries": [{"relative_path": filename, "sha256": digest}],
    }))
    return session


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

    def test_missing_natural_assets_do_not_break_monitor(self):
        client = create_app(
            MonitorState(), HealthyReceiver(), natural_image_dir="/path/that/does/not/exist",
            session_root="/path/that/does/not/exist",
        ).test_client()
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get(
            "/api/stimulus-image/session-1/natimg_center_0001.png"
        ).status_code, 404)

    def test_stimulus_image_cannot_escape_directory(self):
        with tempfile.TemporaryDirectory() as image_dir, tempfile.TemporaryDirectory() as outside_dir:
            with open(os.path.join(outside_dir, "secret.txt"), "wb") as secret:
                secret.write(b"not for the dashboard")
            response = create_app(MonitorState(), HealthyReceiver(), image_dir=image_dir).test_client().get("/stimulus-image/../secret.txt")
            self.assertEqual(response.status_code, 404)

    def test_legacy_stimulus_route_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as image_dir, tempfile.TemporaryDirectory() as outside_dir:
            secret = Path(outside_dir) / "outside.png"
            secret.write_bytes(b"private")
            (Path(image_dir) / "link.png").symlink_to(secret)
            client = create_app(MonitorState(), HealthyReceiver(), image_dir=image_dir).test_client()
            self.assertEqual(client.get("/stimulus-image/link.png").status_code, 404)

    def test_selected_natural_image_requires_session_selection_and_returns_exact_png(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets, sessions = root / "assets", root / "sessions"
            assets.mkdir()
            sessions.mkdir()
            filename = "natimg_center_0007.png"
            image_bytes = _png_bytes()
            (assets / filename).write_bytes(image_bytes)
            _write_selected_session(sessions, "session-7", filename, image_bytes)
            client = create_app(MonitorState(), HealthyReceiver(), image_dir=root / "legacy",
                                session_root=sessions, natural_image_dir=assets).test_client()
            response = client.get(f"/api/stimulus-image/session-7/{filename}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "image/png")
            self.assertEqual(response.data, image_bytes)
            self.assertIn("max-age=86400", response.headers["Cache-Control"])
            self.assertEqual(client.get(
                "/api/stimulus-image/session-7/natimg_center_0008.png"
            ).status_code, 404)
            self.assertEqual(client.get(
                "/api/stimulus-image/session-7/natimg_center_2800.png"
            ).status_code, 404)
            self.assertEqual(client.get(
                "/api/stimulus-image/../outside/natimg_center_0007.png"
            ).status_code, 404)
            invalid = client.get(f"/api/stimulus-image/%2e%2e/{filename}")
            self.assertEqual(invalid.status_code, 404)
            self.assertEqual(invalid.data, b"")
            self.assertNotIn(str(root).encode(), invalid.data)

            session = sessions / "session-7"
            (session / "plan.json").write_text(json.dumps({"metadata": None}))
            malformed = client.get(f"/api/stimulus-image/session-7/{filename}")
            self.assertEqual(malformed.status_code, 404)
            self.assertEqual(malformed.data, b"")

    def test_selected_image_endpoint_rejects_path_escape_and_bad_session_mapping(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            assets, sessions = root / "assets", root / "sessions"
            assets.mkdir()
            sessions.mkdir()
            filename = "natimg_center_0007.png"
            image_bytes = _png_bytes()
            outside_image = Path(outside) / filename
            outside_image.write_bytes(image_bytes)
            (assets / filename).symlink_to(outside_image)
            _write_selected_session(sessions, "session-7", filename, image_bytes)
            client = create_app(MonitorState(), HealthyReceiver(), image_dir=root / "legacy",
                                session_root=sessions, natural_image_dir=assets).test_client()
            self.assertEqual(client.get(
                f"/api/stimulus-image/session-7/{filename}"
            ).status_code, 404)
            self.assertEqual(client.get(
                f"/api/stimulus-image/missing-session/{filename}"
            ).status_code, 404)

    def test_selected_image_endpoint_rejects_symlinked_session_escape_and_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            assets, sessions = root / "assets", root / "sessions"
            assets.mkdir()
            sessions.mkdir()
            filename = "natimg_center_0007.png"
            image_bytes = _png_bytes()
            (assets / filename).write_bytes(image_bytes)
            outside_session = _write_selected_session(Path(outside), "external", filename, image_bytes)
            (sessions / "session-escape").symlink_to(outside_session, target_is_directory=True)
            session = _write_selected_session(sessions, "session-7", filename, image_bytes)
            table = json.loads((session / "selected_image_table.json").read_text())
            table["images"][0]["sha256"] = "0" * 64
            (session / "selected_image_table.json").write_text(json.dumps(table))
            client = create_app(MonitorState(), HealthyReceiver(), image_dir=root / "legacy",
                                session_root=sessions, natural_image_dir=assets).test_client()
            self.assertEqual(client.get(
                f"/api/stimulus-image/session-escape/{filename}"
            ).status_code, 404)
            self.assertEqual(client.get(
                f"/api/stimulus-image/session-7/{filename}"
            ).status_code, 404)

    def test_dashboard_contains_natural_image_and_independent_lick_fields(self):
        client = create_app(MonitorState(), HealthyReceiver()).test_client()
        dashboard = client.get("/")
        for element_id in ("natural-stimulus-details", "stimulus-filename", "stimulus-sequence",
                           "stimulus-freshness", "natural-lick-feedback", "lick-left-status",
                           "lick-left-contact", "lick-left-onset", "lick-left-recent",
                           "lick-left-total", "lick-right-status", "lick-right-contact",
                           "lick-right-onset", "lick-right-recent", "lick-right-total"):
            self.assertIn(f'id="{element_id}"'.encode(), dashboard.data)
        javascript = Path(__file__).resolve().parents[1].joinpath("static", "dashboard.js").read_text()
        self.assertIn('"/api/stimulus-image/" + encodeURIComponent(sessionId)', javascript)
        self.assertIn('data.telemetry_freshness === "FRESH"', javascript)
        self.assertIn('clearNaturalPreview("STALE / UNKNOWN", "stale")', javascript)
        self.assertIn('clearNaturalPreview("Session ended", "gray")', javascript)
        self.assertIn('channel.contact_active === true', javascript)
        self.assertIn('channel.last_lick_age_sec <= 1.5', javascript)

    def test_control_requires_bearer_token_and_forwards_protocol_selection(self):
        supervisor = FakeSupervisor()
        supervisor.status.update({"control_state_path": "/private/control-state/session.json",
                                  "recovery_diagnostic": "durable control state rejected: wrong session",
                                  "lifecycle_source": "engine_control_state_file"})
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
        public_status = client.get("/api/state").get_json()["supervisor"]
        self.assertEqual(public_status["recovery_diagnostic"],
                         "durable control state rejected: wrong session")
        self.assertEqual(public_status["lifecycle_source"], "engine_control_state_file")
        self.assertNotIn("control_state_path", public_status)
        headers = {"Authorization": "Bearer secret"}
        for name, value in (("executable", "/bin/sh"), ("argv", ["-c", "false"]),
                            ("cwd", "/tmp"), ("env", {"PATH": "/tmp"}),
                            ("control_socket", "/tmp/evil.sock"), ("config", "/etc/passwd"),
                            ("control_state_path", "/tmp/attacker.json"),
                            ("control_state_artifact_type", "attacker.state"),
                            ("control_instance_id", "attacker-instance"),
                            ("control_state_contents", {"state": "COMPLETE"}),
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
