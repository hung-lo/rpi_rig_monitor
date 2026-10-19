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

Canonical `unix_json_v1` request envelope:

```json
{"schema_version":1,"request_id":"unique-id","command":"deliver_bait","channel":"left"}
```

Every request, including `status`, carries `schema_version: 1` and a unique
`request_id`. Every status response, success acknowledgement, and error
acknowledgement must return `schema_version: 1` and the identical request ID.
The monitor rejects missing or mismatched response fields. VStim's
`ExperimentControl` accepts omitted envelope fields for older clients, but it
echoes a supplied status request ID and returns versioned, correlated responses;
the monitor uses only the strict form.

Other commands are `status`, `start`, `stop`, `abort`, and semantic actions such
as `deliver_bait`. A `prepare` request is handled by the supervisor launch
registry; the engine starts, performs preflight, then reports `READY`. `stop`
asks the engine to finish cleanly; `abort` asks it to end immediately and report
`ABORTED`. Mutating request IDs are idempotent: VStim caches the last 128
acknowledgements, returns the original acknowledgement for the same ID/body,
and rejects reuse of an ID with a different command. The supervisor also
persists up to 128 acknowledgements so an HTTP retry after a supervisor restart
returns the previous result without reissuing the engine action.

| Operation | VStim implementation at tested commit | Monitor v1 behavior |
|---|---|---|
| Schema | `CONTROL_SCHEMA_VERSION = 1`; accepts omitted schema as legacy v1 | Always sends integer `1`; rejects absent, non-integer, or unsupported response versions |
| Request ID | Mutations require an ID; status accepts an optional ID and echoes it | Sends a fresh ID for status and uses the operator ID for mutations; requires an exact echo |
| Status | Returns `ok`, `state`, and nested `status` | Validates envelope and required lifecycle status fields |
| Session identity | Status reports the launched `session_id` | Must match the persisted monitor session ID before adoption |
| Engine protocol identity | Status reports `natural-images` | Must match saved `engine_protocol_id`; monitor ID remains `natural_images` |
| Lifecycle | Reports PREPARING/READY/RUNNING/FINISHING/COMPLETE/ERROR/ABORTED | Uses the engine state while control is reachable |
| Capabilities | Runtime capabilities are filtered by lifecycle | GUI enables only advertised capabilities |
| Timing | `elapsed_s` and `eta_s` are status fields; ETA may be null | Reads from control status; telemetry is not authoritative |
| Start / stop / abort | Lifecycle-checked commands with acknowledgements | Sends only when current runtime capability allows the action |
| Errors | `ok: false`, current state, structured `error.code` and `error.message` | Rejects the command and shows a concise error; retains full process logs |
| Reconnect | A new status request reports the current engine state | Supervisor revalidates PID identity, then queries the existing control socket |
| Duplicate mutation | Last 128 IDs are cached; same ID/body returns original ack; changed body is rejected | Persists the last 128 acknowledgements and does not repeat a retried action |

### Durable terminal recovery

Live control remains the first authority. If a task advertises a trusted
`control_state_flag` in its server-side registry entry, the supervisor creates
a fresh per-session path under `<monitor-state-dir>/control-state/`, passes it
to the task, and records the path with the exact launch identity. The directory
is mode `0700`; the engine artifact is mode `0600`. The browser cannot choose or
override either value. Files remain beside supervisor state for run history and
debugging; the monitor does not delete them automatically.

For VStim, the file is a separate engine-authored
`rpi_vstim.operator_control_state` artifact. It includes schema version 1,
session and protocol IDs, lifecycle and terminal flag, update and terminal
timestamps, engine PID, control-instance ID, START/STOP/ABORT metadata, terminal
reason, concise error summary, and session output path. VStim refuses to create
the first snapshot over an existing path. It writes a synced temporary file,
then atomically creates or replaces the state file. Terminal state is persisted
before the engine signals completion and closes its control socket.

Before a task exits, the monitor learns `control_instance_id` from live status
or from a matching engine state snapshot while the verified process is alive.
After live control becomes unavailable, the monitor accepts a file only when
artifact/schema version, session ID, engine protocol ID, engine PID, control
instance ID, file owner, and mode match the saved launch. Both lifecycle fields
must agree, `terminal` must be true, and the lifecycle must be `COMPLETE`,
`ABORTED`, or `ERROR`. Malformed, stale, mismatched, and nonterminal snapshots
are rejected with a short recovery diagnostic. A post-exit `READY`, `RUNNING`,
or `FINISHING` snapshot does not prove a scientific outcome.

Exit codes are bookkeeping only. The monitor never infers scientific lifecycle
from exit code, stdout, session folders, or VStim-specific scientific files. If
there is no valid engine terminal snapshot, it reports process exit without a
recoverable terminal state rather than inventing COMPLETE or ABORTED.

## Status authority

- Engine-authoritative: lifecycle, capabilities, elapsed time, ETA, and
  protocol-specific state from live Unix control; after control disappears,
  only an identity-checked terminal state file can supply final lifecycle.
- Supervisor-derived: process existence and identity, saved launch argv/metadata,
  control connectivity, and persistent stdout/stderr paths.
- Telemetry-observational: hardware snapshots, integrity snapshots, recent event
  packets, and `FRESH` / `STALE` / `NONE` freshness.

Telemetry never changes supervisor lifecycle. When control is reachable, the
GUI uses the engine lifecycle even if telemetry reports an older lifecycle.

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

The monitor's trusted registry implements `unix_json_v1`. It starts a registered
executable with structured argv and supplies `--control-socket`, `--session-id`,
and `--telemetry-port`. A protocol that implements durable terminal recovery
may declare a trusted CLI flag such as `control_state_flag: "--control-state-file"`;
the registry also records that task's expected `control_state_artifact_type`.
The supervisor supplies its own unique path and validates against the saved
artifact type on recovery. This is optional, so existing fake and external
fixtures without durable-state support remain compatible. The task owns its Unix socket and authoritative
lifecycle. Its status response contains `protocol`, `session_id`, `state`, and
runtime `capabilities`; elapsed/ETA may be null when not known. All responses
are schema- and request-correlated. The monitor checks that envelope before
using `ok`, validates engine session/protocol/state identity, and persists the
resolved launch identity. Control status uses `state`; UDP telemetry uses
`lifecycle` and remains observational.

Stable registry IDs belong to the monitor. If an engine uses a different
protocol ID, the registry stores that as `engine_protocol_id` and the supervisor
verifies the status response against the saved engine ID. This keeps IDs such as
`natural_images` stable while accommodating an engine-native name such as
`natural-images`. The exact registry entry and resolved argv are persisted so
registry edits cannot reinterpret an already-running session.

## Natural Images software-only preset

The `natural_images` registry entry uses engine protocol `natural-images`,
adapter `unix_json_v1`, and preset `operator_simulation`. A trusted deployment
registry supplies its executable, working directory, and the
`config/rigs/natural_images.operator_simulation.toml` path. Its fixed argv uses
`natural-images run --operator-control --simulate --image-count 5`, an explicit
temporary output root, and the validated subject ID as `--mouse-id`. The
integration asserts `--simulate` is present. PREPARE/READY, START/RUNNING,
reconnect, STOP/COMPLETE, and READY -> ABORT have been exercised software-only
against VStim commit `9931500ab4d603ea412e119b065372ba3c887d3d`. The preset uses
the engine's durable state file to recover STOP, ABORT, natural completion, and
preparation ERROR after the socket closes. No physical Natural Images preset
is registered.

In that engine version, READY runs configuration, asset, and plan preflight but
does not create a session, frame clock, KMS acquisition, cameras, GPIO, or audio.
START creates the simulated session and uses `FakeDisplayBackend`. STOP and
ABORT finalize through the engine lifecycle. Simulation timestamps are planned
values and are not physical presentation timing. The monitor remains a process
supervisor and operator view, not the scientific clock.

The monitor's built-in fake task remains available independently. Separately
installed task repositories can use the same adapter without being imported by
the monitor or merged into `rpi_vstim_suite`; see
[`external_protocols.md`](external_protocols.md).
