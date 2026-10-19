"""Persistent local process supervisor and Unix-socket experiment control client."""

import argparse
import datetime
import fcntl
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ACTIVE_STATES = ("PREPARING", "READY", "RUNNING", "FINISHING")
TERMINAL_STATES = ("COMPLETE", "ERROR", "ABORTED")
CONTROL_ACTIONS = ("start", "stop", "abort", "bait_left", "bait_right")
PROTOCOLS = {
    "fake": {
        "display_name": "Fake task (hardware-free)",
        "config_path": None,
        "argv": ["-m", "monitor.fake_task"],
        "arguments": ["--duration", "30"],
    },
}
SUPERVISOR_SCHEMA_VERSION = 1


class SupervisorError(Exception):
    pass


def default_state_dir():
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "rpi-rig-monitor"


def _new_record():
    return {
        "supervisor_schema_version": SUPERVISOR_SCHEMA_VERSION,
        "session_id": None, "protocol": None, "state": "IDLE", "elapsed_s": None,
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
    def __init__(self, state_dir, socket_path, telemetry_port=5055):
        self.state_dir = Path(state_dir).resolve()
        self.socket_path = Path(socket_path).resolve()
        self.telemetry_port = telemetry_port
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
        response = _rpc(endpoint, request, timeout=timeout)
        if response.get("ok") is not True:
            raise SupervisorError(response.get("error", "experiment rejected command"))
        return response

    def _refresh(self):
        if not self.record.get("session_id"):
            return
        previous = (self.record["state"], self.record.get("exit_status"),
                    self.record.get("process_running"), self.record.get("control_connected"),
                    self.record.get("recovery_state"), tuple(self.record.get("capabilities", {}).items()),
                    tuple(self.record.get("recent_events", [])[:1]))
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
            if self.record["state"] in ACTIVE_STATES:
                self.record["state"] = "ERROR"
                if self.process is not None:
                    self.record["exit_status"] = self.process.poll()
                self.record["recent_events"].insert(0, {"event": "experiment_exit_without_final_state"})
                del self.record["recent_events"][50:]
            self._save_if_changed(previous)
            return
        if None in expected or actual != expected:
            self.record["process_running"] = False
            self.record["control_connected"] = False
            self.record["capabilities"] = {}
            self.record["recovery_state"] = "STALE"
            if self.record["state"] in ACTIVE_STATES:
                self.record["state"] = "ERROR"
            self.record["recent_events"].insert(0, {"event": "saved_process_identity_mismatch"})
            del self.record["recent_events"][50:]
            self._save_if_changed(previous)
            return
        self.record["process_running"] = True
        try:
            response = self._task_request({"command": "status"})
            status = response.get("status", {})
            if (not isinstance(status, dict) or status.get("session_id") != self.record["session_id"] or
                    status.get("protocol") != self.record["protocol"] or
                    status.get("state") not in ACTIVE_STATES + TERMINAL_STATES + ("IDLE",)):
                self.record["control_connected"] = False
                self.record["recovery_state"] = "CONTROL_MISMATCH"
                self._save_if_changed(previous)
                return
            self.record["control_connected"] = True
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
            capabilities = status.get("capabilities", {})
            self.record["capabilities"] = capabilities if isinstance(capabilities, dict) else {}
            event = status.get("recent_event")
            if event and (not self.record["recent_events"] or self.record["recent_events"][0] != event):
                self.record["recent_events"].insert(0, event)
                del self.record["recent_events"][50:]
            if self.record["state"] in TERMINAL_STATES:
                try:
                    final = self._task_request({"command": "shutdown"}, timeout=0.5).get("state")
                    if final:
                        self.record["state"] = final
                except (OSError, SupervisorError):
                    pass
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
        except (OSError, SupervisorError):
            self.record["control_connected"] = False
            self.record["recovery_state"] = "CONTROL_UNAVAILABLE"
        self._save_if_changed(previous)

    def _save_if_changed(self, previous):
        current = (self.record["state"], self.record.get("exit_status"),
                   self.record.get("process_running"), self.record.get("control_connected"),
                   self.record.get("recovery_state"), tuple(self.record.get("capabilities", {}).items()),
                   tuple(self.record.get("recent_events", [])[:1]))
        if current != previous or time.monotonic() - self._last_status_save >= 1.0:
            self._save()

    def snapshot(self):
        self._refresh()
        result = dict(self.record)
        result["capabilities"] = dict(self.record.get("capabilities", {}))
        result["recent_events"] = list(self.record.get("recent_events", []))
        result["session_history"] = list(self.record.get("session_history", []))
        return result

    def prepare(self, protocol, request_id=None):
        if protocol not in PROTOCOLS:
            raise SupervisorError("unsupported protocol")
        self._refresh()
        if self.record["state"] in ACTIVE_STATES or self.record.get("process_running"):
            if self.record.get("prepare_request_id") == request_id and self.record.get("protocol") == protocol:
                return self.snapshot()
            raise SupervisorError("an experiment session is already active")
        control_acks = self.record.get("control_acks", {})
        session_id = "fake-" + uuid.uuid4().hex[:12]
        endpoint = str(self.state_dir / (session_id + ".sock"))
        stdout_path = self.log_dir / (session_id + ".stdout.log")
        stderr_path = self.log_dir / (session_id + ".stderr.log")
        descriptor = PROTOCOLS[protocol]
        command = ([sys.executable] + list(descriptor["argv"]) + list(descriptor["arguments"]) +
                   ["--control-socket", endpoint, "--session-id", session_id,
                    "--telemetry-port", str(self.telemetry_port)])
        self.log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        history = list(self.record.get("session_history", []))
        if self.record.get("session_id"):
            history.insert(0, {key: self.record.get(key) for key in (
                "session_id", "protocol", "state", "pid", "process_starttime", "process_boot_id",
                "process_group_id", "process_session_id", "command", "config_path", "started_at_utc",
                "exit_status", "stdout_path", "stderr_path")})
        self.record = _new_record()
        self.record["session_history"] = history[:20]
        self.record["control_acks"] = control_acks
        self.record.update({"session_id": session_id, "protocol": protocol, "state": "PREPARING",
                            "pid": None, "command": command, "config_path": PROTOCOLS[protocol]["config_path"],
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
                self.process = subprocess.Popen(command, cwd=str(Path(__file__).resolve().parents[1]),
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
                self.record["state"] = "ERROR"
                self.record["exit_status"] = self.process.returncode
                self._save()
                raise SupervisorError("experiment exited during preparation")
            try:
                status = self._task_request({"command": "status"}).get("status", {})
                if (not isinstance(status, dict) or status.get("session_id") != session_id or
                        status.get("protocol") != protocol or status.get("state") not in ACTIVE_STATES + TERMINAL_STATES + ("IDLE",)):
                    raise SupervisorError("experiment control endpoint identity mismatch")
                self.record["state"] = status.get("state", "PREPARING")
                capabilities = status.get("capabilities", {})
                self.record["capabilities"] = capabilities if isinstance(capabilities, dict) else {}
                self.record["control_connected"] = True
                self.record["recovery_state"] = "CONNECTED"
                self._save()
                if self.record["state"] == "READY":
                    return self.snapshot()
            except (OSError, SupervisorError):
                pass
            time.sleep(0.05)
        self.process.terminate()
        self.record["state"] = "ERROR"
        self.record["capabilities"] = {}
        self.record["exit_status"] = self.process.wait(timeout=2.0)
        self._save()
        raise SupervisorError("experiment did not become READY")

    def control(self, command, protocol=None, request_id=None):
        if command not in CONTROL_ACTIONS + ("prepare",):
            raise SupervisorError("unsupported control action")
        request_id = request_id or str(uuid.uuid4())
        signature = [command, protocol]
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
            result = self.prepare(protocol or "fake", request_id)
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
            return {"ok": True, "protocols": [
                {"id": key, "display_name": value["display_name"], "available": True}
                for key, value in sorted(PROTOCOLS.items())]}
        if command == "control":
            try:
                return {"ok": True, "result": self.control(request.get("action"), request.get("protocol"),
                                                              request.get("request_id"))}
            except SupervisorError as exc:
                request_id = request.get("request_id")
                if isinstance(request_id, str) and request_id and request_id not in self.record.get("control_acks", {}):
                    self._remember_ack(request_id, [request.get("action"), request.get("protocol")],
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
                        except socket.timeout:
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
    args = parser.parse_args(argv)
    socket_path = args.socket_path or args.state_dir / "supervisor.sock"
    return SupervisorServer(args.state_dir, socket_path, args.telemetry_port).serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
