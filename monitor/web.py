"""Flask dashboard application."""

import os

from flask import Flask, jsonify, render_template, send_from_directory

from .state import MonitorState


def create_app(state: MonitorState, receiver=None, image_dir=None) -> Flask:
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

    @app.route("/stimulus-image/<filename>")
    def stimulus_image(filename):
        if not image_dir or not os.path.isdir(image_dir):
            return "", 404
        return send_from_directory(image_dir, filename)

    return app
