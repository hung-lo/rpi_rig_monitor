# rpi_rig_monitor

Operator GUI and persistent supervisor for a Raspberry Pi behavioral and
visual-stimulation rig. `rpi_vstim_suite` remains the sole owner of KMS, GPIO,
cameras, audio, reward timing, stimulus timing, and scientific event logging.
The monitor's UDP telemetry is observational and may be lost; closing a browser
or restarting Flask does not stop a supervised experiment.

The built-in registry exposes only a hardware-free fake task. An administrator
may add trusted definitions for separately installed tasks; real VStim and
Go/NoGo protocols are not registered. See
[external protocol setup](docs/external_protocols.md) and
[the engine interface proposal](docs/rpi_vstim_interface.md).

## Requirements and installation

Python 3.7+ and the pinned Flask stack:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Run

Start the supervisor and Flask in separate terminals. Flask is only a client; it
does not start or stop the supervisor automatically.

Terminal 1:

```bash
python3 -m monitor.supervisor
```

Terminal 2:

```bash
export RIG_MONITOR_CONTROL_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 run_monitor.py
```

Open [http://localhost:8080](http://localhost:8080). Enter the configured token
in the control panel to enable the actions advertised for the current session.
The token is held only in the browser page memory. Without it, dashboard and
telemetry remain available but control is disabled.

By default Flask listens on `0.0.0.0:8080`, and telemetry listens on UDP
`0.0.0.0:5055`. Override these with `--udp-host`, `--udp-port`, `--web-host`,
`--web-port`, and `--stale-after-sec` or the corresponding `RIG_MONITOR_*`
environment variables. The supervisor stores state and task logs under
`$XDG_STATE_HOME/rpi-rig-monitor` (default `~/.local/state/rpi-rig-monitor`).
Use `--supervisor-state-dir` and `--supervisor-socket` to set deployment paths.
The supervisor stores atomic state in that directory and owner-only logs in its
`logs` subdirectory.

The supervisor launches each experiment in its own process session and captures
separate stdout/stderr logs. It persists a schema version, session metadata,
argv, PID, Linux `/proc` start time and boot ID, process group/session IDs,
endpoint, and last known lifecycle using atomic file replacement. On restart it
adopts a task only when the PID identity and task endpoint's session/protocol
agree. A live process with an unavailable endpoint is shown as degraded and is left running.
Stale records and exited tasks do not block future launches.

The supervisor tracks process and connection health; the experiment endpoint is
authoritative for task lifecycle and capabilities. Telemetry freshness is
independent and reported as `FRESH`, `STALE`, or `NONE`. Losing telemetry does
not change task state.

### Trusted protocol registry

The supervisor always registers the fake task. Optional protocols load from a
trusted JSON file configured with `RIG_MONITOR_PROTOCOL_REGISTRY` or
`--protocol-registry`. The file contains fixed executable argv components,
preset arguments, working directories, required files, and explicitly allowed
runtime fields. Protect it as executable configuration. The browser receives
only protocol/preset descriptions, availability, and field labels; it cannot
set executable paths, argv, cwd, environment, configs, or control sockets.

Missing optional executables, working directories, or preset files mark the
affected protocol or preset unavailable and leave the monitor running. Invalid
registry structure, unsafe IDs, duplicate IDs, and unsupported adapters fail
supervisor startup. The public registry API does not expose machine paths or
argv. See [`docs/external_protocols.md`](docs/external_protocols.md) for the
file format.

### systemd deployment

An example unit is in
[`deploy/rpi-rig-supervisor.service`](deploy/rpi-rig-supervisor.service). On a
Pi, install the repo at `/opt/rpi_rig_monitor`, adjust `User` and `Group` to the
local rig operator account, then install and enable the unit:

```bash
sudo install -m 0644 deploy/rpi-rig-supervisor.service /etc/systemd/system/rpi-rig-supervisor.service
sudo systemctl daemon-reload
sudo systemctl enable --now rpi-rig-supervisor.service
```

The unit keeps session state/logs in `/var/lib/rpi-rig-monitor` and its
supervisor socket in `/run/rpi-rig-monitor`. Configure Flask with matching
paths, for example:

```bash
RIG_MONITOR_SUPERVISOR_STATE_DIR=/var/lib/rpi-rig-monitor \
RIG_MONITOR_SUPERVISOR_SOCKET=/run/rpi-rig-monitor/supervisor.sock \
python3 run_monitor.py
```

`KillMode=process` is deliberate: `systemctl restart rpi-rig-supervisor` stops
the supervisor process without killing its detached experiment. Use the
operator control API to stop or abort a task. Run the web service independently
as the same local operator account so it can access the mode-0600 socket. If the
supervisor cannot be reached, Flask reports it unavailable and keeps controls
disabled. A host reboot does not preserve a running experiment process.

### Prototype security boundary

This is for a trusted lab LAN and local operator, not public internet exposure.
Keep the bearer token on the server and enter it in the page's control field;
the browser sends it only in the `Authorization` header, never a URL. The
backend does not log the token. Control requires a request ID and accepts only
registered protocol IDs and fixed actions. Telemetry UDP remains observational
and is never a command path.

## Hardware-free operator exercise

Start the monitor with the token configured, open the dashboard, and enter the
same token. Select **Fake task (hardware-free)** and its **Standard** preset,
enter an optional subject ID such as `example_mouse`, prepare, then verify the
session reaches `READY`. Try Bait left while READY, start the task, then test
Stop and Abort in separate sessions. The fake task writes operator bait as
`OPERATOR_REWARD` to its session log and never accesses hardware.

While a fake task is running, close the browser or restart `run_monitor.py`.
The task continues; Flask reconnects to the existing supervisor. Restarting the
supervisor also leaves the task running: the new supervisor verifies its saved
Linux process identity and reconnects to its control socket. UDP telemetry may
be disabled or stale without changing task state. A second launch is rejected
until the known task is terminal. An active telemetry session not registered
with the supervisor also blocks new launches.

## Existing telemetry demos

Run the reward-conditioning-style demo in a second terminal:

```bash
python3 tools/send_demo_telemetry.py
```

The demo is finite and stops after `--total-trials` completed trials. Use
`--session-id` to select a stable ID; otherwise each invocation gets a unique
UTC-based ID. `--contingency-phase acquisition` and `--contingency-phase
reversal` exercise the partial-reversal display; `--legacy` exercises the
older counter shape. Synthetic reward volume is `3.0 µL/train`, not a real
calibration.

The spout-training telemetry view can be exercised with:

```bash
python3 tools/send_spout_demo_telemetry.py
```

The current dashboard retains its legacy reward-conditioning and spout-training
detail panels. Cumulative counters come from telemetry and are never rebuilt by
counting UDP packets. “Likely consumed” is a contact-based estimate, not a
direct intake measurement. Stimulus previews use a local natural-image library
at `/home/pi/stimulus_assets/stringer_natimg2800_center_crop_png` by default;
set `--image-dir` or `RIG_MONITOR_IMAGE_DIR` to use another directory.

## Telemetry and status

Telemetry is UTF-8 JSON over UDP. The backend accepts `state`, `trial_complete`,
`session`, and `event` messages, retains up to 50 recent trials/events, and
preserves unknown protocol fields in a generic state envelope. The browser polls
the API every 300 ms. After five seconds without a packet by default, telemetry
shows `STALE` while the last known values remain visible.

`/api/state` reports `FRESH`, `STALE`, or `NONE` telemetry separately from
supervisor process/control connectivity and task lifecycle. `/health` checks
the UDP receiver and supervisor connection. A healthy monitor does not certify
hardware health; those values are reported by the experiment engine.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Supervisor, registry, and external-process fixture tests use local processes
and sockets; no GPIO, KMS, camera, or audio device is used.
