#!/usr/bin/env python3
"""Start the UDP receiver and the read-only Flask dashboard."""

import logging
import threading

from monitor.config import parse_args
from monitor.receiver import UDPReceiver
from monitor.state import MonitorState
from monitor.web import create_app


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = MonitorState(stale_after_sec=args.stale_after_sec)
    receiver = UDPReceiver(state, host=args.udp_host, port=args.udp_port)
    receiver_thread = threading.Thread(target=receiver.serve_forever, name="udp-receiver", daemon=True)
    receiver_thread.start()
    print("UDP telemetry listening on {0}:{1}".format(args.udp_host, args.udp_port), flush=True)
    print("Dashboard available at http://<host>:{0}".format(args.web_port), flush=True)
    app = create_app(state)
    try:
        app.run(host=args.web_host, port=args.web_port, debug=False, use_reloader=False)
    finally:
        receiver.stop()


if __name__ == "__main__":
    main()
