# `rpi_vstim_suite` monitor interface proposal

This contract keeps the experiment engine independent of the monitor. The engine
owns KMS, GPIO, cameras, audio, reward delivery, stimulus timing, and scientific
event logging. Losing UDP telemetry or the browser must not change a session.

## Telemetry: lossy, outbound only

The current engine groundwork already defines UTF-8 JSON over UDP with
`schema_version: 1`, `telemetry_schema_version: 3`, `type: state` or
`trial_complete`, `session_id`, `protocol`, `lifecycle`, `phase`, sequence and
UTC fields, optional trial/stimulus details, counters, camera records, and
degraded/error information. The monitor accepts this shape and preserves
unknown protocol fields. The engine can keep its current flat wire format.

For a new producer, the monitor also accepts these optional grouped fields:

```json
{
  "session": {"id": "s1", "protocol": "natural_images", "state": "RUNNING", "elapsed_s": 12.5, "eta_s": 30},
  "hardware": {"display": "ok", "camera_1": "recording", "camera_2": "recording", "storage": "ok", "gpio": "ok", "irig": "ok", "chrony": "ok", "audio": "ok"},
  "integrity": {"event_drops": 0, "writer_errors": 0, "kms_completed": 500, "kms_long_intervals": 0, "camera_errors": 0},
  "protocol_state": {"trial": 4, "stimulus": "image-a.png"}
}
```

The monitor exposes this as `rig.session`, `rig.hardware`, `rig.integrity`,
`rig.protocol`, plus a bounded `recent_events` list. Legacy flat fields remain
available to the current dashboard. `rig.protocol` is an open map; adding a
protocol must not require a monitor schema change. Hardware status should use
stable logical names and should not imply that telemetry is a health probe.

Publish compact status at roughly 2–5 Hz and heavier diagnostics at 0.5–1 Hz.
Sequence numbers and UTC timestamps help operators spot gaps, but the monitor's
receive clock and UDP packets are never authoritative scientific timing or the
event log. Staleness is displayed while the last values remain visible.

## Lifecycle and capabilities

The long-term control/session lifecycle is `IDLE`, `PREPARING`, `READY`,
`RUNNING`, `FINISHING`, then `COMPLETE`; terminal alternatives are `ERROR` and
`ABORTED`. The engine reports lifecycle and current capabilities. The monitor
must not derive scientific state from elapsed time, protocol names, or trial
counters. A `READY` report means preflight has completed and Start is safe.

Example status fields:

```json
{
  "session_id": "s1",
  "protocol": "go_nogo",
  "lifecycle": "READY",
  "elapsed_s": 0,
  "eta_s": null,
  "capabilities": {"start": true, "stop": false, "abort": true, "bait_left": true, "bait_right": true, "bait_center": false, "pause": false}
}
```

The dashboard renders only advertised capabilities. The engine rechecks every
command against its current lifecycle and protocol settings; the UI is not a
validation boundary.

## Reliable control: separate local Unix socket

Do not send commands over UDP. The persistent supervisor owns the experiment
process and exposes an owner-only local Unix socket to Flask. The supervisor
forwards semantic JSON-line requests over the running engine's owner-only
control socket and returns the engine acknowledgement. The current development
implementation uses a configurable per-user state directory and a per-session
socket there; production may move sockets to a service-managed runtime
directory when systemd deployment is added.

Proposed request envelope:

```json
{"request_id":"unique-id","command":"deliver_bait","channel":"left"}
```

Other commands are `status`, `start`, `stop`, and `abort`. A `prepare` request is
handled by the supervisor launch registry; the engine process starts, performs
preflight, then reports `READY`. `stop` asks the engine to finish cleanly;
`abort` asks it to end immediately and report `ABORTED`. Every request receives
an explicit success or structured error acknowledgement. The engine should
deduplicate `request_id` values so a lost HTTP response cannot repeat a manual
reward.

Manual reward is a semantic `deliver_bait(channel=...)` command. The engine
chooses calibrated output behavior, validates availability, actuates hardware,
and records an operator-triggered reward distinctly in the scientific event
log. The monitor never knows pin numbers, pulse duration, or output polarity.

## Reconnection and error behavior

The supervisor survives Flask and browser restarts. It stores the session ID,
protocol, state, PID, exact argv, config path, start time, engine control
endpoint, stdout/stderr paths, and exit status. On monitor startup, the monitor
queries the existing supervisor; it must not launch a second process when an
active session exists. The browser reconnects by polling supervisor state and
fresh telemetry independently.

If telemetry is stale, the task continues. If the supervisor is unavailable,
the GUI reports control offline and refuses a launch. If the engine rejects a
command or its control socket is lost, the supervisor returns an error and
preserves the last known lifecycle; it must not claim success. If the engine
exits without a final lifecycle report, the supervisor records `ERROR` and the
exit status when available.

## Minimum engine work for real integration

The separate engine currently has protocol CLI entry points and a telemetry
payload/publisher foundation, but the current runner does not yet expose the
complete monitor contract. Real integration needs only:

1. publish observational lifecycle, health, integrity, session identity, and
   protocol state without blocking timing-critical work;
2. expose a local control socket owned by the experiment process, with
   `status`, `start`, `stop`, `abort`, and only supported semantic operator
   commands;
3. advertise capabilities and reject commands that are invalid in the current
   protocol/lifecycle;
4. acknowledge commands, deduplicate request IDs, and log operator-triggered
   rewards separately;
5. report final state and exit information so the supervisor can reconnect
   after Flask restarts.

The monitor currently launches only a hardware-free fake task. Real protocol
launch descriptors, READY/preflight integration, and the final runtime socket
path should be agreed with the engine before enabling hardware sessions.
