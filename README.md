# rpi_rig_monitor

Read-only monitoring dashboard for Raspberry Pi behavioral/visual-stimulus rigs.

The monitor is observational only. Experimental controllers must never depend on the monitor being available; telemetry may be dropped. No control commands are sent back to experiment hardware. Phase 1 is LAN/local monitoring only.

## Requirements

Python 3.7+. The code uses Python 3.7-era syntax, and `requirements.txt` fully pins the Flask stack to versions compatible with the Raspberry Pi 4B deployment target.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Run with demo telemetry

In terminal 1:

```bash
python run_monitor.py
```

In terminal 2:

```bash
python tools/send_demo_telemetry.py
```

Open [http://localhost:8080](http://localhost:8080). From another machine on the same LAN, open `http://<control-pi-lan-ip>:8080`.

The receiver listens on UDP `0.0.0.0:5055`; the dashboard listens on `0.0.0.0:8080`. Override these with `--udp-host`, `--udp-port`, `--web-host`, `--web-port`, and `--stale-after-sec` (or the corresponding `RIG_MONITOR_*` environment variables).

## Manual Phase 1 acceptance test

1. Terminal 1: run `python run_monitor.py`.
2. Terminal 2: run `python tools/send_demo_telemetry.py`.
3. Open `http://localhost:8080`, or `http://<control-pi-ip>:8080` from another machine on the LAN.
4. Verify that the dashboard loads, `LIVE` appears, trials increment, phases change, image filenames change, the last-trial summary updates, and recent history grows. Confirm that reward/omission/delivery fields agree and the anticipatory-lick label agrees with the emitted `lick_times_sec` window.

The demo is a finite session and stops after `--total-trials` completed trials. Use `--session-id` to select a stable ID; otherwise each invocation gets a UTC-based unique demo ID.

## Telemetry

Telemetry is UTF-8 JSON over UDP. Phase 1 recognizes `state`, `trial_complete`, and `session` messages. The backend retains up to 50 completed trials; the browser displays the newest 20. If no packet arrives for five seconds, the UI marks telemetry `STALE` while preserving the last known state.

## Tests

Run the standard-library test suite with:

```bash
python -m unittest discover -s tests -v
```

## Phase 1 non-goals

There is no camera streaming, browser-to-rig control, remote command execution, persistent database, authentication, WebSockets, or communication back to Box151 or other experiment hardware.
