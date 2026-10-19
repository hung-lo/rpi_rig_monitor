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
event log. The GUI reports telemetry freshness as `FRESH`, `STALE`, or `NONE`;
telemetry loss never changes the supervisor's last known lifecycle.

## Lifecycle and capabilities

The long-term control/session lifecycle is `IDLE`, `PREPARING`, `READY`,
`RUNNING`, `FINISHING`, then `COMPLETE`; terminal alternatives are `ERROR` and
`ABORTED`. The engine reports lifecycle and current capabilities. The monitor
must not derive scientific state from elapsed time, protocol names, or trial
counters. A `READY` report means preflight has completed and Start is safe.

Example control-status fields (UDP telemetry uses `lifecycle` instead of
`state`):

```json
{
  "session_id": "s1",
  "protocol": "go_nogo",
  "state": "READY",
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
control socket and returns the engine acknowledgement. In development, the
supervisor and per-session task socket use the configured per-user state
directory. The example systemd deployment puts persistent state and task
sockets under `/var/lib/rpi-rig-monitor`, with the supervisor client socket
under `/run/rpi-rig-monitor`; Flask must run as the same local operator account
to access it.

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

The browser is disposable, Flask is disposable, the supervisor is restartable,
and the experiment process is independent of all three. Flask only connects to
the configured supervisor socket; it never silently starts a supervisor. The
systemd unit in `deploy/rpi-rig-supervisor.service` starts the supervisor at
boot, while the Flask web service is deployed separately with the same local
operator account and socket configuration.

The supervisor atomically persists a versioned record containing session ID,
protocol, last known state, exact argv, config path, PID, Linux `/proc` process
start time and boot ID, process group/session IDs, UTC and monotonic launch
times, control endpoint, stdout/stderr paths, and exit status. On restart it
adopts only when PID start time, boot ID, group, and session still match and the
control endpoint reports the same session ID and protocol. A missing or
mismatched PID becomes an `ERROR` lifecycle plus a separate recovery status; a live process whose control
socket is unavailable remains running and blocks duplicate launches. Recovery
never signals or kills a task because telemetry or a socket is missing. Session
metadata and log paths remain in persistent state after later sessions start.

The lifecycle display is the engine's report when its control endpoint is
connected. The supervisor owns process bookkeeping and last-known state, not
scientific/task decisions. The engine remains authoritative for valid
transitions, capabilities, task completion, and scientific events. Recovery
indicators such as `control_connected`, `recovery_state`, and telemetry freshness
are infrastructure status, not experiment states.

The example systemd unit uses `KillMode=process` intentionally: systemd restart
stops only the supervisor's main process and leaves its detached experiment
child alive for adoption by the replacement supervisor. A host reboot does not
preserve a running experiment process.

If telemetry is stale or absent, the task continues. If the supervisor is
unavailable, the GUI reports task state as unknown unless telemetry still
reports an active session, and refuses a launch. If the engine rejects a
command or its control socket is lost, the supervisor returns an error and
preserves the last known lifecycle while showing control unavailable; it must
not claim success or kill the process. If the engine
exits without a final lifecycle report, the supervisor records `ERROR` and the
exit status when available.

The local bearer token is intended for a trusted lab LAN and operator, not
public internet exposure. It is sent only in the HTTP Authorization header,
not in a URL or telemetry packet. The browser requires a request ID on each
control request; the supervisor and engine deduplicate retries. Only explicitly
registered protocol IDs and fixed command names are accepted; shell command
strings are not part of the interface.

## Registered task adapter

The monitor's trusted registry currently implements `unix_json_v1`. It starts a
registered executable with structured argv and supplies `--control-socket`,
`--session-id`, and `--telemetry-port`. The task owns its Unix socket and
authoritative lifecycle. Its status response must contain `protocol`,
`session_id`, `state`, and runtime `capabilities`; elapsed/ETA are optional.
Mutating requests carry request IDs. The monitor checks `ok: true` on acknowledgements and
validates identity/state fields in status, but it currently does not send or
validate a control `schema_version` or require the response to echo a request
ID. The task should deduplicate request IDs. This permissive v1 behavior must be
made explicit or tightened before registering a real engine. The monitor does
not use UDP for commands. Control status uses `state`; UDP telemetry uses
`lifecycle` and remains observational.

Stable registry IDs belong to the monitor. If an engine uses a different
protocol ID, the registry stores that as `engine_protocol_id` and the supervisor
verifies the status response against the saved engine ID. This keeps IDs such as
`natural_images` stable while accommodating an engine-native name such as
`natural-images`. The exact registry entry and resolved argv are persisted so
registry edits cannot reinterpret an already-running session.

## Natural Images status

Natural Images is not registered as runnable. The current
`natural-images run --operator-control` path requires leased KMS during
preparation and opens KMS after START, and it does not offer a hardware-free
operator-control simulation mode. Before registration, VStim needs a safe
simulation path that prepares to READY without physical display probing and
runs START/STOP/ABORT through simulated display/timing services without GPIO,
KMS, camera, or audio access. Then monitor-side integration can validate the
existing control contract without hardware.

The monitor's built-in fake task remains the only runnable protocol. External
task repositories can use the same adapter without being imported by the
monitor or merged into `rpi_vstim_suite`; see
[`external_protocols.md`](external_protocols.md).
