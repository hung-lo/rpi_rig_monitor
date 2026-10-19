"""Optional hardware-free integration against the real rpi-vstim console entry point."""

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

try:
    import pytest
except ImportError:  # unittest discovery remains usable without the optional integration runner.
    pytest = None

from monitor.supervisor import (
    SupervisorClient,
    SupervisorError,
    _process_identity,
    _rpc,
)

pytestmark = pytest.mark.vstim_integration if pytest is not None else None
VSTIM_CHECKOUT = os.environ.get("RPI_VSTIM_CHECKOUT")
VSTIM_EXECUTABLE = os.environ.get("RPI_VSTIM_EXECUTABLE")
VSTIM_PYTHON = os.environ.get("RPI_VSTIM_PYTHON")
PILLOW_PATH = os.environ.get("RPI_VSTIM_PYTHONPATH", "")


def _port(kind):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _stop(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=4)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _http(port, path, body=None, token=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data, headers)
    try:
        response = urllib.request.urlopen(request, timeout=2)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _http_bytes(port, path):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        response = urllib.request.urlopen(request, timeout=2)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, response.headers.get_content_type(), response.read()


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


def _wait_until(predicate, timeout=8, message="condition was not reached"):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, SupervisorError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.05)
    detail = ": " + str(last_error) if last_error else ""
    raise AssertionError(f"{message}{detail}")


def _make_registry(path, executable, checkout, output_root, config=None):
    profile = Path(config or checkout / "config/rigs/natural_images.operator_simulation.toml")
    entry = {
        "protocol_id": "natural_images",
        "display_name": "Natural Images",
        "description": "Natural-image task in the VStim FakeDisplayBackend simulation.",
        "adapter_type": "unix_json_v1",
        "engine_protocol_id": "natural-images",
        "control_state_flag": "--control-state-file",
        "control_state_artifact_type": "rpi_vstim.operator_control_state",
        "executable": [str(executable)],
        "working_directory": str(checkout),
        "runtime_fields": [{"name": "subject_id", "display_name": "Subject / Mouse ID",
                            "required": True, "type": "subject_id", "cli_flag": "--mouse-id"}],
        "presets": [{
            "preset_id": "operator_simulation",
            "display_name": "Operator simulation",
            "description": "Five-image FakeDisplayBackend validation; no physical hardware.",
            "config_path": str(profile),
            "arguments": ["natural-images", "run", "--config", str(profile),
                          "--image-count", "5", "--simulate", "--operator-control",
                          "--output-root", str(output_root)],
            "required_files": [str(profile)],
        }],
    }
    path.write_text(json.dumps({"schema_version": 1, "protocols": [entry]}))
    return entry


def test_real_natural_images_v1_reconnect_and_lifecycle():
    if not (VSTIM_CHECKOUT and VSTIM_EXECUTABLE and VSTIM_PYTHON):
        pytest.skip("set RPI_VSTIM_CHECKOUT, RPI_VSTIM_EXECUTABLE, and RPI_VSTIM_PYTHON")
    checkout = Path(VSTIM_CHECKOUT).resolve()
    executable = Path(VSTIM_EXECUTABLE).resolve()
    python = Path(VSTIM_PYTHON).resolve()
    profile = checkout / "config/rigs/natural_images.operator_simulation.toml"
    if not (checkout.is_dir() and executable.is_file() and profile.is_file() and python.is_file()):
        pytest.skip("configured VStim checkout, executable, or simulation profile is missing")
    shebang = executable.read_text().splitlines()[0].strip()
    assert shebang.startswith("#!") and Path(shebang[2:]).resolve() == python
    vstim_environment = os.environ.copy()
    vstim_pythonpath = [str(checkout / "src")]
    if PILLOW_PATH:
        vstim_pythonpath.append(PILLOW_PATH)
    vstim_environment["PYTHONPATH"] = os.pathsep.join(vstim_pythonpath)
    version = subprocess.check_output([str(python), "--version"], text=True).strip()
    if not version.startswith("Python 3.13."):
        pytest.skip(f"VStim integration requires Python 3.13; configured interpreter is {version}")
    assert subprocess.run([str(executable), "--version"], env=vstim_environment,
                          check=True, capture_output=True).returncode == 0

    with tempfile.TemporaryDirectory(prefix="monitor-vstim-integration-") as temp_name:
        root = Path(temp_name)
        state_dir = root / "state"
        socket_path = state_dir / "supervisor.sock"
        registry_path = root / "protocols.json"
        output_root = root / "vstim-output"
        session_root = root / "real-sessions"
        session_root.mkdir()
        natural_image_dir = checkout / "assets/natural_images/stringer_natimg2800_center_crop"
        fixture_filename = "natimg_center_0000.png"
        fixture_bytes = (natural_image_dir / fixture_filename).read_bytes()
        _write_selected_session(session_root, "fixture-real-session", fixture_filename, fixture_bytes)
        registry_entry = _make_registry(registry_path, executable, checkout, output_root)
        good_registry = registry_path.read_text()
        web_port = _port(socket.SOCK_STREAM)
        telemetry_port = _port(socket.SOCK_DGRAM)
        token = uuid.uuid4().hex
        environment = vstim_environment
        supervisor_log = (root / "supervisor.log").open("ab")
        web_log = (root / "web.log").open("ab")
        supervisor = None
        web = None
        client = None
        seen_control_state_paths = set()

        def assert_new_control_state_path(status):
            path = Path(status["control_state_path"])
            assert str(path) not in seen_control_state_paths
            seen_control_state_paths.add(str(path))
            assert path.parent == state_dir / "control-state"
            assert path.name == status["session_id"] + ".json"

        def start_supervisor():
            nonlocal supervisor
            supervisor = subprocess.Popen(
                [sys.executable, "-m", "monitor.supervisor", "--state-dir", str(state_dir),
                 "--socket-path", str(socket_path), "--telemetry-port", str(telemetry_port),
                 "--protocol-registry", str(registry_path)],
                cwd=str(Path(__file__).resolve().parents[1]), env=environment,
                stdin=subprocess.DEVNULL, stdout=supervisor_log, stderr=supervisor_log,
                close_fds=True, start_new_session=True, shell=False,
            )
            client = SupervisorClient(state_dir, socket_path)
            _wait_until(client.connect, message="supervisor did not start")
            return client

        def start_web(udp_port, stale_after_sec=5.0):
            nonlocal web
            web_env = environment.copy()
            web_env.update({
                "RIG_MONITOR_CONTROL_TOKEN": token,
                "RIG_MONITOR_UDP_HOST": "127.0.0.1",
                "RIG_MONITOR_UDP_PORT": str(udp_port),
                "RIG_MONITOR_STALE_AFTER_SEC": str(stale_after_sec),
                "RIG_MONITOR_WEB_HOST": "127.0.0.1",
                "RIG_MONITOR_WEB_PORT": str(web_port),
                "RIG_MONITOR_SUPERVISOR_STATE_DIR": str(state_dir),
                "RIG_MONITOR_SUPERVISOR_SOCKET": str(socket_path),
                "RIG_MONITOR_SESSION_ROOT": str(session_root),
                "RIG_MONITOR_SIMULATION_SESSION_ROOT": str(output_root),
                "RIG_MONITOR_NATURAL_IMAGE_DIR": str(natural_image_dir),
            })
            web = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve().parents[1] / "run_monitor.py")],
                cwd=str(Path(__file__).resolve().parents[1]), env=web_env,
                stdin=subprocess.DEVNULL, stdout=web_log, stderr=web_log,
                close_fds=True, start_new_session=True, shell=False,
            )
            _wait_until(lambda: _http(web_port, "/health")[0] == 200,
                        message="Flask monitor did not start")

        def stop_web():
            nonlocal web
            _stop(web)
            web = None

        def restart_supervisor():
            nonlocal supervisor
            _stop(supervisor)
            return start_supervisor()

        def control(command, request_id, **fields):
            status, body = _http(web_port, "/api/control",
                                 {"command": command, "request_id": request_id, **fields}, token)
            if status != 200:
                record_path = state_dir / "supervisor-state.json"
                record = json.loads(record_path.read_text()) if record_path.is_file() else {}
                stderr_path = Path(record["stderr_path"]) if record.get("stderr_path") else None
                stderr = stderr_path.read_text() if stderr_path and stderr_path.is_file() else ""
                raise AssertionError(f"{body}; experiment stderr: {stderr}")
            return body

        def wait_engine_state(client, expected, timeout=8):
            def query():
                status = client.request({"command": "status"})["status"]
                return status if status["state"] == expected else None
            return _wait_until(query, timeout=timeout, message="engine did not reach " + expected)

        def wait_exited_state(client, expected, timeout=8):
            last = {}
            def query():
                status = client.request({"command": "status"})["status"]
                last.update(status)
                return status if status["state"] == expected and not status["process_running"] else None
            try:
                return _wait_until(query, timeout=timeout, message="task did not exit in " + expected)
            except AssertionError as exc:
                details = {key: last.get(key) for key in (
                    "state", "pid", "process_running", "process_state", "exit_status", "recovery_state",
                )}
                logs = []
                for key in ("stdout_path", "stderr_path"):
                    path = Path(last[key]) if last.get(key) else None
                    if path and path.is_file():
                        logs.append(path.read_text()[-1000:])
                raise AssertionError(f"{exc}; task={details}; logs={logs}")

        def wait_exit_or_error(client, expected):
            def query():
                status = client.request({"command": "status"})["status"]
                if not status["process_running"] and status["state"] in (expected, "ERROR"):
                    return status
            return _wait_until(query, message="task did not exit in " + expected)

        try:
            client = start_supervisor()
            start_web(telemetry_port)
            fixture_status, fixture_mimetype, served_fixture = _http_bytes(
                web_port, f"/api/stimulus-image/fixture-real-session/{fixture_filename}"
            )
            assert fixture_status == 200 and fixture_mimetype == "image/png"
            assert served_fixture == fixture_bytes
            status, protocols = _http(web_port, "/api/protocols")
            assert status == 200
            natural = next(item for item in protocols if item["protocol_id"] == "natural_images")
            preset = next(item for item in natural["presets"]
                          if item["preset_id"] == "operator_simulation")
            assert natural["available"] and preset["available"]
            assert not any(key in natural for key in
                            ("executable", "argv", "cwd", "environment", "config_path"))
            assert "config_path" not in preset
            assert "--simulate" in registry_entry["presets"][0]["arguments"]
            assert "--licks" not in registry_entry["presets"][0]["arguments"]
            assert "--cameras" not in registry_entry["presets"][0]["arguments"]

            prepared = control("prepare", "integration-prepare-1", protocol_id="natural_images",
                               preset_id="operator_simulation",
                               fields={"subject_id": "example_mouse"})
            assert prepared["state"] == "READY"
            snapshot = client.request({"command": "status"})["status"]
            assert_new_control_state_path(snapshot)
            assert snapshot["process_running"] and snapshot["control_connected"]
            assert snapshot["protocol_id"] == "natural_images"
            assert snapshot["preset_id"] == "operator_simulation"
            assert snapshot["engine_protocol_id"] == "natural-images"
            assert snapshot["engine_session_id"] == snapshot["session_id"]
            assert snapshot["adapter_type"] == "unix_json_v1"
            assert snapshot["control_schema_version"] == 1
            assert snapshot["subject_id"] == "example_mouse"
            assert snapshot["config_path"] == str(profile)
            assert snapshot["launch_metadata"]["config_path"] == str(profile)
            control_state_path = Path(snapshot["control_state_path"])
            assert control_state_path.parent == state_dir / "control-state"
            assert control_state_path.name == snapshot["session_id"] + ".json"
            assert control_state_path.is_file()
            assert control_state_path.stat().st_mode & 0o777 == 0o600
            assert snapshot["launch_metadata"]["control_state_path"] == str(control_state_path)
            assert snapshot["control_state_artifact_type"] == "rpi_vstim.operator_control_state"
            assert snapshot["launch_metadata"]["control_state_artifact_type"] == "rpi_vstim.operator_control_state"
            assert snapshot["control_instance_id"]
            assert "--control-state-file" in snapshot["command"]
            assert snapshot["command"][snapshot["command"].index("--control-state-file") + 1] == str(control_state_path)
            assert "--simulate" in snapshot["command"]
            assert snapshot["command"][snapshot["command"].index("--mouse-id") + 1] == "example_mouse"
            assert "--operator-control" in snapshot["command"]
            assert Path(snapshot["control_state_path"]).parent.stat().st_mode & 0o777 == 0o700
            assert Path(snapshot["stdout_path"]).is_file()
            assert Path(snapshot["stderr_path"]).is_file()
            assert snapshot["capabilities"].get("start") is True
            assert snapshot["capabilities"].get("stop") is False
            engine_status = _rpc(snapshot["control_endpoint"],
                                 {"schema_version": 1, "request_id": "direct-ready-status",
                                  "command": "status"})
            assert engine_status["schema_version"] == 1
            assert engine_status["request_id"] == "direct-ready-status"
            assert engine_status["status"]["protocol"] == "natural-images"
            assert engine_status["status"]["state"] == "READY"
            assert engine_status["status"]["elapsed_s"] == 0.0
            assert not list(output_root.rglob("session_manifest.json"))
            assert not list(output_root.rglob("events.jsonl"))

            override_status, _ = _http(
                web_port, "/api/control",
                {"command": "prepare", "request_id": "override-state-path",
                 "protocol_id": "natural_images", "preset_id": "operator_simulation",
                 "fields": {"subject_id": "example_mouse"},
                 "control_state_path": "/tmp/attacker.json",
                 "control_state_artifact_type": "attacker.state"}, token,
            )
            assert override_status == 400

            def read_fresh_api_state():
                state = _http(web_port, "/api/state")[1]
                return state if state["telemetry_freshness"] == "FRESH" else False

            fresh_state = _wait_until(read_fresh_api_state,
                                      message="simulation telemetry did not become fresh")
            lick_channels = fresh_state["lick_state"]["channels"]
            assert lick_channels["left"]["status"] == "not_requested"
            assert lick_channels["right"]["status"] == "not_requested"
            session_id = snapshot["session_id"]
            stop_web()
            start_web(telemetry_port)
            api_state = _http(web_port, "/api/state")[1]
            assert api_state["supervisor"]["session_id"] == session_id
            assert api_state["supervisor"]["state"] == "READY"

            # The changed registry applies to future tasks; adoption uses the saved launch identity.
            registry_path.write_text(json.dumps({"schema_version": 1, "protocols": []}))
            client = restart_supervisor()
            adopted = client.request({"command": "status"})["status"]
            assert adopted["state"] == "READY" and adopted["recovery_state"] == "ADOPTED"
            assert adopted["session_id"] == session_id
            assert adopted["engine_session_id"] == session_id
            assert adopted["command"] == snapshot["command"]
            assert adopted["config_path"] == str(profile)
            assert adopted["launch_metadata"]["config_path"] == str(profile)
            assert all(item["protocol_id"] != "natural_images"
                       for item in _http(web_port, "/api/protocols")[1])
            blocked_status, blocked = _http(
                web_port, "/api/control",
                {"command": "prepare", "request_id": "duplicate-active",
                 "protocol_id": "fake", "preset_id": "standard", "fields": {}}, token,
            )
            assert blocked_status == 409 and "already active" in blocked["error"]

            # ABORT while READY is acknowledged without creating an acquisition session.
            ready_abort = control("abort", "real-abort-ready-2")
            assert ready_abort["ack"]["schema_version"] == 1
            assert ready_abort["ack"]["request_id"] == "real-abort-ready-2"
            assert control("abort", "real-abort-ready-2")["ack"] == ready_abort["ack"]
            aborted_ready = wait_exited_state(client, "ABORTED")
            assert aborted_ready["session_id"] == session_id
            assert not (output_root / session_id).exists()
            ready_state = json.loads(Path(aborted_ready["control_state_path"]).read_text())
            assert ready_state["terminal"] is True and ready_state["lifecycle"] == "ABORTED"
            assert ready_state["terminal_request_id"] == "real-abort-ready-2"
            assert not Path(aborted_ready["control_endpoint"]).exists()

            # Invalid configuration fails clearly; restoring the registry permits a new launch.
            broken_config = root / "missing-assets.toml"
            empty_assets = root / "empty-assets"
            empty_assets.mkdir()
            broken_config.write_text(profile.read_text().replace(
                'asset_root = "../../assets/natural_images/stringer_natimg2800_center_crop"',
                'asset_root = "' + str(empty_assets) + '"',
            ))
            _make_registry(registry_path, executable, checkout, output_root, config=broken_config)
            client = restart_supervisor()
            bad_status, failure = _http(
                web_port, "/api/control",
                {"command": "prepare", "request_id": "broken-prepare",
                 "protocol_id": "natural_images", "preset_id": "operator_simulation",
                 "fields": {"subject_id": "example_mouse"}}, token,
            )
            assert bad_status == 409 and "preparation" in failure["error"]
            failed = client.request({"command": "status"})["status"]
            assert_new_control_state_path(failed)
            assert failed["state"] == "ERROR"
            assert failed["exit_status"] not in (None, 0)
            error_state = json.loads(Path(failed["control_state_path"]).read_text())
            assert error_state["terminal"] is True and error_state["lifecycle"] == "ERROR"
            assert failed["lifecycle_source"] == "engine_control_state_file"
            assert failed["engine_error_summary"]
            failure_log = Path(failed["stderr_path"]).read_text().lower()
            assert "contains no images" in failed["engine_error_summary"].lower()
            assert failure_log and ("failed" in failure_log or "asset" in failure_log)

            registry_path.write_text(good_registry)
            client = restart_supervisor()
            prepared = control("prepare", "valid-after-error", protocol_id="natural_images",
                               preset_id="operator_simulation", fields={"subject_id": "example_mouse"})
            assert prepared["state"] == "READY"
            failed_history = next(item for item in prepared["session_history"]
                                  if item["session_id"] == failed["session_id"])
            assert failed_history["control_state_path"] == failed["control_state_path"]
            assert Path(failed_history["control_state_path"]).is_file()
            snapshot = client.request({"command": "status"})["status"]
            assert_new_control_state_path(snapshot)
            session_id = snapshot["session_id"]

            start_ack = control("start", "real-start-1")
            assert start_ack["ack"]["schema_version"] == 1
            assert start_ack["ack"]["request_id"] == "real-start-1"
            assert start_ack["ack"]["ok"] is True
            duplicate_start = control("start", "real-start-1")
            assert duplicate_start["ack"] == start_ack["ack"]
            engine_duplicate_start = _rpc(snapshot["control_endpoint"],
                                          {"schema_version": 1, "request_id": "real-start-1",
                                           "command": "start"})
            assert engine_duplicate_start == start_ack["ack"]
            running = wait_engine_state(client, "RUNNING")
            assert running["elapsed_s"] >= 0
            assert running["eta_s"] is None or 0 <= running["eta_s"] <= 10
            assert running["capabilities"].get("start") is False
            assert running["capabilities"].get("stop") is True
            simulation_session = output_root / session_id
            _wait_until(lambda: (simulation_session / "selected_image_table.json").is_file(),
                        message="VStim did not write its selected image table")
            selected_images = json.loads(
                (simulation_session / "selected_image_table.json").read_text()
            )["images"]
            selected_filenames = {item["image"] for item in selected_images}
            observed = {"natural_image": False, "gray_after_image": False}

            def observe_stimulus_transitions():
                telemetry = _http(web_port, "/api/state")[1]
                stimulus = telemetry.get("stimulus_state") or {}
                if telemetry.get("session_id") != session_id or telemetry.get("telemetry_freshness") != "FRESH":
                    return False
                if stimulus.get("stimulus_kind") == "natural_image":
                    filename = stimulus.get("image_filename")
                    assert filename in selected_filenames
                    assert stimulus.get("image_id") == Path(filename).stem
                    assert isinstance(stimulus.get("sequence_index"), int)
                    observed["natural_image"] = True
                    status, mimetype, image_bytes = _http_bytes(
                        web_port, f"/api/stimulus-image/{session_id}/{filename}"
                    )
                    assert status == 200 and mimetype == "image/png"
                    assert image_bytes == (natural_image_dir / filename).read_bytes()
                elif observed["natural_image"] and stimulus.get("stimulus_kind") == "gray":
                    assert stimulus.get("image_id") is None
                    assert stimulus.get("image_filename") is None
                    observed["gray_after_image"] = True
                return all(observed.values())

            _wait_until(observe_stimulus_transitions, timeout=8,
                        message="Natural Images telemetry missed image identity or gray transition")
            first_elapsed = running["elapsed_s"]
            time.sleep(0.15)
            progressed = client.request({"command": "status"})["status"]
            assert progressed["elapsed_s"] >= first_elapsed
            illegal = _rpc(progressed["control_endpoint"],
                           {"schema_version": 1, "request_id": "illegal-start-2", "command": "start"})
            assert illegal["schema_version"] == 1 and illegal["request_id"] == "illegal-start-2"
            assert illegal["ok"] is False and illegal["error"]["code"] == "invalid_state"

            stop_web()
            stale_telemetry_port = _port(socket.SOCK_DGRAM)
            start_web(stale_telemetry_port, stale_after_sec=0.2)
            disconnected_telemetry = _http(web_port, "/api/state")[1]
            assert disconnected_telemetry["telemetry_freshness"] == "NONE"
            assert disconnected_telemetry["supervisor"]["state"] == "RUNNING"
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as telemetry_socket:
                telemetry_socket.sendto(json.dumps({
                    "type": "state", "session_id": session_id,
                    "protocol": "natural-images", "phase": "RUNNING",
                }).encode("utf-8"), ("127.0.0.1", stale_telemetry_port))
            _wait_until(
                lambda: _http(web_port, "/api/state")[1]["telemetry_freshness"] == "FRESH",
                message="injected software telemetry did not become fresh",
            )
            time.sleep(0.3)
            assert _http(web_port, "/api/state")[1]["telemetry_freshness"] == "STALE"
            client = restart_supervisor()
            adopted_running = client.request({"command": "status"})["status"]
            assert adopted_running["state"] == "RUNNING"
            assert adopted_running["session_id"] == session_id
            assert control("start", "real-start-1")["ack"] == start_ack["ack"]
            stop_ack = control("stop", "real-stop-1")
            assert stop_ack["ack"]["schema_version"] == 1
            assert stop_ack["ack"]["request_id"] == "real-stop-1"
            assert stop_ack["ack"]["state"] == "FINISHING"
            duplicate_stop = control("stop", "real-stop-1")
            assert duplicate_stop["ack"] == stop_ack["ack"]
            final_status_path = output_root / session_id / "final_status.json"
            _wait_until(final_status_path.is_file, message="VStim did not persist final stop status")
            process_after_stop = wait_exit_or_error(client, "COMPLETE")
            final_artifact = json.loads(final_status_path.read_text())
            assert final_artifact["status"] == "COMPLETE"
            demo_status = final_artifact.get("natural_image_demo", {})
            assert demo_status.get("simulated") is True, sorted(final_artifact)
            assert demo_status.get("display_backend") == "simulated"
            assert demo_status.get("physical_kms_trace") is False
            assert demo_status.get("gpio_used") == []
            assert demo_status.get("camera_manifest") == []
            assert "audio_coexistence" not in final_artifact
            session_root = final_status_path.parent
            manifest = json.loads((session_root / "session_manifest.json").read_text())
            assert manifest.get("simulated") is True, sorted(manifest)
            assert manifest.get("display_backend") == "simulated"
            operator_metadata = json.loads((session_root / "operator_control.json").read_text())
            assert operator_metadata["stop"]["request_id"] == "real-stop-1"
            assert process_after_stop["state"] == "COMPLETE"
            assert process_after_stop["process_running"] is False
            stop_state = json.loads(Path(process_after_stop["control_state_path"]).read_text())
            assert stop_state["terminal"] is True and stop_state["lifecycle"] == "COMPLETE"
            assert stop_state["reason"] == "operator_stop"
            assert stop_state["terminal_request_id"] == "real-stop-1"
            assert not Path(process_after_stop["control_endpoint"]).exists()
            for log_path in (process_after_stop["stdout_path"], process_after_stop["stderr_path"]):
                assert Path(log_path).is_file()

            # Flask can restart after the engine exits; it reads the saved engine terminal state.
            stop_web()
            start_web(_port(socket.SOCK_DGRAM))
            final_api = _http(web_port, "/api/state")[1]["supervisor"]
            assert final_api["state"] == "COMPLETE"
            assert final_api["session_id"] == session_id
            assert control("stop", "real-stop-1")["ack"] == stop_ack["ack"]

            # READY abort has no acquisition output; running abort finalizes an aborted session.
            ready_abort_task = control("prepare", "abort-ready-prepare", protocol_id="natural_images",
                                       preset_id="operator_simulation",
                                       fields={"subject_id": "example_mouse"})
            assert_new_control_state_path(ready_abort_task)
            ready_abort = control("abort", "real-abort-ready-1")
            assert ready_abort["ack"]["schema_version"] == 1
            assert ready_abort["ack"]["request_id"] == "real-abort-ready-1"
            assert control("abort", "real-abort-ready-1")["ack"] == ready_abort["ack"]
            aborted_ready = wait_exited_state(client, "ABORTED")
            assert aborted_ready["session_id"] == ready_abort_task["session_id"]
            assert not (output_root / ready_abort_task["session_id"]).exists()
            ready_abort_file = json.loads(Path(aborted_ready["control_state_path"]).read_text())
            assert ready_abort_file["lifecycle"] == "ABORTED"
            assert ready_abort_file["terminal_request_id"] == "real-abort-ready-1"

            running_abort_task = control("prepare", "abort-running-prepare", protocol_id="natural_images",
                                         preset_id="operator_simulation",
                                         fields={"subject_id": "example_mouse"})
            assert_new_control_state_path(running_abort_task)
            control("start", "real-start-abort")
            wait_engine_state(client, "RUNNING")
            running_abort = control("abort", "real-abort-running-1")
            assert running_abort["ack"]["schema_version"] == 1
            assert running_abort["ack"]["request_id"] == "real-abort-running-1"
            assert control("abort", "real-abort-running-1")["ack"] == running_abort["ack"]
            final_abort = output_root / running_abort_task["session_id"] / "final_status.json"
            _wait_until(final_abort.is_file, message="VStim did not persist final abort status")
            assert json.loads(final_abort.read_text())["status"] == "ABORTED"
            abort_metadata = json.loads((final_abort.parent / "operator_control.json").read_text())
            assert abort_metadata["abort"]["request_id"] == "real-abort-running-1"
            # Replace the supervisor during terminal cleanup; registry changes affect only future launches.
            registry_path.write_text(json.dumps({"schema_version": 1, "protocols": []}))
            aborted_pid = running_abort_task["pid"]
            _stop(supervisor)
            supervisor = None

            def engine_exited():
                identity = _process_identity(aborted_pid)
                return identity is None or identity["process_state"] == "Z"

            _wait_until(engine_exited, timeout=8,
                        message="VStim did not exit while its supervisor was down")
            client = start_supervisor()
            after_abort = wait_exited_state(client, "ABORTED")
            assert after_abort["state"] == "ABORTED"
            abort_state = json.loads(Path(after_abort["control_state_path"]).read_text())
            assert abort_state["terminal"] is True and abort_state["lifecycle"] == "ABORTED"
            assert abort_state["terminal_request_id"] == "real-abort-running-1"
            assert after_abort["terminal_request_id"] == "real-abort-running-1"
            assert not Path(after_abort["control_endpoint"]).exists()
            assert control("abort", "real-abort-running-1")["ack"] == running_abort["ack"]

            # A short five-image simulation also reaches an engine-authored COMPLETE naturally.
            registry_path.write_text(good_registry)
            client = restart_supervisor()
            natural_task = control("prepare", "natural-completion-prepare", protocol_id="natural_images",
                                   preset_id="operator_simulation", fields={"subject_id": "example_mouse"})
            assert_new_control_state_path(natural_task)
            control("start", "natural-completion-start")
            natural_complete = wait_exited_state(client, "COMPLETE", timeout=45)
            natural_state = json.loads(Path(natural_complete["control_state_path"]).read_text())
            assert natural_state["terminal"] is True and natural_state["lifecycle"] == "COMPLETE"
            assert natural_state["reason"] == "natural_completion"
            assert natural_state["terminal_request_id"] is None
            assert natural_complete["session_id"] == natural_task["session_id"]
            assert not Path(natural_complete["control_endpoint"]).exists()
        finally:
            if client is not None:
                try:
                    def terminal_status():
                        status = client.request({"command": "status"})["status"]
                        return status if status["state"] in ("COMPLETE", "ERROR", "ABORTED") else None

                    current = client.request({"command": "status"})["status"]
                    if current["state"] in ("PREPARING", "READY", "RUNNING", "FINISHING"):
                        client.request({"command": "control", "action": "abort",
                                       "request_id": "test-cleanup-" + uuid.uuid4().hex})
                    _wait_until(terminal_status, timeout=12,
                                message="experiment did not reach terminal cleanup")
                except (SupervisorError, AssertionError, OSError):
                    pass
            stop_web()
            _stop(supervisor)
            supervisor_log.close()
            web_log.close()

