import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from monitor.state import MonitorState
from monitor.supervisor import (
    ACTIVE_STATES,
    SupervisorClient,
    SupervisorError,
    SupervisorServer,
    _new_record,
    _process_identity,
)
from monitor.web import create_app

ROOT = Path(__file__).resolve().parents[1]


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temporary.name)
        self.socket_path = self.state_dir / "supervisor.sock"
        self.daemon = None
        self._start_supervisor()

    def tearDown(self):
        try:
            try:
                status = self._status()
                if status["state"] in ACTIVE_STATES and status.get("control_connected"):
                    self._control("abort")
            except (SupervisorError, KeyError):
                pass
            if self.daemon is not None and self.daemon.poll() is None:
                try:
                    self.client.request({"command": "shutdown-supervisor"})
                except SupervisorError:
                    self.daemon.terminate()
                try:
                    self.daemon.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.daemon.kill()
                    self.daemon.wait(timeout=2)
            self._terminate_recorded_task()
        finally:
            self.temporary.cleanup()

    def _start_supervisor(self, registry_file=None):
        command = [sys.executable, "-m", "monitor.supervisor", "--state-dir", str(self.state_dir),
                   "--socket-path", str(self.socket_path), "--telemetry-port", "0"]
        environment = os.environ.copy()
        environment.pop("RIG_MONITOR_PROTOCOL_REGISTRY", None)
        if registry_file is not None:
            environment["RIG_MONITOR_PROTOCOL_REGISTRY"] = str(registry_file)
        environment["RIG_MONITOR_CONTROL_TOKEN"] = "supervisor-only-test-secret"
        self.daemon = subprocess.Popen(command, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       close_fds=True, start_new_session=True, shell=False, env=environment)
        self.client = SupervisorClient(self.state_dir, self.socket_path)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                self.client.connect()
                return
            except SupervisorError:
                if self.daemon.poll() is not None:
                    self.fail("supervisor exited during startup")
                time.sleep(0.05)
        self.fail("supervisor did not start")

    def _restart_supervisor(self, registry_file=None):
        self.daemon.terminate()
        self.daemon.wait(timeout=3)
        self._start_supervisor(registry_file=registry_file)

    def _terminate_recorded_task(self):
        try:
            record = json.loads((self.state_dir / "supervisor-state.json").read_text())
            pid = record.get("pid")
            expected = record.get("process_starttime")
            identity = _process_identity(pid)
            if identity and identity["process_starttime"] == expected and pid != os.getpid():
                os.kill(int(pid), signal.SIGTERM)
        except (OSError, ValueError, TypeError):
            pass

    def _response(self, action, protocol=None, request_id=None, preset_id="standard", fields=None):
        request = {"command": "control", "action": action, "request_id": request_id or uuid.uuid4().hex}
        if action == "prepare":
            request.update({"protocol_id": protocol or "fake", "preset_id": preset_id,
                            "fields": fields or {"subject_id": "example_mouse"}})
        return self.client.request(request)

    def _control(self, action, protocol=None, preset_id="standard", fields=None):
        response = self._response(action, protocol, preset_id=preset_id, fields=fields)
        self.assertTrue(response.get("ok"), response.get("error"))
        return response.get("result")

    def _status(self):
        return self.client.request({"command": "status"})["status"]

    def _wait_for(self, expected, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._status()
            if status["state"] == expected:
                return status
            time.sleep(0.05)
        self.fail(f"supervisor did not reach {expected}; last state was {status['state']}")

    def test_fake_task_lifecycle_ack_deduplication_and_persistent_logs(self):
        self.assertFalse(self._response("start")["ok"])
        self.assertFalse(self._response("python -c 'import os'")["ok"])
        prepared = self._control("prepare", "fake")
        self.assertEqual(prepared["state"], "READY")
        self.assertEqual(prepared["protocol_id"], "fake")
        self.assertEqual(prepared["preset_id"], "standard")
        self.assertEqual(prepared["subject_id"], "example_mouse")
        self.assertEqual(prepared["adapter_type"], "unix_json_v1")
        self.assertEqual(prepared["launch_metadata"]["runtime_fields"], {"subject_id": "example_mouse"})
        self.assertEqual(prepared["supervisor_schema_version"], 1)
        self.assertEqual(prepared["process_starttime"], _process_identity(prepared["pid"])["process_starttime"])
        self.assertEqual(prepared["process_group_id"], prepared["pid"])
        self.assertEqual(prepared["process_session_id"], prepared["pid"])
        self.assertIsInstance(prepared["command"], list)
        self.assertNotIn(b"supervisor-only-test-secret", Path("/proc", str(prepared["pid"]), "environ").read_bytes())
        self.assertTrue(prepared["started_at_utc"])
        self.assertTrue(prepared["started_monotonic"] > 0)
        self.assertTrue(prepared["capabilities"]["start"])
        self.assertTrue(prepared["capabilities"]["bait_left"])
        self.assertFalse(prepared["capabilities"].get("bait_center", False))
        self.assertFalse(self._response("prepare", "fake")["ok"], "duplicate launch must be rejected")
        self.assertEqual(os.getsid(prepared["pid"]), prepared["pid"])

        repeated = {"command": "control", "action": "bait_left", "request_id": "same-bait-request"}
        first = self.client.request(repeated)
        again = self.client.request(repeated)
        self.assertEqual(first["result"]["ack"], again["result"]["ack"])
        self.assertFalse(self.client.request(dict(repeated, action="bait_right"))["ok"])
        self.assertFalse(self._response("bait_center")["ok"])
        self._control("start")
        self.assertFalse(self._response("bait_left")["ok"])
        self.assertEqual(self._status()["state"], "RUNNING")

        self._control("stop")
        complete = self._wait_for("COMPLETE")
        self.assertEqual(complete["exit_status"], 0)
        self.assertFalse(complete["process_running"])
        self.assertTrue(Path(complete["stdout_path"]).is_file())
        self.assertTrue(Path(complete["stderr_path"]).is_file())
        stdout = Path(complete["stdout_path"]).read_text()
        self.assertIn("graceful stop requested", stdout)
        saved = json.loads((self.state_dir / "supervisor-state.json").read_text())
        self.assertEqual(saved["session_id"], complete["session_id"])

        next_session = self._control("prepare", "fake")
        history = next_session["session_history"]
        self.assertEqual(history[0]["session_id"], complete["session_id"])
        self.assertEqual(history[0]["stdout_path"], complete["stdout_path"])
        self.assertTrue(Path(history[0]["stdout_path"]).is_file())

    def test_supervisor_and_flask_restart_adopt_running_task(self):
        ready = self._control("prepare", "fake")
        self.assertEqual(ready["state"], "READY")
        pid = ready["pid"]
        bait_id = "operator-bait-before-restart"
        bait = self._response("bait_left", request_id=bait_id)
        self.assertTrue(bait["ok"])
        self._control("start")
        old_app = create_app(MonitorState(), supervisor=self.client).test_client()
        old_status = old_app.get("/api/state").get_json()
        self.assertEqual(old_status["supervisor"]["state"], "RUNNING")
        self.assertEqual(old_status["telemetry_freshness"], "NONE")

        record = json.loads((self.state_dir / "supervisor-state.json").read_text())
        self._restart_supervisor()
        identity = _process_identity(pid)
        self.assertIsNotNone(identity, "the experiment must outlive its supervisor")
        self.assertEqual(identity["process_starttime"], record["process_starttime"])
        self.assertEqual(identity["process_session_id"], pid)

        # A new Flask app/client and a new supervisor process reconnect to the saved task.
        stale_telemetry = MonitorState(stale_after_sec=1.0)
        stale_telemetry.update({"type": "state", "session_id": ready["session_id"], "protocol": "fake",
                                "lifecycle": "RUNNING"}, received_at=time.monotonic() - 2)
        new_app = create_app(stale_telemetry, supervisor=self.client).test_client()
        status_response = new_app.get("/api/state").get_json()
        adopted = status_response["supervisor"]
        self.assertEqual(adopted["state"], "RUNNING")
        self.assertEqual(adopted["session_id"], ready["session_id"])
        self.assertEqual(adopted["recovery_state"], "ADOPTED")
        self.assertTrue(adopted["process_running"])
        self.assertTrue(adopted["control_connected"])
        self.assertEqual(status_response["telemetry_freshness"], "STALE")
        self.assertEqual(self._status()["state"], "RUNNING")
        self.assertFalse(self._response("prepare", "fake")["ok"], "adoption must prevent a duplicate launch")
        repeated_bait = self._response("bait_left", request_id=bait_id)
        self.assertTrue(repeated_bait["ok"])
        self.assertEqual(repeated_bait["result"]["ack"], bait["result"]["ack"])
        self.assertFalse(self._response("bait_right", request_id=bait_id)["ok"])

        stop = self._control("stop")
        self.assertTrue(stop["ack"]["ok"])
        complete = self._wait_for("COMPLETE")
        self.assertEqual(complete["session_id"], ready["session_id"])
        self.assertTrue(Path(complete["stdout_path"]).is_file())
        self.assertTrue(Path(complete["stderr_path"]).is_file())
        self.assertIn("fake task started", Path(complete["stdout_path"]).read_text())

    def test_stale_pid_identity_is_never_adopted_or_controlled(self):
        identity = _process_identity(os.getpid())
        state_dir = self.state_dir / "stale-identity"
        server = SupervisorServer(state_dir, state_dir / "supervisor.sock", telemetry_port=0)
        server.record.update({"session_id": "old-session", "protocol": "fake", "state": "RUNNING",
                              "pid": os.getpid(), "process_starttime": identity["process_starttime"] + 1,
                              "process_group_id": identity["process_group_id"],
                              "process_session_id": identity["process_session_id"],
                              "control_endpoint": str(self.state_dir / "must-not-connect.sock"),
                              "capabilities": {"stop": True}})
        server._save()
        status = server.snapshot()
        self.assertEqual(status["state"], "ERROR")
        self.assertEqual(status["recovery_state"], "STALE")
        self.assertFalse(status["process_running"])
        self.assertFalse(status["control_connected"])
        with self.assertRaises(SupervisorError):
            server.control("stop", request_id="stale-stop")
        self.assertIsNotNone(_process_identity(os.getpid()), "the unrelated current process must remain alive")

    def test_task_exit_while_supervisor_is_down_recovers_as_terminal_error(self):
        ready = self._control("prepare", "fake")
        self._control("start")
        pid = ready["pid"]
        self.daemon.terminate()
        self.daemon.wait(timeout=3)
        self.assertIsNotNone(_process_identity(pid))
        os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            identity = _process_identity(pid)
            if identity is None or identity["process_state"] == "Z":
                break
            time.sleep(0.05)
        self._start_supervisor()
        recovered = self._status()
        self.assertEqual(recovered["state"], "ERROR")
        self.assertEqual(recovered["recovery_state"], "EXITED")
        self.assertFalse(recovered["process_running"])
        self.assertTrue(self._response("prepare", "fake")["ok"])

    def test_missing_control_socket_keeps_verified_process_running(self):
        state_dir = self.state_dir / "missing-control"
        server = SupervisorServer(state_dir, state_dir / "supervisor.sock", telemetry_port=0)
        prepared = server.prepare("fake")
        server.control("start", request_id="missing-endpoint-start")
        Path(prepared["control_endpoint"]).unlink()
        try:
            status = server.snapshot()
            self.assertEqual(status["state"], "RUNNING")
            self.assertEqual(status["recovery_state"], "CONTROL_UNAVAILABLE")
            self.assertTrue(status["process_running"])
            self.assertFalse(status["control_connected"])
            with self.assertRaisesRegex(SupervisorError, "left running"):
                server.control("stop", request_id="missing-endpoint-stop")
            self.assertIsNotNone(_process_identity(prepared["pid"]))
        finally:
            server.process.terminate()
            server.process.wait(timeout=3)

    def test_exited_or_stale_record_does_not_block_future_launch(self):
        state_dir = self.state_dir / "exited-record"
        state_dir.mkdir()
        state_file = state_dir / "supervisor-state.json"
        stale = _new_record()
        stale.update({"session_id": "dead-session", "protocol": "fake", "state": "RUNNING",
                      "pid": 2147483647, "process_starttime": 1, "process_group_id": 1,
                      "process_session_id": 1, "control_endpoint": str(self.state_dir / "dead.sock")})
        state_file.write_text(json.dumps(stale))
        server = SupervisorServer(state_dir, state_dir / "supervisor.sock", telemetry_port=0)
        recovered = server.snapshot()
        self.assertEqual(recovered["state"], "ERROR")
        self.assertEqual(recovered["recovery_state"], "EXITED")
        self.assertFalse(recovered["process_running"])
        launched = server.prepare("fake", request_id="recovered-launch")
        self.assertEqual(launched["state"], "READY")
        server.control("abort", request_id="cleanup-abort")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and server.snapshot()["process_running"]:
            time.sleep(0.05)
        self.assertFalse(server.snapshot()["process_running"])

    def test_external_task_process_is_adopted_after_registry_changes(self):
        task_source = '''
import argparse, json, socket, time
parser = argparse.ArgumentParser()
parser.add_argument("--profile", required=True)
parser.add_argument("--subject-id", required=True)
parser.add_argument("--control-socket", required=True)
parser.add_argument("--session-id", required=True)
parser.add_argument("--telemetry-port", type=int, required=True)
args = parser.parse_args()
state = "READY"
started = None
finish_at = None
cache = {}
def status():
    elapsed = max(0.0, time.monotonic() - started) if started is not None else 0.0
    return {"session_id": args.session_id, "protocol": "external-native", "state": state,
            "elapsed_s": elapsed, "eta_s": None,
            "exit_status": 0 if state in ("COMPLETE", "ABORTED") else None,
            "capabilities": {"start": state == "READY", "stop": state == "RUNNING",
                             "abort": state in ("PREPARING", "READY", "RUNNING")}}
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(args.control_socket)
server.listen(4)
server.settimeout(0.05)
print("external fixture ready subject=" + args.subject_id, flush=True)
try:
    done = False
    while not done:
        if state == "FINISHING" and time.monotonic() >= finish_at:
            state = "COMPLETE"
        try:
            client, _ = server.accept()
        except socket.timeout:
            continue
        with client:
            request = json.loads(client.makefile("rb").readline())
            command, request_id = request.get("command"), request.get("request_id")
            signature = json.dumps(request, sort_keys=True)
            if command != "status" and request_id in cache:
                cached_signature, cached_response = cache[request_id]
                response = cached_response if cached_signature == signature else {"ok": False, "error": "request ID conflict"}
            elif command == "status":
                response = {"ok": True, "status": status()}
            else:
                error = None
                if command == "start" and state == "READY":
                    state, started = "RUNNING", time.monotonic()
                elif command == "stop" and state == "RUNNING":
                    state, finish_at = "FINISHING", time.monotonic() + 0.1
                elif command == "abort" and state in ("PREPARING", "READY", "RUNNING"):
                    state = "ABORTED"
                elif command == "shutdown" and state in ("COMPLETE", "ERROR", "ABORTED"):
                    done = True
                else:
                    error = "command unavailable"
                if error:
                    response = {"ok": False, "error": error}
                else:
                    response = {"schema_version": 1, "request_id": request_id, "ok": True,
                                "state": state, "status": status()}
                if command != "status" and request_id:
                    cache[request_id] = (signature, response)
            client.sendall(json.dumps(response).encode() + b"\\n")
finally:
    server.close()
'''
        script = self.state_dir / "external_task.py"
        config = self.state_dir / "preset.toml"
        registry_file = self.state_dir / "protocols.json"
        script.write_text(task_source)
        config.write_text("profile = 'short'\n")
        definition = {
            "protocol_id": "external_fixture", "display_name": "External fixture",
            "description": "Separate process, not imported by monitor",
            "adapter_type": "unix_json_v1", "engine_protocol_id": "external-native",
            "executable": [sys.executable, str(script)],
            "working_directory": str(self.state_dir),
            "runtime_fields": [{"name": "subject_id", "display_name": "Subject / Mouse ID",
                                "required": True, "type": "subject_id", "cli_flag": "--subject-id"}],
            "presets": [{"preset_id": "short_validation", "display_name": "Short validation",
                         "description": "Test fixture preset", "arguments": ["--profile", "short"],
                         "required_files": [str(config)]}],
        }
        registry_file.write_text(json.dumps({"schema_version": 1, "protocols": [definition]}))
        self._restart_supervisor(registry_file=registry_file)

        ready = self._control("prepare", "external_fixture", preset_id="short_validation",
                              fields={"subject_id": "Fucci-Tri_7"})
        self.assertEqual(ready["state"], "READY")
        self.assertEqual(ready["protocol_id"], "external_fixture")
        self.assertEqual(ready["engine_protocol_id"], "external-native")
        self.assertEqual(ready["preset_id"], "short_validation")
        self.assertEqual(ready["subject_id"], "Fucci-Tri_7")
        self.assertEqual(ready["adapter_type"], "unix_json_v1")
        self.assertEqual(ready["command"][0], str(Path(sys.executable).resolve()))
        self.assertIn("--profile", ready["command"])
        self.assertIn("short", ready["command"])
        self.assertIn("--subject-id", ready["command"])
        self.assertIn("Fucci-Tri_7", ready["command"])
        self.assertEqual(ready["launch_metadata"]["runtime_fields"], {"subject_id": "Fucci-Tri_7"})
        script_pid = ready["pid"]
        self._control("start")
        self.assertEqual(self._wait_for("RUNNING")["pid"], script_pid)

        # Remove the running protocol from the new registry; adoption uses saved launch identity.
        registry_file.write_text(json.dumps({"schema_version": 1, "protocols": []}))
        self._restart_supervisor(registry_file=registry_file)
        adopted = self._status()
        self.assertEqual(adopted["state"], "RUNNING")
        self.assertEqual(adopted["recovery_state"], "ADOPTED")
        self.assertEqual(adopted["pid"], script_pid)
        self.assertEqual(adopted["protocol_id"], "external_fixture")
        self.assertEqual(adopted["preset_id"], "short_validation")
        self.assertEqual(adopted["command"], ready["command"])
        app = create_app(MonitorState(), supervisor=self.client)
        client = app.test_client()
        app_status = client.get("/api/state").get_json()
        self.assertEqual(app_status["supervisor"]["protocol_id"], "external_fixture")
        self.assertEqual(app_status["supervisor"]["preset_id"], "short_validation")
        self.assertEqual([item["protocol_id"] for item in client.get("/api/protocols").get_json()], ["fake"])
        self.assertFalse(self._response("prepare", "fake")["ok"], "active task blocks a second protocol launch")
        self._control("stop")
        complete = self._wait_for("COMPLETE")
        self.assertEqual(complete["exit_status"], 0)
        self.assertTrue(Path(complete["stdout_path"]).is_file())
        self.assertIn("external fixture ready subject=Fucci-Tri_7", Path(complete["stdout_path"]).read_text())

    def test_unavailable_external_protocol_is_listed_but_cannot_launch(self):
        missing_executable = str(self.state_dir / "not-installed-task")
        missing_config = str(self.state_dir / "missing.toml")
        registry_file = self.state_dir / "optional-protocols.json"
        registry_file.write_text(json.dumps({"schema_version": 1, "protocols": [{
            "protocol_id": "optional_external", "display_name": "Optional external task",
            "description": "Configured separately", "adapter_type": "unix_json_v1",
            "executable": [missing_executable],
            "runtime_fields": [{"name": "subject_id", "display_name": "Subject / Mouse ID",
                                "required": False, "type": "subject_id"}],
            "presets": [{"preset_id": "standard", "display_name": "Standard",
                         "required_files": [missing_config]}],
        }]}))
        self._restart_supervisor(registry_file=registry_file)
        app = create_app(MonitorState(), supervisor=self.client, control_token="test-token")
        client = app.test_client()
        protocols = client.get("/api/protocols").get_json()
        optional = next(item for item in protocols if item["protocol_id"] == "optional_external")
        self.assertFalse(optional["available"])
        self.assertFalse(optional["presets"][0]["available"])
        self.assertIn("not installed", optional["unavailable_reason"].lower())
        public_json = json.dumps(optional)
        self.assertNotIn(missing_executable, public_json)
        self.assertNotIn(missing_config, public_json)

        headers = {"Authorization": "Bearer test-token"}
        body = {"command": "prepare", "protocol_id": "optional_external", "preset_id": "standard",
                "fields": {}, "request_id": "unavailable-launch"}
        self.assertEqual(client.post("/api/control", json=body, headers=headers).status_code, 409)
        unknown = dict(body, protocol_id="not_registered", request_id="unknown-launch")
        self.assertEqual(client.post("/api/control", json=unknown, headers=headers).status_code, 409)
        self.assertEqual(client.post("/api/control", json=dict(body, protocol_id="../../bin/bash"),
                                     headers=headers).status_code, 400)
        unsupported_fields = dict(body, request_id="unsupported-fields",
                                  fields={"subject_id": "example_mouse", "config": "/etc/passwd"})
        self.assertEqual(client.post("/api/control", json=unsupported_fields, headers=headers).status_code, 409)
        invalid_subject = dict(body, request_id="invalid-subject",
                               fields={"subject_id": "../example_mouse"})
        self.assertEqual(client.post("/api/control", json=invalid_subject, headers=headers).status_code, 409)
        self.assertEqual(self._status()["state"], "IDLE")
        self.assertFalse(self._status()["process_running"])


if __name__ == "__main__":
    unittest.main()
