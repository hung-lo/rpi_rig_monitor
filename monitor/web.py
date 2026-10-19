"""Flask dashboard application."""

import hmac
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from .registry import PROTOCOL_ID_RE
from .state import MonitorState
from .supervisor import SupervisorError

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_IMAGE_NAME_RE = re.compile(r"^natimg_center_\d{4}\.png$")
_MAX_SESSION_INDEX_BYTES = 4_000_000
_MAX_NATURAL_IMAGE_BYTES = 1_000_000

def create_app(state: MonitorState, receiver=None, image_dir=None, supervisor=None,
               control_token=None, session_root=None, natural_image_dir=None,
               simulation_session_root=None) -> Flask:
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
        if not isinstance(request_id, str) or not request_id or len(request_id) > 64:
            return jsonify({"error": "invalid control request"}), 400
        if command == "prepare":
            protocol_id, preset_id, fields = (body.get("protocol_id"), body.get("preset_id"),
                                                body.get("fields"))
            if (set(body) != {"command", "request_id", "protocol_id", "preset_id", "fields"} or
                    not isinstance(protocol_id, str) or not PROTOCOL_ID_RE.fullmatch(protocol_id) or
                    not isinstance(preset_id, str) or not PROTOCOL_ID_RE.fullmatch(preset_id) or
                    not isinstance(fields, dict)):
                return jsonify({"error": "invalid control request"}), 400
        elif set(body) != {"command", "request_id"}:
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
            payload.update({"protocol_id": protocol_id, "preset_id": preset_id, "fields": fields})
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
        try:
            root = Path(image_dir).resolve(strict=True)
            path = (root / filename).resolve(strict=True)
            path.relative_to(root)
            if not path.is_file():
                return "", 404
        except (OSError, ValueError):
            return "", 404
        return send_file(path)

    @app.route("/api/stimulus-image/<session_id>/<filename>")
    def selected_stimulus_image(session_id, filename):
        if (not _SESSION_ID_RE.fullmatch(session_id) or session_id in {".", ".."}
                or not _IMAGE_NAME_RE.fullmatch(filename)
                or int(filename[14:18]) >= 2800 or not natural_image_dir
                or not (session_root or simulation_session_root)):
            return "", 404
        try:
            assets = Path(natural_image_dir).resolve(strict=True)
            roots = []
            for configured_root in (session_root, simulation_session_root):
                if configured_root:
                    try:
                        root = Path(configured_root).resolve(strict=True)
                        if stat.S_ISDIR(root.stat().st_mode) and root not in roots:
                            roots.append(root)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        return "", 404
            matches = []
            for root in roots:
                candidate = root / session_id
                try:
                    candidate.lstat()
                except FileNotFoundError:
                    continue
                except OSError:
                    return "", 404
                matches.append((root, candidate))
            if len(matches) != 1:
                return "", 404
            sessions, session = matches[0]
            session = session.resolve(strict=True)
            session.relative_to(sessions)
            table_path = (session / "selected_image_table.json").resolve(strict=True)
            table_path.relative_to(session)
            plan_path = (session / "plan.json").resolve(strict=True)
            plan_path.relative_to(session)
            manifest_path = (session / "asset_manifest.json").resolve(strict=True)
            manifest_path.relative_to(session)
            if any(not path.is_file() or path.stat().st_size > _MAX_SESSION_INDEX_BYTES
                   for path in (table_path, plan_path, manifest_path)):
                return "", 404
            table = json.loads(table_path.read_text(encoding="utf-8"))
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not all(isinstance(value, dict) for value in (table, plan, manifest)):
                return "", 404
            rows = table.get("images") if isinstance(table, dict) else None
            row = next((item for item in rows if isinstance(item, dict)
                        and item.get("image") == filename), None) if isinstance(rows, list) else None
            expected_hash = row.get("sha256") if row else None
            if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
                return "", 404
            metadata = plan.get("metadata")
            selected = metadata.get("selected_images") if isinstance(metadata, dict) else None
            entries = manifest.get("entries") if isinstance(manifest, dict) else None
            if (not isinstance(selected, list) or filename not in selected or
                    not isinstance(entries, list) or not any(
                        isinstance(item, dict) and item.get("relative_path") == filename
                        and item.get("sha256") == expected_hash for item in entries
                    )):
                return "", 404
            image_path = (assets / filename).resolve(strict=True)
            image_path.relative_to(assets)
            if not image_path.is_file() or image_path.stat().st_size > _MAX_NATURAL_IMAGE_BYTES:
                return "", 404
            image_bytes = image_path.read_bytes()
            if (len(image_bytes) > _MAX_NATURAL_IMAGE_BYTES
                    or hashlib.sha256(image_bytes).hexdigest() != expected_hash):
                return "", 404
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return "", 404
        response = send_file(
            image_path, mimetype="image/png", conditional=True, max_age=86400,
            download_name=filename,
        )
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    return app


def _public_status(status):
    return {"available": True, "session_id": status.get("session_id"),
            "protocol": status.get("protocol"), "protocol_id": status.get("protocol_id", status.get("protocol")),
            "preset_id": status.get("preset_id"),
            "protocol_display_name": status.get("protocol_display_name"),
            "preset_display_name": status.get("preset_display_name"),
            "subject_id": status.get("subject_id"), "state": status.get("state", "IDLE"),
            "elapsed_s": status.get("elapsed_s"), "eta_s": status.get("eta_s"),
            "capabilities": status.get("capabilities", {}),
            "recent_events": status.get("recent_events", []),
            "process_running": status.get("process_running", False),
            "control_connected": status.get("control_connected", False),
            "recovery_state": status.get("recovery_state", "UNKNOWN"),
            "lifecycle_source": status.get("lifecycle_source"),
            "recovery_diagnostic": status.get("recovery_diagnostic"),
            "terminal_reason": status.get("terminal_reason"),
            "engine_error_summary": status.get("engine_error_summary"),
            "stdout_path": status.get("stdout_path"), "stderr_path": status.get("stderr_path"),
            "session_history": status.get("session_history", [])}
