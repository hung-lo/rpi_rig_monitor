#!/usr/bin/env python3
"""Send one finite, self-consistent fake reward-conditioning session."""

import argparse
import json
import math
import socket
import time

LICK_PATTERNS = ([0.21, 0.48], [-0.12, 1.27], [])
PROTOCOL_VERSION = "exposure_reward_partial_reversal_v1"
ROLES = [
    "high_R_to_R", "high_R_to_U", "high_U_to_R", "high_U_to_U",
    "medium_R_to_R", "medium_R_to_U", "medium_U_to_R", "medium_U_to_U",
    "low_U_to_U_01",
]


def utc_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def demo_lick_times(trial):
    """Return the legacy deterministic lick pattern used by the demo."""
    return list(LICK_PATTERNS[((trial - 1) // 3) % len(LICK_PATTERNS)])


def is_anticipatory(lick_times):
    return any(0.0 <= value < 1.0 for value in lick_times)


def current_reward(trajectory, contingency_phase):
    if contingency_phase == "acquisition":
        return trajectory in ("R_to_R", "R_to_U")
    return trajectory in ("R_to_R", "U_to_R")


def role_details(role, contingency_phase):
    if role.startswith("low_"):
        exposure, trajectory = "low", "U_to_U"
    else:
        exposure, trajectory = role.split("_", 1)
    return exposure.upper(), trajectory, current_reward(trajectory, contingency_phase)


def new_protocol_demo(args):
    session_id = args.session_id or "demo_{0}".format(time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    total_blocks = int(math.ceil(float(args.total_trials) / args.trials_per_block))
    address = (args.host, args.port)
    images = ["natimg_center_0693.png", "natimg_center_1393.png", "natimg_center_2093.png", "natimg_center_2793.png"]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    reward_volume = 3.0
    counters = {
        "task_reward_trains_verified_session": 0,
        "task_reward_trains_contacted_session": 0,
        "task_water_delivered_ul_session": 0.0,
        "task_water_likely_consumed_ul_session": 0.0,
    }
    for group in ("r_plus", "r_minus"):
        counters["task_{0}_cue_trials_completed_session".format(group)] = 0
        counters["task_{0}_cue_anticipatory_lick_trials_session".format(group)] = 0
    counters["task_r_plus_omission_trials_completed_session"] = 0
    counters["task_r_plus_omission_anticipatory_lick_trials_session"] = 0
    for exposure in ("high", "medium", "low"):
        for group in ("r_plus", "r_minus"):
            if exposure == "low" and group == "r_plus":
                continue
            counters["task_{0}_{1}_cue_trials_completed_session".format(exposure, group)] = 0
            counters["task_{0}_{1}_cue_anticipatory_lick_trials_session".format(exposure, group)] = 0

    def send(message_type, **fields):
        packet = {"schema_version": 1, "type": message_type, "protocol": "reward_conditioning",
                  "protocol_version": PROTOCOL_VERSION, "session_id": session_id,
                  "timestamp_utc": utc_timestamp(), "contingency_phase": args.contingency_phase}
        packet.update(fields)
        sock.sendto(json.dumps(packet).encode("utf-8"), address)

    def cumulative_fields():
        fields = {"reward_volume_ul_per_train": reward_volume}
        fields.update(counters)
        return fields

    try:
        send("session", phase="GRAY", total_trials=args.total_trials, total_blocks=total_blocks, **cumulative_fields())
        for trial in range(1, args.total_trials + 1):
            block = ((trial - 1) // args.trials_per_block) + 1
            role = ROLES[(trial - 1) % len(ROLES)]
            exposure, trajectory, reward_eligible = role_details(role, args.contingency_phase)
            anticipatory_lick = trial % (10 if reward_eligible else 5) != 0
            lick_times = [0.21] if anticipatory_lick else []
            reward_omission = reward_eligible and trial % 7 == 0
            reward_delivered = reward_eligible and not reward_omission
            reward_contacted = reward_delivered and trial % 3 != 0
            common = {"trial": trial, "total_trials": args.total_trials, "block": block,
                      "total_blocks": total_blocks, "image": images[(trial - 1) % len(images)],
                      "stimulus_role": role, "exposure_level": exposure,
                      "reward_trajectory": trajectory, "reward_eligible": reward_eligible,
                      "eta_sec": max(0, (args.total_trials - trial) * args.interval_sec)}
            common.update(cumulative_fields())
            send("state", phase="GRAY", **common)
            time.sleep(0.5)
            send("state", phase="STIMULUS", **common)
            time.sleep(1.0)

            group = "r_plus" if reward_eligible else "r_minus"
            counters["task_{0}_cue_trials_completed_session".format(group)] += 1
            if anticipatory_lick:
                counters["task_{0}_cue_anticipatory_lick_trials_session".format(group)] += 1
            counters["task_{0}_{1}_cue_trials_completed_session".format(exposure.lower(), group)] += 1
            if anticipatory_lick:
                counters["task_{0}_{1}_cue_anticipatory_lick_trials_session".format(exposure.lower(), group)] += 1
            if reward_omission:
                counters["task_r_plus_omission_trials_completed_session"] += 1
                if anticipatory_lick:
                    counters["task_r_plus_omission_anticipatory_lick_trials_session"] += 1
            if reward_delivered:
                counters["task_reward_trains_verified_session"] += 1
                counters["task_water_delivered_ul_session"] += reward_volume
                if reward_contacted:
                    counters["task_reward_trains_contacted_session"] += 1
                    counters["task_water_likely_consumed_ul_session"] += reward_volume

            trial_fields = dict(common)
            trial_fields.update(cumulative_fields())
            send("trial_complete", **trial_fields, phase="POST", reward_scheduled=reward_eligible,
                 reward_omission=reward_omission, reward_delivered=reward_delivered,
                 reward_contacted=reward_contacted, anticipatory_lick=anticipatory_lick,
                 lick_times_sec=lick_times)
            iti_fields = dict(common)
            iti_fields.update(cumulative_fields())
            send("state", phase="ITI", **iti_fields)
            time.sleep(args.interval_sec)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


def legacy_demo(args):
    session_id = args.session_id or "demo_{0}".format(time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    total_blocks = int(math.ceil(float(args.total_trials) / args.trials_per_block))
    address = (args.host, args.port)
    images = ["natimg_center_0693.png", "natimg_center_1393.png", "natimg_center_2093.png", "natimg_center_2793.png"]
    roles = ["rewarded_high_1", "reward_omission", "unrewarded_high_1", "low_probability"]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    reward_volume = 3.0
    verified = contacted = rewarded_completed = rewarded_anticipatory = 0
    unrewarded_completed = unrewarded_anticipatory = low_probability_completed = low_probability_anticipatory = 0

    def cumulative_fields():
        return {"reward_volume_ul_per_train": reward_volume,
                "task_reward_trains_verified_session": verified,
                "task_reward_trains_contacted_session": contacted,
                "task_water_delivered_ul_session": verified * reward_volume,
                "task_water_likely_consumed_ul_session": contacted * reward_volume,
                "task_rewarded_high_cue_trials_completed_session": rewarded_completed,
                "task_rewarded_high_cue_anticipatory_lick_trials_session": rewarded_anticipatory,
                "task_unrewarded_high_cue_trials_completed_session": unrewarded_completed,
                "task_unrewarded_high_cue_anticipatory_lick_trials_session": unrewarded_anticipatory,
                "task_low_probability_cue_trials_completed_session": low_probability_completed,
                "task_low_probability_cue_anticipatory_lick_trials_session": low_probability_anticipatory}

    def send(message_type, **fields):
        packet = {"schema_version": 1, "type": message_type, "protocol": "reward_conditioning",
                  "session_id": session_id, "timestamp_utc": utc_timestamp()}
        packet.update(fields)
        sock.sendto(json.dumps(packet).encode("utf-8"), address)

    try:
        send("session", phase="GRAY", total_trials=args.total_trials, total_blocks=total_blocks, **cumulative_fields())
        for trial in range(1, args.total_trials + 1):
            block = ((trial - 1) // args.trials_per_block) + 1
            role = roles[(trial - 1) % len(roles)]
            common = {"trial": trial, "total_trials": args.total_trials, "block": block,
                      "total_blocks": total_blocks, "image": images[(trial - 1) % len(images)],
                      "stimulus_role": role, "eta_sec": max(0, (args.total_trials - trial) * args.interval_sec)}
            common.update(cumulative_fields())
            send("state", phase="GRAY", **common)
            time.sleep(0.5)
            send("state", phase="STIMULUS", **common)
            time.sleep(1.0)
            lick_times = demo_lick_times(trial)
            anticipatory_lick = is_anticipatory(lick_times)
            reward_scheduled = role in ("rewarded_high_1", "reward_omission")
            reward_omission = role == "reward_omission"
            reward_delivered = reward_scheduled and not reward_omission
            reward_contacted = reward_delivered and trial % 3 != 0
            if role in ("rewarded_high_1", "reward_omission"):
                rewarded_completed += 1
                rewarded_anticipatory += int(anticipatory_lick)
            elif role == "unrewarded_high_1":
                unrewarded_completed += 1
                unrewarded_anticipatory += int(anticipatory_lick)
            else:
                low_probability_completed += 1
                low_probability_anticipatory += int(anticipatory_lick)
            if reward_delivered:
                verified += 1
                contacted += int(reward_contacted)
            trial_fields = dict(common)
            trial_fields.update(cumulative_fields())
            send("trial_complete", **trial_fields, reward_scheduled=reward_scheduled,
                 reward_omission=reward_omission, reward_delivered=reward_delivered,
                 reward_contacted=reward_contacted, anticipatory_lick=anticipatory_lick,
                 lick_times_sec=lick_times)
            iti_fields = dict(common)
            iti_fields.update(cumulative_fields())
            send("state", phase="ITI", **iti_fields)
            time.sleep(args.interval_sec)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(description="Send demo telemetry to rpi_rig_monitor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5055)
    parser.add_argument("--interval-sec", type=float, default=3.0)
    parser.add_argument("--total-trials", type=int, default=20)
    parser.add_argument("--trials-per-block", type=int, default=10)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--contingency-phase", choices=("acquisition", "reversal"), default="acquisition")
    parser.add_argument("--legacy", action="store_true", help="send the legacy reward-conditioning demo")
    args = parser.parse_args()
    if args.total_trials < 1 or args.trials_per_block < 1:
        parser.error("--total-trials and --trials-per-block must be positive")
    (legacy_demo if args.legacy else new_protocol_demo)(args)


if __name__ == "__main__":
    main()
