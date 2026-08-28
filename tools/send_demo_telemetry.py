#!/usr/bin/env python3
"""Send one finite, self-consistent fake reward-conditioning session."""

import argparse
import json
import math
import socket
import time


def utc_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main():
    parser = argparse.ArgumentParser(description="Send demo telemetry to rpi_rig_monitor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5055)
    parser.add_argument("--interval-sec", type=float, default=3.0)
    parser.add_argument("--total-trials", type=int, default=20)
    parser.add_argument("--trials-per-block", type=int, default=10)
    parser.add_argument("--session-id", default=None)
    args = parser.parse_args()
    if args.total_trials < 1 or args.trials_per_block < 1:
        parser.error("--total-trials and --trials-per-block must be positive")

    session_id = args.session_id or "demo_{0}".format(time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    total_blocks = int(math.ceil(float(args.total_trials) / args.trials_per_block))
    address = (args.host, args.port)
    images = ["natimg_center_0693.png", "natimg_center_1393.png", "natimg_center_2093.png", "natimg_center_2793.png"]
    roles = ["rewarded_high_1", "reward_omission", "unrewarded"]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(message_type, **fields):
        packet = {"schema_version": 1, "type": message_type, "protocol": "reward_conditioning",
                  "session_id": session_id, "timestamp_utc": utc_timestamp()}
        packet.update(fields)
        sock.sendto(json.dumps(packet).encode("utf-8"), address)

    try:
        send("session", phase="GRAY", total_trials=args.total_trials, total_blocks=total_blocks)
        for trial in range(1, args.total_trials + 1):
            block = ((trial - 1) // args.trials_per_block) + 1
            image = images[(trial - 1) % len(images)]
            role = roles[(trial - 1) % len(roles)]
            common = {"trial": trial, "total_trials": args.total_trials, "block": block,
                      "total_blocks": total_blocks, "image": image, "stimulus_role": role,
                      "eta_sec": max(0, (args.total_trials - trial) * args.interval_sec)}
            send("state", phase="GRAY", **common)
            time.sleep(0.5)
            send("state", phase="STIMULUS", **common)
            time.sleep(1.0)
            # Generate licks first, then derive the operator-facing label from them.
            lick_times = [-0.12, 1.27] if trial % 4 == 0 else ([0.21, 0.48] if trial % 2 else [-0.12, 1.27])
            anticipatory_lick = any(0.0 <= value <= 1.0 for value in lick_times)
            reward_scheduled = role != "unrewarded"
            reward_omission = role == "reward_omission"
            reward_delivered = reward_scheduled and not reward_omission
            send("trial_complete", **common, reward_scheduled=reward_scheduled,
                 reward_omission=reward_omission, reward_delivered=reward_delivered,
                 anticipatory_lick=anticipatory_lick, lick_times_sec=lick_times)
            send("state", phase="ITI", **common)
            time.sleep(args.interval_sec)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
