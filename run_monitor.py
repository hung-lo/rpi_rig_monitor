#!/usr/bin/env python3
"""Start observational telemetry, the supervisor connection, and the GUI."""

import logging
import sys
import threading

from monitor.config import parse_args
from monitor.receiver import UDPReceiver
from monitor.state import MonitorState
from monitor.supervisor import SupervisorClient, SupervisorError
from monitor.web import create_app

LOGGER = logging.getLogger(__name__)


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = MonitorState(stale_after_sec=args.stale_after_sec)
    receiver = UDPReceiver(state, host=args.udp_host, port=args.udp_port)
    supervisor = SupervisorClient(args.supervisor_state_dir, args.supervisor_socket)
    try:
        receiver.bind()
    except OSError as exc:
        print(f"Unable to bind UDP telemetry receiver on {args.udp_host}:{args.udp_port}: {exc}", file=sys.stderr)
        return 1
    receiver_thread = threading.Thread(target=receiver.serve_forever, name="udp-receiver", daemon=True)
    receiver_thread.start()
    try:
        supervisor.connect()
    except SupervisorError as exc:
        LOGGER.error("Supervisor unavailable; dashboard controls remain offline: %s", exc)
    print(f"UDP telemetry listening on {args.udp_host}:{args.udp_port}", flush=True)
    print(f"Dashboard available at http://<host>:{args.web_port}", flush=True)
    if not args.control_token:
        LOGGER.warning("Browser controls disabled; set RIG_MONITOR_CONTROL_TOKEN to enable them")
    app = create_app(state, receiver, image_dir=args.image_dir,
                     session_root=args.session_root,
                     simulation_session_root=args.simulation_session_root,
                     natural_image_dir=args.natural_image_dir,
                     supervisor=supervisor,
                     control_token=args.control_token)
    try:
        app.run(host=args.web_host, port=args.web_port, debug=False, use_reloader=False)
    finally:
        receiver.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
