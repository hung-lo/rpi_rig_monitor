"""Persistent local process supervisor and Unix-socket experiment control client."""

import argparse
import datetime
import fcntl
import json
import os
import socket
import stat
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .registry import RegistryError, default_registry
from .profiles import ProfileError, ProfileStore

ACTIVE_STATES = ("PREPARING", "READY", "RUNNING", "FINISHING")
TERMINAL_STATES = ("COMPLETE", "ERROR", "ABORTED")
CONTROL_ACTIONS = ("start", "stop", "abort", "bait_left", "bait_right")
SUPERVISOR_SCHEMA_VERSION = 1
ENGINE_CONTROL_SCHEMA_VERSION = 1


class SupervisorError(Exception):
    pass


def default_state_dir():
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "rpi-rig-monitor"


def _new_record():
    return {
        "supervisor_schema_version": SUPERVISOR_SCHEMA_VERSION,
        "session_id": None, "engine_session_id": None, "protocol": None,
        "protocol_id": None, "preset_id": None, "engine_protocol_id": None,
        "control_schema_version": 1,
        "protocol_display_name": None, "preset_display_name": None, "adapter_type": None,
        "subject_id": None, "working_directory": None, "launch_metadata": {},
        "parameters": {}, "profile_identity": None, "parameter_overrides": {},
        "control_state_path": None, "control_state_artifact_type": None,
        "session_metadata_path": None, "control_instance_id": None,
        "terminal_reason": None, "terminal_command": None, "terminal_request_id": None,
        "engine_error_summary": None, "session_output_path": None,
        "control_state_updated_at_utc": None, "control_state_updated_monotonic_ns": None,
        "engine_lifecycle_metadata": {},
        "lifecycle_source": None, "recovery_diagnostic": None,
        "state": "IDLE", "elapsed_s": None,
        "eta_s": None, "pid": None, "command": None, "config_path": None,
        "started_at": None, "started_at_utc": None, "started_monotonic": None,
        "process_starttime": None, "process_boot_id": None,
        "process_group_id": None, "process_session_id": None,
        "control_endpoint": None, "exit_status": None,
        "stdout_path": None, "stderr_path": None, "capabilities": {},
        "recent_events": [], "process_running": False, "control_connected": False,
        "recovery_state": "IDLE", "session_history": [], "prepare_request_id": None,
        "control_acks": {},
    }


def _process_identity(pid):
    """Return Linux procfs identity; field 22 distinguishes a reused PID."""
    try:
        stat = Path("/proc", str(int(pid)), "stat").read_text()
        fields = stat[stat.rfind(")") + 2:].split()
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return {"process_state": fields[0], "process_group_id": int(fields[2]), "process_session_id": int(fields[3]),
                "process_starttime": int(fields[19]), "process_boot_id": boot_id}
    except (OSError, ValueError, IndexError, TypeError):
        return None


def _read_control_state_document(path):
    try:
        state_path = Path(path)
        info = state_path.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()):
            return None, "file ownership or permissions are invalid"
        if info.st_size > 65536:
            return None, "file is oversized"
        with state_path.open("r") as stream:
            document = json.load(stream)
    except FileNotFoundError:
        return None, "file is missing"
    except (OSError, ValueError, TypeError):
        return None, "file is unreadable or malformed"
    if not isinstance(document, dict):
        return None, "document is not an object"
    if (type(document.get("schema_version")) is not int
            or document["schema_version"] != ENGINE_CONTROL_SCHEMA_VERSION):
        return None, "unsupported schema version"
    return document, None


def _write_json_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(str(path.parent), 0o700)
    fd, name = tempfile.mkstemp(prefix="session-metadata-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, str(path))
        directory = os.open(str(path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass


def _rpc(path, request, timeout=2.0):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
        client.sendall(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
        with client.makefile("rb") as stream:
            response = stream.readline(65537)
        if not response or len(response) > 65536:
            raise SupervisorError("empty or oversized local control response")
        try:
            decoded = json.loads(response.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise SupervisorError("invalid local control response")
        if not isinstance(decoded, dict):
            raise SupervisorError("invalid local control response")
        return decoded
    finally:
        client.close()


class SupervisorClient:
    """Client for the independently managed supervisor process."""

    def __init__(self, state_dir=None, socket_path=None):
        self.state_dir = Path(state_dir or default_state_dir()).expanduser().resolve()
        self.socket_path = Path(socket_path or self.state_dir / "supervisor.sock").expanduser().resolve()
        if len(os.fsencode(str(self.socket_path))) >= 108:
            raise ValueError("Unix socket path must be shorter than 108 bytes")

    def request(self, request):
        try:
            return _rpc(self.socket_path, request)
        except (OSError, ValueError) as exc:
            raise SupervisorError(f"supervisor unavailable: {exc}")

    def connect(self):
        """Check the configured service; never start or recover it implicitly."""
        return self.request({"command": "status"})


class SupervisorServer:
    def __init__(self, state_dir, socket_path, telemetry_port=5055, registry=None):
        self.state_dir = Path(state_dir).resolve()
        self.socket_path = Path(socket_path).resolve()
        self.telemetry_port = telemetry_port
        self.registry = registry or default_registry()
        self.profile_store = ProfileStore(self.state_dir / "profiles", self.registry)
        self.state_file = self.state_dir / "supervisor-state.json"
        self.lock_file = self.state_dir / "supervisor.lock"
        self.log_dir = self.state_dir / "logs"
        self.process = None
        self.stop_requested = False
        self._last_status_save = 0.0
        self.record = self._load_record()
        self._adopting = self.record["state"] in ACTIVE_STATES and self.record.get("session_id") is not None

    def _load_record(self):
        try:
            with self.state_file.open("r") as stream:
                saved = json.load(stream)
            if not isinstance(saved, dict):
                return _new_record()
            record = _new_record()
            record.update(saved)
            for key in ("capabilities", "control_acks"):
                if not isinstance(record.get(key), dict):
                    record[key] = {}
            for key in ("recent_events", "session_history"):
                if not isinstance(record.get(key), list):
                    record[key] = []
            record["supervisor_schema_version"] = SUPERVISOR_SCHEMA_VERSION
            if record.get("protocol_id") is None:
                record["protocol_id"] = record.get("protocol")
            if record.get("protocol") is None:
                record["protocol"] = record.get("protocol_id")
            if record.get("engine_protocol_id") is None:
                record["engine_protocol_id"] = record.get("protocol_id")
            return record
        except (OSError, ValueError, TypeError):
            return _new_record()

    def _save(self):
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="supervisor-", suffix=".json", dir=str(self.state_dir))
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.record, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            os.replace(name, str(self.state_file))
            directory = os.open(str(self.state_dir), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self._last_status_save = time.monotonic()
        finally:
            try:
                os.unlink(name)
            except OSError:
                pass

    def _task_request(self, request, timeout=1.0):
        endpoint = self.record.get("control_endpoint")
        if not endpoint:
            raise SupervisorError("experiment control endpoint is missing")
        request = dict(request)
        request.setdefault("schema_version", ENGINE_CONTROL_SCHEMA_VERSION)
        request_id = request.setdefault("request_id", str(uuid.uuid4()))
        if (type(request["schema_version"]) is not int or
                request["schema_version"] != ENGINE_CONTROL_SCHEMA_VERSION):
            raise SupervisorError("unsupported experiment control schema version")
        if not isinstance(request_id, str) or not request_id:
            raise SupervisorError("experiment control request_id is required")
        response = _rpc(endpoint, request, timeout=timeout)
        if (type(response.get("schema_version")) is not int or
                response["schema_version"] != ENGINE_CONTROL_SCHEMA_VERSION):
            raise SupervisorError("experiment control response has an invalid schema version")
        if response.get("request_id") != request_id:
            raise SupervisorError("experiment control response request_id mismatch")
        if response.get("ok") is not True:
            error = response.get("error", "experiment rejected command")
            if isinstance(error, dict):
                error = error.get("message", "experiment rejected command")
            raise SupervisorError(str(error))
        return response

    def _learn_control_instance_from_state_file(self):
        if self.record.get("control_instance_id") or not self.record.get("control_state_path"):
            return False
        document, error = _read_control_state_document(self.record["control_state_path"])
        if error:
            return False
        if (document.get("session_id") != (self.record.get("engine_session_id") or self.record.get("session_id"))
                or document.get("protocol") != (self.record.get("engine_protocol_id") or self.record.get("protocol"))
                or document.get("artifact_type") != self.record.get("control_state_artifact_type")
                or type(document.get("process_id")) is not int
                or document["process_id"] != self.record.get("pid")
                or not isinstance(document.get("control_instance_id"), str)
                or not document["control_instance_id"]):
            return False
        self.record["control_instance_id"] = document["control_instance_id"]
        return True

    def _recover_terminal_state(self):
        path = self.record.get("control_state_path")
        if not path:
            return False

        def reject(reason):
            self.record["recovery_diagnostic"] = "durable control state rejected: " + reason
            return False

        expected_instance = self.record.get("control_instance_id")
        if not isinstance(expected_instance, str) or not expected_instance:
            return reject("control instance identity was not learned from the live engine")
        document, error = _read_control_state_document(path)
        if error:
            return reject(error)
        required_fields = {
            "session_id", "protocol", "state", "lifecycle", "terminal", "control_instance_id",
            "process_id", "prepared_at_utc", "prepared_monotonic_ns", "ready_at_utc",
            "ready_monotonic_ns", "start", "stop", "abort", "terminal_command",
            "terminal_request_id", "updated_at_utc", "updated_monotonic_ns", "terminal_at_utc",
            "terminal_monotonic_ns", "reason", "error_summary", "session_output_path",
        }
        if not required_fields.issubset(document):
            return reject("required state fields are missing")
        expected_session = self.record.get("engine_session_id") or self.record.get("session_id")
        expected_protocol = self.record.get("engine_protocol_id") or self.record.get("protocol")
        if document.get("artifact_type") != self.record.get("control_state_artifact_type"):
            return reject("artifact type mismatch")
        if document.get("session_id") != expected_session:
            return reject("session identity mismatch")
        if document.get("protocol") != expected_protocol:
            return reject("protocol identity mismatch")
        if document.get("control_instance_id") != expected_instance:
            return reject("control instance identity mismatch")
        pid = self.record.get("pid")
        if type(document.get("process_id")) is not int or document["process_id"] != pid:
            return reject("engine PID mismatch")
        lifecycle = document.get("lifecycle")
        if lifecycle != document.get("state"):
            return reject("lifecycle fields disagree")
        if document.get("terminal") is not True:
            return reject("snapshot is not terminal")
        if lifecycle not in TERMINAL_STATES:
            return reject("unknown terminal lifecycle")
        if (not isinstance(document.get("prepared_at_utc"), str)
                or type(document.get("prepared_monotonic_ns")) is not int
                or not isinstance(document.get("updated_at_utc"), str)
                or type(document.get("updated_monotonic_ns")) is not int
                or not isinstance(document.get("terminal_at_utc"), str)
                or type(document.get("terminal_monotonic_ns")) is not int):
            return reject("terminal timestamps are invalid")
        ready_at, ready_monotonic = document.get("ready_at_utc"), document.get("ready_monotonic_ns")
        if ((ready_at is None) != (ready_monotonic is None)
                or (ready_at is not None and (not isinstance(ready_at, str)
                                               or type(ready_monotonic) is not int))):
            return reject("READY timestamps are invalid")
        for key in ("start", "stop", "abort"):
            metadata = document.get(key)
            if metadata is not None and (
                    not isinstance(metadata, dict)
                    or not isinstance(metadata.get("request_id"), str)
                    or not metadata.get("request_id")):
                return reject("lifecycle request metadata is invalid")
        if not isinstance(document.get("reason"), str) or not document["reason"]:
            return reject("terminal reason is missing")
        for key in ("reason", "terminal_command", "terminal_request_id", "error_summary",
                    "session_output_path"):
            if document.get(key) is not None and not isinstance(document[key], str):
                return reject("terminal metadata is invalid")
        terminal_command = document.get("terminal_command")
        terminal_request_id = document.get("terminal_request_id")
        if terminal_command not in (None, "stop", "abort"):
            return reject("terminal command is invalid")
        if terminal_command is None:
            if terminal_request_id is not None:
                return reject("terminal request has no command")
        elif (not isinstance(document.get(terminal_command), dict)
              or document[terminal_command].get("request_id") != terminal_request_id):
            return reject("terminal request metadata does not match")

        self.record.update({
            "state": lifecycle,
            "control_instance_id": expected_instance,
            "lifecycle_source": "engine_control_state_file",
            "recovery_state": "TERMINAL_RECOVERED",
            "recovery_diagnostic": None,
            "control_connected": False,
            "capabilities": {},
            "terminal_reason": document.get("reason"),
            "terminal_command": document.get("terminal_command"),
            "terminal_request_id": document.get("terminal_request_id"),
            "engine_error_summary": document.get("error_summary"),
            "session_output_path": document.get("session_output_path"),
            "control_state_updated_at_utc": document["updated_at_utc"],
            "control_state_updated_monotonic_ns": document["updated_monotonic_ns"],
            "engine_lifecycle_metadata": {key: document.get(key) for key in ("start", "stop", "abort")},
        })
        return True

    def _refresh(self):
        if not self.record.get("session_id"):
            return
        previous = (self.record["state"], self.record.get("exit_status"),
                    self.record.get("process_running"), self.record.get("control_connected"),
                    self.record.get("recovery_state"), tuple(self.record.get("capabilities", {}).items()),
                    tuple(self.record.get("recent_events", [])[:1]),
                    self.record.get("control_instance_id"), self.record.get("lifecycle_source"),
                    self.record.get("recovery_diagnostic"), self.record.get("terminal_request_id"))
        identity = _process_identity(self.record.get("pid"))
        expected = (self.record.get("process_starttime"), self.record.get("process_boot_id"),
                    self.record.get("process_group_id"), self.record.get("process_session_id"))
        actual = None if identity is None else (identity["process_starttime"], identity["process_boot_id"],
                                                identity["process_group_id"], identity["process_session_id"])
        if identity is None or identity.get("process_state") == "Z":
            self.record["process_running"] = False
            self.record["control_connected"] = False
            self.record["capabilities"] = {}
            self.record["recovery_state"] = "EXITED"
            if self.process is not None:
                self.record["exit_status"] = self.process.poll()
            recovered = self._recover_terminal_state()
            if not recovered and self.record["state"] in ACTIVE_STATES:
                self.record["state"] = "ERROR"
                self.record["lifecycle_source"] = "supervisor_process_exit"
                self.record["recent_events"].insert(0, {"event": "experiment_exit_without_final_state"})
                del self.record["recent_events"][50:]
            self._save_if_changed(previous)
            return
        if None in expected or actual != expected:
            self.record["process_running"] = False
            self.record["control_connected"] = False
            self.record["capabilities"] = {}
            if self._recover_terminal_state():
                self.record["recovery_diagnostic"] = "terminal lifecycle recovered after saved PID identity expired"
            else:
                self.record["recovery_state"] = "STALE"
            if self.record["state"] in ACTIVE_STATES and self.record.get("lifecycle_source") != "engine_control_state_file":
                self.record["state"] = "ERROR"
                self.record["lifecycle_source"] = "supervisor_process_exit"
            self.record["recent_events"].insert(0, {"event": "saved_process_identity_mismatch"})
            del self.record["recent_events"][50:]
            self._save_if_changed(previous)
            return
        self.record["process_running"] = True
        try:
            response = self._task_request({"command": "status"})
            status = response.get("status", {})
            if (not isinstance(status, dict) or status.get("session_id") != self.record["session_id"] or
                    status.get("protocol") != (self.record.get("engine_protocol_id") or self.record["protocol"]) or
                    status.get("state") not in ACTIVE_STATES + TERMINAL_STATES + ("IDLE",)):
                self.record["control_connected"] = False
                self.record["recovery_state"] = "CONTROL_MISMATCH"
                self._save_if_changed(previous)
                return
            instance_id = status.get("control_instance_id")
            if self.record.get("control_state_path") and (
                    type(status.get("control_schema_version")) is not int
                    or status["control_schema_version"] != ENGINE_CONTROL_SCHEMA_VERSION
                    or not isinstance(instance_id, str) or not instance_id):
                self.record["control_connected"] = False
                self.record["recovery_state"] = "CONTROL_MISMATCH"
                self.record["recovery_diagnostic"] = "live engine omitted durable-state identity"
                self._save_if_changed(previous)
                return
            if self.record.get("control_instance_id") and instance_id != self.record["control_instance_id"]:
                self.record["control_connected"] = False
                self.record["recovery_state"] = "CONTROL_MISMATCH"
                self.record["recovery_diagnostic"] = "live engine control instance identity mismatch"
                self._save_if_changed(previous)
                return
            self.record["control_connected"] = True
            self.record["engine_session_id"] = status.get("session_id")
            if isinstance(instance_id, str) and instance_id:
                self.record["control_instance_id"] = instance_id
            self.record["lifecycle_source"] = "live_control_socket"
            self.record["recovery_diagnostic"] = None
            if self._adopting:
                self.record["recovery_state"] = "ADOPTED"
            elif self.record.get("recovery_state") == "CONTROL_UNAVAILABLE":
                self.record["recovery_state"] = "RECOVERED"
            elif self.record.get("recovery_state") != "ADOPTED":
                self.record["recovery_state"] = "CONNECTED"
            self._adopting = False
            for key in ("state", "elapsed_s", "eta_s"):
                if key in status:
                    self.record[key] = status[key]
            if self.record["state"] not in TERMINAL_STATES:
                for key in ("terminal_reason", "terminal_command", "terminal_request_id",
                            "engine_error_summary", "control_state_updated_at_utc",
                            "control_state_updated_monotonic_ns"):
                    self.record[key] = None
                self.record["engine_lifecycle_metadata"] = {}
            capabilities = status.get("capabilities", {})
            self.record["capabilities"] = capabilities if isinstance(capabilities, dict) else {}
            event = status.get("recent_event")
            if event and (not self.record["recent_events"] or self.record["recent_events"][0] != event):
                self.record["recent_events"].insert(0, event)
                del self.record["recent_events"][50:]
            if self.record["state"] in TERMINAL_STATES:
                if self.process is not None:
                    try:
                        self.record["exit_status"] = self.process.wait(timeout=1.0)
                        self.record["process_running"] = False
                    except subprocess.TimeoutExpired:
                        self.record["exit_status"] = status.get("exit_status")
                        self.record["process_running"] = True
                else:
                    self.record["exit_status"] = status.get("exit_status")
                    self.record["process_running"] = _process_identity(self.record.get("pid")) is not None
        except OSError:
            self.record["control_connected"] = False
            self._learn_control_instance_from_state_file()
            if not self._recover_terminal_state():
                self.record["recovery_state"] = "CONTROL_UNAVAILABLE"
        except SupervisorError as exc:
            self.record["control_connected"] = False
            self.record["recovery_state"] = "CONTROL_MISMATCH"
            self.record["recovery_diagnostic"] = "control response rejected: " + str(exc)[:200]
        self._save_if_changed(previous)

    def _save_if_changed(self, previous):
        current = (self.record["state"], self.record.get("exit_status"),
                   self.record.get("process_running"), self.record.get("control_connected"),
                   self.record.get("recovery_state"), tuple(self.record.get("capabilities", {}).items()),
                   tuple(self.record.get("recent_events", [])[:1]),
                   self.record.get("control_instance_id"), self.record.get("lifecycle_source"),
                   self.record.get("recovery_diagnostic"), self.record.get("terminal_request_id"))
        if current != previous or time.monotonic() - self._last_status_save >= 1.0:
            self._save()

    def snapshot(self):
        self._refresh()
        result = dict(self.record)
        result["capabilities"] = dict(self.record.get("capabilities", {}))
        result["recent_events"] = list(self.record.get("recent_events", []))
        result["session_history"] = list(self.record.get("session_history", []))
        return result

    def prepare(self, protocol_id, request_id=None, preset_id="standard", runtime_fields=None,
                parameters=None, profile_ref=None):
        if runtime_fields is None:
            runtime_fields = {}
        if parameters is None:
            parameters = {}
        if profile_ref is not None and (
                not isinstance(profile_ref, dict) or set(profile_ref) != {"profile_id", "version"}
                or not isinstance(profile_ref.get("profile_id"), str)
                or type(profile_ref.get("version")) is not int):
            raise SupervisorError("invalid profile reference")
        profile_identity = None
        profile_parameters = {}
        if profile_ref is not None:
            try:
                saved_profile = self.profile_store.get_profile(
                    protocol_id, preset_id, profile_ref["profile_id"], profile_ref["version"]
                )
            except ProfileError as exc:
                raise SupervisorError(str(exc))
            profile_identity = {"profile_id": saved_profile["profile_id"],
                                "name": saved_profile["name"], "version": saved_profile["version"]}
            profile_parameters = saved_profile["parameters"]
        if not isinstance(runtime_fields, dict):
            raise SupervisorError("runtime fields must be an object")
        if not isinstance(parameters, dict):
            raise SupervisorError("session parameters must be an object")
        try:
            resolved_parameters = self.registry.validate_parameters(protocol_id, preset_id, parameters)
        except RegistryError as exc:
            raise SupervisorError(str(exc))
        self._refresh()
        if self.record["state"] in ACTIVE_STATES or self.record.get("process_running"):
            if (self.record.get("prepare_request_id") == request_id
                    and self.record.get("protocol_id") == protocol_id
                    and self.record.get("preset_id") == preset_id
                    and self.record.get("launch_metadata", {}).get("runtime_fields", {}) == (runtime_fields or {})
                    and self.record.get("parameters", {}) == resolved_parameters
                    and self.record.get("profile_identity") == profile_identity):
                return self.snapshot()
            raise SupervisorError("an experiment session is already active")
        control_acks = self.record.get("control_acks", {})
        session_id = "session-" + uuid.uuid4().hex[:16]
        endpoint = str(self.state_dir / (session_id + ".sock"))
        state_path = self.state_dir / "control-state" / (session_id + ".json")
        metadata_path = self.state_dir / "session-metadata" / (session_id + ".json")
        stdout_path = self.log_dir / (session_id + ".stdout.log")
        stderr_path = self.log_dir / (session_id + ".stderr.log")
        try:
            parameter_overrides = {
                key: value for key, value in resolved_parameters.items()
                if (value != profile_parameters.get(key, value) if profile_identity else
                    value != self.registry.validate_parameters(protocol_id, preset_id, {}).get(key))
            }
            launch = self.registry.resolve(protocol_id, preset_id, session_id, endpoint,
                                           self.telemetry_port, runtime_fields or {}, str(state_path),
                                           resolved_parameters, profile_identity, parameter_overrides,
                                           str(metadata_path))
        except (RegistryError, ProfileError) as exc:
            raise SupervisorError(str(exc))
        if launch.control_state_path:
            state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(str(state_path.parent), 0o700)
            if state_path.exists() or state_path.is_symlink():
                raise SupervisorError("generated control-state path already exists; refusing to reuse it")
        if launch.session_metadata_path:
            if Path(launch.session_metadata_path).exists() or Path(launch.session_metadata_path).is_symlink():
                raise SupervisorError("generated session-metadata path already exists; refusing to reuse it")
            _write_json_atomic(launch.session_metadata_path, {
                "schema_version": 1,
                "session_id": session_id,
                "protocol_id": launch.protocol_id,
                "engine_protocol_id": launch.engine_protocol_id,
                "preset_id": launch.preset_id,
                "subject_id": launch.runtime_fields.get("subject_id"),
                "profile": dict(launch.profile_identity) if launch.profile_identity else None,
                "parameters": dict(launch.parameters or {}),
                "parameter_overrides": dict(launch.parameter_overrides or {}),
            })
        command = list(launch.argv)
        self.log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        history = list(self.record.get("session_history", []))
        if self.record.get("session_id"):
            history.insert(0, {key: self.record.get(key) for key in (
                "session_id", "protocol", "protocol_id", "preset_id", "protocol_display_name",
                "engine_protocol_id", "engine_session_id", "control_schema_version",
                "preset_display_name", "adapter_type", "subject_id",
                "parameters", "profile_identity", "parameter_overrides",
                "working_directory", "launch_metadata", "control_state_path", "control_state_artifact_type",
                "session_metadata_path",
                "control_instance_id",
                "terminal_reason", "terminal_command", "terminal_request_id", "engine_error_summary",
                "session_output_path", "control_state_updated_at_utc", "control_state_updated_monotonic_ns",
                "engine_lifecycle_metadata",
                "state", "pid", "process_starttime", "process_boot_id",
                "process_group_id", "process_session_id", "command", "config_path", "started_at_utc",
                "exit_status", "stdout_path", "stderr_path")})
        self.record = _new_record()
        self.record["session_history"] = history[:20]
        self.record["control_acks"] = control_acks
        self.record.update({"session_id": session_id, "protocol": protocol_id, "protocol_id": protocol_id,
                            "preset_id": preset_id, "engine_protocol_id": launch.engine_protocol_id,
                            "engine_session_id": None, "control_schema_version": 1,
                            "protocol_display_name": launch.protocol_display_name,
                            "preset_display_name": launch.preset_display_name, "adapter_type": launch.adapter_type,
                            "subject_id": launch.runtime_fields.get("subject_id"),
                            "parameters": dict(launch.parameters or {}),
                            "profile_identity": dict(launch.profile_identity) if launch.profile_identity else None,
                            "parameter_overrides": dict(launch.parameter_overrides or {}),
                            "working_directory": launch.cwd or str(Path(__file__).resolve().parents[1]),
                            "launch_metadata": {"runtime_fields": dict(launch.runtime_fields),
                                                "working_directory": launch.cwd,
                                                "config_path": launch.config_path,
                                                "parameters": dict(launch.parameters or {}),
                                                "parameter_overrides": dict(launch.parameter_overrides or {}),
                                                "profile_identity": dict(launch.profile_identity) if launch.profile_identity else None,
                                                "session_metadata_path": launch.session_metadata_path,
                                                "engine_protocol_id": launch.engine_protocol_id,
                                                "control_schema_version": 1,
                                                "control_state_path": launch.control_state_path,
                                                "control_state_artifact_type": launch.control_state_artifact_type},
                            "state": "PREPARING", "pid": None, "command": command,
                            "config_path": launch.config_path,
                            "control_state_path": launch.control_state_path,
                            "control_state_artifact_type": launch.control_state_artifact_type,
                            "session_metadata_path": launch.session_metadata_path,
                            "started_at": time.time(),
                            "started_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "started_monotonic": time.monotonic(), "control_endpoint": endpoint,
                            "prepare_request_id": request_id,
                            "stdout_path": str(stdout_path), "stderr_path": str(stderr_path)})
        self._save()
        try:
            with open(str(stdout_path), "ab") as stdout, open(str(stderr_path), "ab") as stderr:
                environment = os.environ.copy()
                environment.pop("RIG_MONITOR_CONTROL_TOKEN", None)
                environment.update(launch.env)
                self.process = subprocess.Popen(command, cwd=launch.cwd or str(Path(__file__).resolve().parents[1]),
                                                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                                close_fds=True, start_new_session=True, shell=False,
                                                env=environment)
        except OSError as exc:
            self.record["state"] = "ERROR"
            self.record["recent_events"].insert(0, {"event": "launch_failed", "error": str(exc)})
            self._save()
            raise SupervisorError(f"unable to launch experiment: {exc}")
        self.record["pid"] = self.process.pid
        identity = _process_identity(self.process.pid)
        if identity is None:
            self.process.terminate()
            self.record["state"] = "ERROR"
            self.record["exit_status"] = self.process.wait(timeout=2.0)
            self.record["recovery_state"] = "IDENTITY_UNAVAILABLE"
            self._save()
            raise SupervisorError("unable to verify launched process identity")
        self.record.update(identity)
        self.record["process_running"] = True
        self._save()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self._refresh()
                self.record["exit_status"] = self.process.returncode
                self._save()
                detail = self.record.get("engine_error_summary")
                message = "experiment exited during preparation"
                if detail:
                    message += ": " + detail
                raise SupervisorError(message)
            try:
                status = self._task_request({"command": "status"}).get("status", {})
                if (not isinstance(status, dict) or status.get("session_id") != session_id or
                        status.get("protocol") != launch.engine_protocol_id or
                        status.get("state") not in ACTIVE_STATES + TERMINAL_STATES + ("IDLE",)):
                    raise SupervisorError("experiment control endpoint identity mismatch")
                instance_id = status.get("control_instance_id")
                if launch.control_state_path and (
                        type(status.get("control_schema_version")) is not int
                        or status["control_schema_version"] != ENGINE_CONTROL_SCHEMA_VERSION
                        or not isinstance(instance_id, str) or not instance_id):
                    raise SupervisorError("experiment status omitted durable-state identity")
                self.record["state"] = status.get("state", "PREPARING")
                self.record["engine_session_id"] = status.get("session_id")
                if isinstance(instance_id, str) and instance_id:
                    self.record["control_instance_id"] = instance_id
                self.record["lifecycle_source"] = "live_control_socket"
                self.record["recovery_diagnostic"] = None
                capabilities = status.get("capabilities", {})
                self.record["capabilities"] = capabilities if isinstance(capabilities, dict) else {}
                self.record["control_connected"] = True
                self.record["recovery_state"] = "CONNECTED"
                self._save()
                if self.record["state"] == "READY":
                    return self.snapshot()
            except (OSError, SupervisorError):
                self._learn_control_instance_from_state_file()
            time.sleep(0.05)
        self.process.terminate()
        self.record["state"] = "ERROR"
        self.record["capabilities"] = {}
        self.record["exit_status"] = self.process.wait(timeout=2.0)
        self._save()
        raise SupervisorError("experiment did not become READY")

    def control(self, command, protocol_id=None, request_id=None, preset_id="standard", runtime_fields=None,
                parameters=None, profile_ref=None):
        if command not in CONTROL_ACTIONS + ("prepare",):
            raise SupervisorError("unsupported control action")
        request_id = request_id or str(uuid.uuid4())
        if runtime_fields is None:
            runtime_fields = {}
        if parameters is None:
            parameters = {}
        if not isinstance(runtime_fields, dict):
            raise SupervisorError("runtime fields must be an object")
        if not isinstance(parameters, dict):
            raise SupervisorError("session parameters must be an object")
        signature = [command, protocol_id, preset_id if command == "prepare" else None,
                     runtime_fields if command == "prepare" else {},
                     parameters if command == "prepare" else {},
                     profile_ref if command == "prepare" else None]
        cached = self.record.get("control_acks", {}).get(request_id)
        if cached:
            if cached.get("signature") != signature:
                raise SupervisorError("request_id was already used for a different command")
            if cached.get("error"):
                raise SupervisorError(cached["error"])
            if "result" in cached:
                return self.snapshot()
            return {"ack": cached["ack"], "status": self.snapshot()}
        if command == "prepare":
            result = self.prepare(protocol_id, request_id, preset_id, runtime_fields, parameters, profile_ref)
            self._remember_ack(request_id, signature, result=True)
            return result
        self._refresh()
        status = self.record
        if not status.get("control_connected"):
            raise SupervisorError("experiment process is alive but its control endpoint is unavailable; task left running")
        if command in ("bait_left", "bait_right"):
            if status.get("capabilities", {}).get(command) is not True:
                raise SupervisorError(f"{command} is not available in {status['state']}")
            response = self._task_request({"command": "deliver_bait", "channel": command[5:],
                                           "request_id": request_id})
        else:
            if status.get("capabilities", {}).get(command) is not True:
                raise SupervisorError(f"{command} is not available in {status['state']}")
            response = self._task_request({"command": command, "request_id": request_id})
        if response.get("ok") is not True:
            error = response.get("error", "experiment rejected command")
            self._remember_ack(request_id, signature, error=error)
            raise SupervisorError(error)
        if response.get("state") in ACTIVE_STATES + TERMINAL_STATES:
            self.record["state"] = response["state"]
        self._remember_ack(request_id, signature, ack=response)
        self._refresh()
        return {"ack": response, "status": self.snapshot()}

    def _remember_ack(self, request_id, signature, ack=None, result=None, error=None):
        acknowledgements = self.record.setdefault("control_acks", {})
        entry = {"signature": signature}
        if ack is not None:
            entry["ack"] = ack
        if result is not None:
            entry["result"] = result
        if error is not None:
            entry["error"] = error
        acknowledgements[request_id] = entry
        while len(acknowledgements) > 128:
            del acknowledgements[next(iter(acknowledgements))]
        self._save()

    def handle(self, request):
        command = request.get("command") if isinstance(request, dict) else None
        if command == "status":
            return {"ok": True, "status": self.snapshot()}
        if command == "protocols":
            return {"ok": True, "protocols": self.registry.public_metadata()}
        if command == "profiles":
            action = request.get("action")
            expected = {"command", "action", "protocol_id", "preset_id"}
            if action == "save":
                expected.update(("name", "parameters"))
            elif action == "update":
                expected.update(("profile_id", "version", "name", "parameters"))
            elif action == "delete":
                expected.update(("profile_id", "version"))
            elif action != "list":
                return {"ok": False, "error": "invalid profile action"}
            if set(request) != expected:
                return {"ok": False, "error": "invalid profile request"}
            try:
                scope = (request["protocol_id"], request["preset_id"])
                if action == "list":
                    return {"ok": True, "profiles": self.profile_store.list_profiles(*scope)}
                if action == "save":
                    result = self.profile_store.save_profile(*scope, request["name"], request["parameters"])
                elif action == "update":
                    result = self.profile_store.update_profile(
                        *scope, request["profile_id"], request["version"],
                        request["name"], request["parameters"])
                else:
                    result = self.profile_store.delete_profile(
                        *scope, request["profile_id"], request["version"])
                return {"ok": True, "result": result}
            except (ProfileError, RegistryError) as exc:
                return {"ok": False, "error": str(exc)}
        if command == "control":
            action = request.get("action")
            expected = {"command", "action", "request_id"}
            if action == "prepare":
                expected.update(("protocol_id", "preset_id", "fields"))
                optional = {"parameters", "profile_ref"}
                if set(request) - expected and not (set(request) - expected).issubset(optional):
                    return {"ok": False, "error": "invalid control request", "status": self.snapshot()}
            if (not expected.issubset(request) or
                    (action != "prepare" and set(request) != expected)):
                return {"ok": False, "error": "invalid control request", "status": self.snapshot()}
            try:
                return {"ok": True, "result": self.control(
                    action, request.get("protocol_id"), request.get("request_id"),
                    request.get("preset_id", "standard"), request.get("fields", {}),
                    request.get("parameters", {}), request.get("profile_ref"))}
            except SupervisorError as exc:
                request_id = request.get("request_id")
                if isinstance(request_id, str) and request_id and request_id not in self.record.get("control_acks", {}):
                    self._remember_ack(request_id, [action, request.get("protocol_id"),
                                                     request.get("preset_id") if action == "prepare" else None,
                                                     request.get("fields", {}) if action == "prepare" else {},
                                                     request.get("parameters", {}) if action == "prepare" else {},
                                                     request.get("profile_ref") if action == "prepare" else None],
                                       error=str(exc))
                return {"ok": False, "error": str(exc), "status": self.snapshot()}
        if command == "shutdown-supervisor":
            self.stop_requested = True
            return {"ok": True}
        return {"ok": False, "error": "unknown supervisor command"}

    def serve_forever(self):
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(str(self.state_dir), 0o700)
        self.log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.lock_file.open("a") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return 1
            try:
                self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                try:
                    os.unlink(str(self.socket_path))
                except OSError:
                    pass
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(str(self.socket_path))
                os.chmod(str(self.socket_path), 0o600)
                server.listen(8)
                server.settimeout(0.25)
                try:
                    while not self.stop_requested:
                        self._refresh()
                        try:
                            connection, _ = server.accept()
                        except TimeoutError:
                            continue
                        with connection:
                            connection.settimeout(3.0)
                            request = _read_request(connection)
                            try:
                                response = self.handle(request)
                            except (OSError, SupervisorError, ValueError, TypeError, KeyError,
                                    subprocess.TimeoutExpired) as exc:
                                response = {"ok": False, "error": str(exc)}
                            connection.sendall(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
                finally:
                    server.close()
                    try:
                        os.unlink(str(self.socket_path))
                    except OSError:
                        pass
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return 0


def _read_request(connection):
    data = bytearray()
    while len(data) <= 65536:
        chunk = connection.recv(4096)
        if not chunk or b"\n" in chunk:
            data.extend(chunk.split(b"\n", 1)[0])
            try:
                value = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}
            return value if isinstance(value, dict) else {}
        data.extend(chunk)
    return {}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    parser.add_argument("--socket-path", type=Path)
    parser.add_argument("--telemetry-port", type=int, default=5055)
    parser.add_argument("--protocol-registry", type=Path,
                        default=os.environ.get("RIG_MONITOR_PROTOCOL_REGISTRY"))
    args = parser.parse_args(argv)
    socket_path = args.socket_path or args.state_dir / "supervisor.sock"
    return SupervisorServer(args.state_dir, socket_path, args.telemetry_port,
                            registry=default_registry(args.protocol_registry)).serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
