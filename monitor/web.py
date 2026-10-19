"""Flask dashboard application."""

import hmac
import os

from flask import Flask, jsonify, render_template, request, send_from_directory

from .state import MonitorState
from .supervisor import PROTOCOLS, SupervisorError


def create_app(state: MonitorState, receiver=None, image_dir=None, supervisor=None,
               control_token=None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    control_token = control_token or os.environ.get("RIG_MONITOR_CONTROL_TOKEN")

    @app.route("/")
    def dashboard():
        return render_template("dashboard.html")

    @app.route("/api/state")
    def api_state():
        result = state.snapshot()
        result["control_enabled"] = bool(control_token)
        if supervisor is not None:
            try:
                response = supervisor.request({"command": "status"})
                result["supervisor"] = _public_status(response.get("status", {}))
            except SupervisorError as exc:
                result["supervisor"] = {"available": False, "state": "UNKNOWN", "error": str(exc),
                                         "capabilities": {}, "control_connected": False,
                                         "recovery_state": "SUPERVISOR_UNAVAILABLE"}
        else:
            result["supervisor"] = {"available": False, "state": "UNKNOWN", "capabilities": {},
                                     "control_connected": False, "recovery_state": "SUPERVISOR_UNAVAILABLE"}
        return jsonify(result)

    @app.route("/api/protocols")
    def api_protocols():
        try:
            if supervisor is None:
                return jsonify({"error": "supervisor unavailable"}), 503
            response = supervisor.request({"command": "protocols"})
            return jsonify(response.get("protocols", []))
        except SupervisorError as exc:
            return jsonify({"error": str(exc)}), 503

    @app.route("/api/control", methods=["POST"])
    def api_control():
        if not control_token:
            return jsonify({"error": "control is disabled; configure RIG_MONITOR_CONTROL_TOKEN"}), 503
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + control_token).encode("utf-8")):
            return jsonify({"error": "operator authorization required"}), 401
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get("command") not in (
                "prepare", "start", "stop", "abort", "bait_left", "bait_right"):
            return jsonify({"error": "invalid control request"}), 400
        command = body["command"]
        request_id = body.get("request_id")
        if (not isinstance(request_id, str) or not request_id or len(request_id) > 64 or
                set(body) - {"command", "protocol", "request_id"} or
                (command == "prepare" and body.get("protocol") not in PROTOCOLS) or
                (command != "prepare" and "protocol" in body)):
            return jsonify({"error": "invalid control request"}), 400
        if supervisor is None:
            return jsonify({"error": "supervisor unavailable"}), 503
        if command == "prepare":
            observed = state.snapshot()["rig"]["session"]
            if observed.get("state") in ("PREPARING", "READY", "RUNNING", "FINISHING") and observed.get("id"):
                try:
                    current = supervisor.request({"command": "status"}).get("status", {})
                except SupervisorError as exc:
                    return jsonify({"error": str(exc)}), 503
                if observed["id"] != current.get("session_id"):
                    return jsonify({"error": "an active telemetry session is not registered with the supervisor"}), 409
        payload = {"command": "control", "action": command, "request_id": request_id}
        if command == "prepare":
            payload["protocol"] = body["protocol"]
        try:
            response = supervisor.request(payload)
        except SupervisorError as exc:
            return jsonify({"error": str(exc)}), 503
        if response.get("ok") is not True:
            return jsonify({"error": response.get("error", "command rejected")}), 409
        return jsonify(response.get("result", {}))

    @app.route("/health")
    def health():
        udp_status = "ok" if receiver is None else receiver.health_status()
        supervisor_status = "ok"
        if supervisor is not None:
            try:
                supervisor.request({"command": "status"})
            except SupervisorError:
                supervisor_status = "unavailable"
        status = "ok" if udp_status == "ok" and supervisor_status == "ok" else "unhealthy"
        response = jsonify({"status": status, "udp_receiver": udp_status,
                            "supervisor": supervisor_status})
        return response, (200 if status == "ok" else 503)

    @app.route("/stimulus-image/<filename>")
    def stimulus_image(filename):
        if not image_dir or not os.path.isdir(image_dir):
            return "", 404
        return send_from_directory(image_dir, filename)

    return app


def _public_status(status):
    return {"available": True, "session_id": status.get("session_id"),
            "protocol": status.get("protocol"), "state": status.get("state", "IDLE"),
            "elapsed_s": status.get("elapsed_s"), "eta_s": status.get("eta_s"),
            "capabilities": status.get("capabilities", {}),
            "recent_events": status.get("recent_events", []),
            "process_running": status.get("process_running", False),
            "control_connected": status.get("control_connected", False),
            "recovery_state": status.get("recovery_state", "UNKNOWN"),
            "stdout_path": status.get("stdout_path"), "stderr_path": status.get("stderr_path"),
            "session_history": status.get("session_history", [])}
