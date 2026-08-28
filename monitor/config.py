"""Runtime configuration for the monitor."""

import argparse
import os
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the read-only rig monitor")
    parser.add_argument("--udp-host", default=os.environ.get("RIG_MONITOR_UDP_HOST", "0.0.0.0"))
    parser.add_argument("--udp-port", type=int, default=int(os.environ.get("RIG_MONITOR_UDP_PORT", "5055")))
    parser.add_argument("--web-host", default=os.environ.get("RIG_MONITOR_WEB_HOST", "0.0.0.0"))
    parser.add_argument("--web-port", type=int, default=int(os.environ.get("RIG_MONITOR_WEB_PORT", "8080")))
    parser.add_argument("--stale-after-sec", type=float, default=float(os.environ.get("RIG_MONITOR_STALE_AFTER_SEC", "5.0")))
    return parser


def parse_args(argv: Any = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)
