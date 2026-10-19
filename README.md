# rpi_rig_monitor

Operator GUI and persistent supervisor for a Raspberry Pi behavioral and
visual-stimulation rig. `rpi_vstim_suite` remains the sole owner of KMS, GPIO,
cameras, audio, reward timing, stimulus timing, and scientific event logging.
The monitor's UDP telemetry is observational and may be lost; closing a browser
or restarting Flask does not stop a supervised experiment.

The current launch registry contains only a hardware-free fake task. Real
protocol launch descriptors and engine control integration are future work.
See [the engine interface proposal](docs/rpi_vstim_interface.md).

## Requirements and installation

Python 3.7+ and the pinned Flask stack:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Run

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
These paths are configurable because this repository has no existing service
or runtime-directory convention; systemd integration is not included yet.

The supervisor is a detached local process. It launches each experiment in its
own process session and captures separate stdout/stderr logs. Flask reconnects
to its owner-only Unix socket on restart. The web control API requires a bearer
token and accepts only fixed semantic actions; it does not accept shell
commands, GPIO numbers, or arbitrary process arguments.

## Hardware-free operator exercise

Start the monitor with the token configured, open the dashboard, and enter the
same token. Select **Fake task (hardware-free)**, prepare, then verify the
session reaches `READY`. Try Bait left while READY, start the task, then test
Stop and Abort in separate sessions. The fake task writes operator bait as
`OPERATOR_REWARD` to its session log and never accesses hardware.

While a fake task is running, close the browser or restart `run_monitor.py`.
The task continues under the supervisor; reopening the dashboard reconnects to
the same session. UDP telemetry may be disabled or stale without changing task
state. Preparing again while a supervised task is active is rejected. An
active telemetry session that is not registered with the supervisor also
blocks new launches.

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

`/api/state` reports telemetry freshness and supervisor session state
separately. `/health` checks the UDP receiver and supervisor connection. A
healthy monitor does not certify hardware health; those values are reported by
the experiment engine.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

All supervisor and lifecycle tests use only the fake task and local sockets; no
GPIO, KMS, camera, or audio device is used.
