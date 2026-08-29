#!/usr/bin/env python3
"""Send one finite, self-consistent fake reward-conditioning session."""

import argparse
import json
import math
import socket
import time

LICK_PATTERNS = ([0.21, 0.48], [-0.12, 1.27], [])


def utc_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def demo_lick_times(trial):
    """Return a deterministic pattern, rotating independently from stimulus role."""
    return list(LICK_PATTERNS[((trial - 1) // 3) % len(LICK_PATTERNS)])


def is_anticipatory(lick_times):
    return any(0.0 <= value < 1.0 for value in lick_times)


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
    roles = ["rewarded_high_1", "reward_omission", "unrewarded_high_1", "low_probability"]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    reward_volume = 3.0
    verified = 0
    contacted = 0
    rewarded_completed = 0
    rewarded_anticipatory = 0
    unrewarded_completed = 0
    unrewarded_anticipatory = 0
    low_probability_completed = 0
    low_probability_anticipatory = 0

    def cumulative_fields():
        return {
            "reward_volume_ul_per_train": reward_volume,
            "task_reward_trains_verified_session": verified,
            "task_reward_trains_contacted_session": contacted,
            "task_water_delivered_ul_session": verified * reward_volume,
            "task_water_likely_consumed_ul_session": contacted * reward_volume,
            "task_rewarded_high_cue_trials_completed_session": rewarded_completed,
            "task_rewarded_high_cue_anticipatory_lick_trials_session": rewarded_anticipatory,
            "task_unrewarded_high_cue_trials_completed_session": unrewarded_completed,
            "task_unrewarded_high_cue_anticipatory_lick_trials_session": unrewarded_anticipatory,
            "task_low_probability_cue_trials_completed_session": low_probability_completed,
            "task_low_probability_cue_anticipatory_lick_trials_session": low_probability_anticipatory,
        }

    def send(message_type, **fields):
        packet = {"schema_version": 1, "type": message_type, "protocol": "reward_conditioning",
                  "session_id": session_id, "timestamp_utc": utc_timestamp()}
        packet.update(fields)
        sock.sendto(json.dumps(packet).encode("utf-8"), address)

    try:
        send("session", phase="GRAY", total_trials=args.total_trials, total_blocks=total_blocks, **cumulative_fields())
        for trial in range(1, args.total_trials + 1):
            block = ((trial - 1) // args.trials_per_block) + 1
            image = images[(trial - 1) % len(images)]
            role = roles[(trial - 1) % len(roles)]
            common = {"trial": trial, "total_trials": args.total_trials, "block": block,
                      "total_blocks": total_blocks, "image": image, "stimulus_role": role,
                      "eta_sec": max(0, (args.total_trials - trial) * args.interval_sec)}
            common.update(cumulative_fields())
            send("state", phase="GRAY", **common)
            time.sleep(0.5)
            send("state", phase="STIMULUS", **common)
            time.sleep(1.0)
            # Generate licks first, then derive the operator-facing label from them.
            lick_times = demo_lick_times(trial)
            anticipatory_lick = is_anticipatory(lick_times)
            reward_scheduled = role in ("rewarded_high_1", "reward_omission")
            reward_omission = role == "reward_omission"
            reward_delivered = reward_scheduled and not reward_omission
            reward_contacted = reward_delivered and trial % 3 != 0
            if role in ("rewarded_high_1", "reward_omission"):
                rewarded_completed += 1
                if anticipatory_lick:
                    rewarded_anticipatory += 1
            elif role == "unrewarded_high_1":
                unrewarded_completed += 1
                if anticipatory_lick:
                    unrewarded_anticipatory += 1
            else:
                low_probability_completed += 1
                if anticipatory_lick:
                    low_probability_anticipatory += 1
            if reward_delivered:
                verified += 1
                if reward_contacted:
                    contacted += 1
            trial_fields = dict(common)
            trial_fields.update(cumulative_fields())
            send("trial_complete", **trial_fields, reward_scheduled=reward_scheduled,
                 reward_omission=reward_omission, reward_delivered=reward_delivered,
                 reward_contacted=reward_contacted,
                 anticipatory_lick=anticipatory_lick, lick_times_sec=lick_times)
            iti_fields = dict(common)
            iti_fields.update(cumulative_fields())
            send("state", phase="ITI", **iti_fields)
            time.sleep(args.interval_sec)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
