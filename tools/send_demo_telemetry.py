#!/usr/bin/env python3
"""Send a small fake reward-conditioning telemetry stream over UDP."""

import argparse
import json
import random
import socket
import time


def send(sock, address, packet):
    sock.sendto(json.dumps(packet).encode("utf-8"), address)


def main():
    parser = argparse.ArgumentParser(description="Send demo telemetry to rpi_rig_monitor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5055)
    parser.add_argument("--interval-sec", type=float, default=3.0)
    args = parser.parse_args()
    address = (args.host, args.port)
    session_id = "demo_session"
    images = ["natimg_center_1825.png", "natimg_left_0412.png", "natimg_right_0931.png"]
    roles = ["rewarded_high_1", "reward_omission", "unrewarded"]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    trial = 0
    try:
        send(sock, address, {"schema_version": 1, "type": "session", "protocol": "reward_conditioning", "session_id": session_id, "phase": "TASK", "total_trials": 20, "total_blocks": 2})
        while True:
            image = images[trial % len(images)]
            role = roles[trial % len(roles)]
            for phase, delay in (("GRAY", 0.5), ("STIMULUS", 1.0), ("ITI", args.interval_sec)):
                send(sock, address, {"schema_version": 1, "type": "state", "protocol": "reward_conditioning", "session_id": session_id, "phase": phase, "trial": trial + 1, "total_trials": 20, "block": trial // 10 + 1, "total_blocks": 2, "image": image, "stimulus_role": role, "eta_sec": max(0, (19 - trial) * args.interval_sec)})
                time.sleep(delay)
            trial += 1
            omission = role == "reward_omission"
            send(sock, address, {"schema_version": 1, "type": "trial_complete", "protocol": "reward_conditioning", "session_id": session_id, "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "trial": trial, "image": image, "stimulus_role": role, "reward_scheduled": role != "unrewarded", "reward_omission": omission, "anticipatory_lick": random.choice([True, False]), "lick_times_sec": [-0.12, 0.21, 0.48]})
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
