"""Hardware-free experiment endpoint used to exercise the supervisor contract."""

import argparse
import json
import os
import socket
import time
import uuid

TERMINAL = ("COMPLETE", "ABORTED", "ERROR")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-socket", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--telemetry-port", type=int, default=5055)
    args = parser.parse_args(argv)
    state = {"session_id": args.session_id, "protocol": "fake", "state": "PREPARING",
             "elapsed_s": 0.0, "eta_s": None, "exit_status": None,
             "capabilities": {}, "recent_event": None, "_ack_cache": {}, "_ack_order": []}
    started = None
    finishing_at = None
    ready_at = time.monotonic() + 0.1
    terminal_at = None
    next_telemetry = 0.0
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        try:
            os.unlink(args.control_socket)
        except OSError:
            pass
        server.bind(args.control_socket)
        os.chmod(args.control_socket, 0o600)
        server.listen(4)
        server.settimeout(0.1)
        print(f"fake task preparing session {args.session_id}", flush=True)
        while True:
            now = time.monotonic()
            if state["state"] == "PREPARING" and now >= ready_at:
                state["state"] = "READY"
                print("fake task ready", flush=True)
            if state["state"] == "RUNNING":
                started = state["started_at_monotonic"]
                state["elapsed_s"] = max(0.0, now - started)
                state["eta_s"] = max(0.0, args.duration - state["elapsed_s"])
                if state["elapsed_s"] >= args.duration:
                    state["state"] = "FINISHING"
                    finishing_at = now + 0.1
                    print("fake task finishing", flush=True)
            if state["state"] == "FINISHING" and now >= finishing_at:
                state["state"] = "COMPLETE"
                state["eta_s"] = 0.0
                state["exit_status"] = 0
                terminal_at = now
                print("fake task complete", flush=True)
            state["capabilities"] = {
                "start": state["state"] == "READY",
                "stop": state["state"] == "RUNNING",
                "abort": state["state"] in ("PREPARING", "READY", "RUNNING"),
                "bait_left": state["state"] == "READY",
                "bait_right": state["state"] == "READY",
            }
            if now >= next_telemetry:
                _send_telemetry(args.telemetry_port, state)
                next_telemetry = now + 0.5
            if state["state"] in TERMINAL:
                server.settimeout(0.25)
            try:
                connection, _ = server.accept()
            except socket.timeout:
                if state["state"] in TERMINAL and now - terminal_at >= 5.0:
                    break
                continue
            with connection:
                request = _read_json(connection)
                if not isinstance(request, dict):
                    request = {}
                response = _handle(request, state)
                try:
                    connection.sendall(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
                except OSError:
                    pass
                if request.get("command") == "shutdown" and state["state"] in TERMINAL:
                    break
                if request.get("command") == "stop":
                    finishing_at = time.monotonic() + 0.1
                if request.get("command") == "abort":
                    terminal_at = time.monotonic()
        return 0 if state["exit_status"] == 0 else 1
    finally:
        server.close()
        try:
            os.unlink(args.control_socket)
        except OSError:
            pass


def _handle(request, state):
    command = request.get("command") if isinstance(request, dict) else None
    current = state["state"]
    if command == "status":
        return {"ok": True, "status": {key: value for key, value in state.items() if not key.startswith("_")}}
    request_id = request.get("request_id")
    if request_id is not None:
        if not isinstance(request_id, str) or not request_id or len(request_id) > 64:
            return {"ok": False, "error": "invalid request_id"}
        signature = (command, request.get("channel"))
        cached = state["_ack_cache"].get(request_id)
        if cached:
            if cached["signature"] != signature:
                return {"ok": False, "error": "request_id reused for a different command"}
            return cached["response"]

    def reply(response):
        if request_id is not None:
            state["_ack_cache"][request_id] = {"signature": signature, "response": response}
            state["_ack_order"].append(request_id)
            if len(state["_ack_order"]) > 128:
                del state["_ack_cache"][state["_ack_order"].pop(0)]
        return response

    if command == "start" and current == "READY":
        state["state"] = "RUNNING"
        state["started_at_monotonic"] = time.monotonic()
        state["recent_event"] = {"event": "task_started", "id": str(uuid.uuid4())}
        print("fake task started", flush=True)
        return reply({"ok": True, "state": "RUNNING"})
    if command == "stop" and current == "RUNNING":
        state["state"] = "FINISHING"
        state["eta_s"] = 0.0
        state["recent_event"] = {"event": "operator_stop"}
        print("fake task graceful stop requested", flush=True)
        return reply({"ok": True, "state": "FINISHING"})
    if command == "abort" and current in ("PREPARING", "READY", "RUNNING"):
        state["state"] = "ABORTED"
        state["eta_s"] = None
        state["exit_status"] = 0
        state["recent_event"] = {"event": "operator_abort"}
        print("fake task aborted", flush=True)
        return reply({"ok": True, "state": "ABORTED"})
    if command == "deliver_bait" and current == "READY" and request.get("channel") in ("left", "right"):
        state["recent_event"] = {"event": "operator_bait", "channel": request["channel"]}
        print(f"OPERATOR_REWARD channel={request['channel']}", flush=True)
        return reply({"ok": True, "accepted": "operator_bait", "channel": request["channel"]})
    if command == "shutdown" and current in TERMINAL:
        return {"ok": True, "state": current}
    return reply({"ok": False, "error": f"command unavailable in {current}"})


def _read_json(connection):
    data = bytearray()
    while len(data) <= 65536:
        chunk = connection.recv(4096)
        if not chunk or b"\n" in chunk:
            data.extend(chunk.split(b"\n", 1)[0])
            return json.loads(data.decode("utf-8"))
        data.extend(chunk)
    return {}


def _send_telemetry(port, state):
    packet = {
        "schema_version": 1,
        "type": "state",
        "session_id": state["session_id"],
        "protocol": state["protocol"],
        "lifecycle": state["state"],
        "elapsed_s": state["elapsed_s"],
        "eta_s": state["eta_s"],
        "hardware": {"display": "mock", "camera_1": "mock", "camera_2": "mock",
                     "storage": "mock", "gpio": "mock", "irig": "mock",
                     "chrony": "mock", "audio": "mock"},
        "integrity": {"event_drops": 0, "writer_errors": 0, "kms_completed": 0,
                      "kms_long_intervals": 0, "camera_errors": 0},
        "protocol_state": {"fake_progress": round(state["elapsed_s"], 1)},
    }
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(json.dumps(packet, separators=(",", ":")).encode("utf-8"), ("127.0.0.1", port))
        finally:
            sock.close()
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
