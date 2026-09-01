#!/usr/bin/env python3
"""Send a finite, read-only spout-training demo telemetry session."""

import argparse
import json
import socket
import time


def utc_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main():
    parser = argparse.ArgumentParser(description="Send spout-training demo telemetry")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5055)
    parser.add_argument("--interval-sec", type=float, default=1.0)
    parser.add_argument("--session-id", default=None)
    args = parser.parse_args()
    session_id = args.session_id or "spout_demo_{0}".format(time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    address = (args.host, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bait_water = 15.0
    reward_volume = 5.0
    completed_bait = 3
    bait_contacted = 2
    success_pattern = [True, True, False, True, False]

    def send(message_type, **fields):
        packet = {"schema_version": 1, "type": message_type, "protocol": "spout_training",
                  "session_id": session_id, "timestamp_utc": utc_timestamp()}
        packet.update(fields)
        sock.sendto(json.dumps(packet).encode("utf-8"), address)

    def base(phase):
        return {"phase": phase, "maximum_training_rewards": 5,
                "task_water_delivered_ul_session": 0.0,
                "bait_water_ul_session": bait_water,
                "total_water_ul_session": bait_water,
                "reward_volume_ul": reward_volume,
                "completed_bait_reward_count": completed_bait,
                "completed_bait_suction_count": completed_bait,
                "bait_contacted_count_session": bait_contacted,
                "recent_lick_count_2s": 4, "last_lick_age_sec": 0.2,
                "licking_active": True, "total_lick_onset_count_session": 4,
                "criterion_evaluable": False, "training_passed": False,
                "attempted_training_reward_count": 0, "completed_training_reward_count": 0,
                "retrieval_success_count_session": 0, "retrieval_failure_count_session": 0}

    try:
        send("session", **base("MANUAL_BAIT"))
        send("state", **base("MANUAL_BAIT"), manual_bait_active=True, bait_mode="manual",
             bait_index=3, bait_total=None, last_bait_contacted=False,
             last_bait_first_lick_latency_sec=None, bait_reward_ready=False,
             manual_start_requested=False, next_reward_in_sec=None)
        time.sleep(args.interval_sec)
        send("state", **base("MANUAL_BAIT"), manual_bait_active=True, bait_mode="manual",
             bait_index=3, bait_total=None, last_bait_contacted=True,
             last_bait_first_lick_latency_sec=0.2, bait_reward_ready=False,
             manual_start_requested=True, next_reward_in_sec=None)
        time.sleep(args.interval_sec)
        for index, success in enumerate(success_pattern, 1):
            completed = index
            successes = sum(1 for value in success_pattern[:index] if value)
            failures = completed - successes
            common = base("WAITING_REWARD")
            common.update({"training_reward_index": index, "attempted_training_reward_count": index,
                           "completed_training_reward_count": completed,
                           "retrieval_success_count_session": successes,
                           "retrieval_failure_count_session": failures,
                           "task_water_delivered_ul_session": completed * reward_volume,
                           "total_water_ul_session": bait_water + completed * reward_volume,
                           "recent_lick_count_2s": 0, "last_lick_age_sec": 1.2,
                           "licking_active": False, "total_lick_onset_count_session": 4})
            send("state", **common, next_reward_in_sec=0.8)
            time.sleep(args.interval_sec)
            trial_packet = dict(common)
            trial_packet.update({"phase": "REWARD", "trial": index,
                                 "reward_contacted": success,
                                 "first_lick_latency_sec": 0.34 if success else None,
                                 "lick_count_reward_to_0p5_sec": 2 if success else 0,
                                 "lick_count_reward_to_1p0_sec": 4 if success else 0,
                                 "lick_count_reward_to_2p5_sec": 7 if success else 0,
                                 "reward_delivered": True})
            send("trial_complete", **trial_packet)
            time.sleep(args.interval_sec)
        final = base("COMPLETE")
        final.update({"training_reward_index": 5, "attempted_training_reward_count": 5,
                      "completed_training_reward_count": 5,
                      "retrieval_success_count_session": 3,
                      "retrieval_failure_count_session": 2,
                      "task_water_delivered_ul_session": 25.0,
                      "total_water_ul_session": 40.0,
                      "recent_lick_count_2s": 0, "last_lick_age_sec": 3.0,
                      "licking_active": False})
        send("state", **final, next_reward_in_sec=None)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
