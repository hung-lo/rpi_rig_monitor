"""Flask dashboard application."""

from flask import Flask, jsonify, render_template

from .state import MonitorState


def create_app(state: MonitorState, receiver=None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    @app.route("/")
    def dashboard():
        return render_template("dashboard.html")

    @app.route("/api/state")
    def api_state():
        return jsonify(state.snapshot())

    @app.route("/health")
    def health():
        udp_status = "ok" if receiver is None else receiver.health_status()
        status = "ok" if udp_status == "ok" else "unhealthy"
        response = jsonify({"status": status, "udp_receiver": udp_status})
        return response, (200 if status == "ok" else 503)

    return app
