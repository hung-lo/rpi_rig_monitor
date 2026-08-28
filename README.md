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

## Phase 2A local image preview

The dashboard can preview the current stimulus from a local copy of the natural-image library. The library is deliberately not included in this repository. On the current Control Pi, the default directory is `/home/pi/stimulus_assets/stringer_natimg2800_center_crop_png`.

Configure another directory with:

```bash
python run_monitor.py --image-dir /some/image/directory
```

or:

```bash
export RIG_MONITOR_IMAGE_DIR=/some/image/directory
python run_monitor.py
```

Telemetry sends only the image filename; the Control Pi browser preview serves the corresponding file from its local copy. If an image is missing, telemetry and the experiment are unaffected and the dashboard shows `Image preview unavailable`. The preview is an operator reference, not scientific stimulus-onset timing.

## Manual Phase 1 acceptance test

1. Terminal 1: run `python run_monitor.py`.
2. Terminal 2: run `python tools/send_demo_telemetry.py`.
3. Open `http://localhost:8080`, or `http://<control-pi-ip>:8080` from another machine on the LAN.
4. While the demo is running, verify that the dashboard loads, `LIVE` appears, trials increment, phases change, image filenames change, the last-trial summary updates, and recent history grows. Confirm that reward/omission/delivery fields agree and the anticipatory-lick label agrees with the emitted `lick_times_sec` window.
5. After the finite demo sender exits, wait approximately the configured stale threshold (about five seconds by default). Verify that `LIVE` changes to `STALE`, while the last known state and recent history remain visible.
6. Start the demo sender again with `python tools/send_demo_telemetry.py`. Verify that `STALE` changes to `LIVE`, a new session ID appears, old recent-trial history is cleared, and old image/trial/block/ETA values do not leak into the new session.
7. While the monitor receiver is healthy, request `GET /health` and verify HTTP 200 with a response such as `{"status":"ok","udp_receiver":"ok"}`.

`/api/state` `LIVE`/`STALE` reports telemetry freshness from the experiment or demo sender. `/health` reports the health of the monitor process and UDP receiver. These are intentionally separate concepts.

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
