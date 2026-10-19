# Pi5 Natural Images operator integration

This handoff completes the software path for the VStim source image preview,
left/right lick observation, and an opt-in licks preset. No Pi, GPIO, camera,
KMS display, service, session data, or active registry was accessed or changed.

## Source changes

VStim adds a read-only corpus/session identity audit and publishes the current
plan segment in the existing lifecycle telemetry. The image filename and
zero-based corpus index come from the already-seeded plan; `sequence_index` is
the one-based trial number. Gray segments clear all image fields. Lick
snapshots use the existing classifier callbacks and run on the existing
telemetry cadence; GPIO callbacks do not send packets or encode JSON.

The monitor serves a PNG only when the requested session and filename appear
in that session's `plan.json`, `selected_image_table.json`, and
`asset_manifest.json`, and the canonical file hash matches the selected-table
hash. Natural Images uses `RIG_MONITOR_NATURAL_IMAGE_DIR` (default
`/home/pi/rpi_vstim_suite/assets/natural_images/stringer_natimg2800_center_crop`);
the legacy preview route keeps its existing `RIG_MONITOR_IMAGE_DIR`. The
read-only endpoint is:

```text
/api/stimulus-image/<session_id>/<natimg_center_NNNN.png>
```

The browser requests an image only when the session/image identity changes,
clears it for PRE/ITI/POST gray, stale data, or terminal state, and displays
telemetry freshness and sequence number. This is a source-PNG preview; it does
not confirm framebuffer pixels, physical onset, or photodiode timing.

## Telemetry fields

Fields are optional additions to the existing lifecycle packet. Old telemetry
continues to populate the legacy monitor fields.

| Field | Meaning |
| --- | --- |
| `stimulus_state.phase` | `READY`, `PRE`, `TASK`, `ITI`, `POST`, or terminal lifecycle state |
| `stimulus_state.stimulus_kind` | `gray` or `natural_image` |
| `stimulus_state.image_id` | Stable ID such as `natimg_center_0007`; null during gray |
| `stimulus_state.image_index` | Zero-based source image index; null during gray |
| `stimulus_state.image_filename` | Canonical PNG filename; null during gray |
| `stimulus_state.sequence_index` | One-based trial/sequence index when known |
| `lick_state.requested` | Whether this run opted into `--licks` |
| `lick_state.channels.left/right.available` | Whether that GPIO input initialized successfully |
| `lick_state.channels.left/right.status` | `available`, `not_requested`, `not_available`, or `session_ended` |
| `lick_state.channels.left/right.contact_active` | Latest edge-derived contact state, or null if unavailable |
| `lick_state.channels.left/right.last_lick_age_sec` | Age of the most recent classified onset, from monotonic time |
| `lick_state.channels.left/right.recent_lick_count_2s` | Classified onsets in the trailing two seconds |
| `lick_state.channels.left/right.total_lick_onset_count_session` | Classified onsets in this session |
| `lick_state.channels.center.status` | Always `not_configured` until validated and implemented |

Aggregate `licking_active` means either left or right contact is active;
`last_lick_age_sec` is the younger (most recent) channel onset age;
`recent_lick_count_2s` and `total_lick_onset_count_session` are left plus
right. These same four aggregate values remain at packet top level for legacy
consumers. Counts are classified lick onsets, not raw transitions. When licks
were not requested or a sensor did not initialize, counts are null rather than
misleading zeros. Periodic snapshots carry state and counters independently,
so a later packet repairs UI counts after packet loss.

The monitor shows left and right separately. A recent-onset marker persists
for 1.5 seconds; held contact is shown independently. Stale packets gray the
live indicators and mark retained counts stale. COMPLETE/STOP/ABORT clears live
contact/onset indicators. The monitor is observational and cannot control GPIO
or reward outputs.

## Image corpus and session identity

The local VStim corpus audit passed for all 2,800 files:

- Names are exactly `natimg_center_0000.png` through `natimg_center_2799.png`.
- Each file is an 8-bit grayscale PNG at 90 x 68 pixels.
- Every SHA-256 matches the repository `MANIFEST.sha256`.
- Manifest digest: `dca481282f9f6a1d0a281f4639fa96661b00f35748e0972f96205553898aa8fe`.
- The frozen 1024 x 600 crop transform passed representative checks for
  images 0000, 1400, and 2799; all 2,800 images decoded.

The repository documents the Stringer source dataset (DOI
[10.25378/janelia.6845348](https://doi.org/10.25378/janelia.6845348)), its
CC BY-NC 4.0 license, and the centered crop conversion. The copied Box151
legacy corpus, Pi5 corpus, and successful session directory were not available
in this workspace. Therefore their SHA comparison, deployed `asset_root`, and
selected images from session `session-5a752b1744f84af2` remain pending. No
images were copied or changed. After copying permitted audit inputs locally,
run:

```bash
cd /home/pi/rpi_vstim_suite
PYTHONPATH=src .venv/bin/python scripts/validate_natural_image_assets.py \
  --asset-root assets/natural_images/stringer_natimg2800_center_crop \
  --width 1024 --height 600 \
  --legacy-root /path/to/copied/legacy/stringer_natimg2800_center_crop_png \
  --session-dir /path/to/copied/session-5a752b1744f84af2
```

The legacy comparison is read-only. If it reports identical filenames and
hashes, no copy is needed. If files differ or are missing, stage only an
operator-reviewed copy after the dry-run below, then rerun the corpus audit:

```bash
rsync -avn --checksum --ignore-existing --itemize-changes /path/to/approved/corpus/ \
  pi@10.42.84.10:/home/pi/rpi_vstim_suite/assets/natural_images/stringer_natimg2800_center_crop/

(cd /path/to/approved/corpus && sha256sum -c MANIFEST.sha256)
ssh pi@10.42.84.10 'cd /home/pi/rpi_vstim_suite/assets/natural_images/stringer_natimg2800_center_crop && sha256sum -c MANIFEST.sha256'
```

After review, the copy form is the same command with `-n` removed. It adds
missing files only; `--ignore-existing` will not replace mismatches. Back up
and replace a mismatched file only after an operator has reviewed its source
hash. Run the full asset validator on the Pi after any copy. Never overwrite an
existing Pi corpus from Box151 automatically.

## Opt-in hardware preset

[`../deploy/pi5_hardware_smoke_licks.preset.example.json`](../deploy/pi5_hardware_smoke_licks.preset.example.json)
contains only the new `pi5_hardware_smoke_licks` preset. Append that preset
object under the existing `natural_images` protocol's `presets` array in the
trusted registry. Keep `operator_simulation` and `pi5_hardware_smoke` exactly
as they are. The fragment requests real KMS, both cameras, and `--licks`; it
contains no subject/session ID or supervisor runtime paths and is not a
default. It adds no reward, suction, or airpuff control. BCM9 remains reserved
for NeuroKairos IRIG.

## Automated checks

| Repository | Result | Skips |
| --- | ---: | ---: |
| `rpi_vstim_suite` | 383 passed | 6 |
| `rpi_rig_monitor` | 76 passed | 2 |

Both suites ran with Python 3.11.16 from the available local virtualenvs.
Python 3.13.15 `compileall` passed for VStim, but the local 3.13 interpreter
does not have pytest or Pillow installed. The monitor's two
cross-repository VStim integration tests were skipped because their configured
VStim checkout/executable environment was unavailable. The automated lick
tests use fakes only; they do not validate physical sensors. No hardware tests
were performed.

## Manual deployment

These commands are for a later approved maintenance window. The changes are
committed locally but not pushed or deployed. First publish both reviewed
branches from the parent checkout:

```bash
git -C /home/hunglo/Codex_folder/rpi_vstim_suite push origin codex/operator-control-api
git -C /home/hunglo/Codex_folder/rpi_rig_monitor push origin codex/vstim-natural-images-integration
```

On the Pi, confirm no session is active and preserve any local changes before
updating either checkout:

```bash
cd /home/pi/rpi_vstim_suite
git status --short
git fetch origin
git switch codex/operator-control-api
git pull --ff-only origin codex/operator-control-api

cd /home/pi/rpi_rig_monitor
git status --short
git fetch origin
git switch codex/vstim-natural-images-integration
git pull --ff-only origin codex/vstim-natural-images-integration
```

Validate the corpus on the Pi with the deployed Python environment, then append
the example preset object to `/etc/rpi-rig-monitor/protocols.json` under the
existing `natural_images.presets` array. Preserve all other registry entries
and keep the file administrator-writable only. Validate the resulting registry
and confirm the new preset is available before restarting the supervisor:

```bash
cd /home/pi/rpi_rig_monitor
.venv/bin/python -c 'from monitor.registry import ProtocolRegistry; r=ProtocolRegistry.from_json_file("/etc/rpi-rig-monitor/protocols.json"); p=next(p for p in r.public_metadata() if p["protocol_id"] == "natural_images"); assert any(x["preset_id"] == "pi5_hardware_smoke_licks" and x["available"] for x in p["presets"]); print("licks preset available")'
```

The Flask process must use the new code and the default
`RIG_MONITOR_NATURAL_IMAGE_DIR` and `RIG_MONITOR_SESSION_ROOT` values, or set
those environment variables explicitly. The local repo does not define the
Flask systemd unit name; identify the site's existing unit before restarting
it. Restart services only after confirming the rig is idle:

```bash
sudo systemctl restart rpi-rig-supervisor.service
sudo systemctl restart <existing-Flask-monitor-unit>
```

In the dashboard, select **Pi5 hardware smoke + licks**, enter the subject ID,
PREPARE and verify READY, then START. Runtime session ID, control socket,
telemetry port, and control-state file remain supervisor-managed.

## Rollback

After safely ending any run, remove only `pi5_hardware_smoke_licks` from the
trusted registry's `natural_images.presets`, validate the JSON, then restart
the supervisor and existing Flask unit. To restore the pre-change source
baseline, first preserve any local changes and use the recorded baseline
commits (never `reset --hard` or `git clean`):

```bash
git -C /home/pi/rpi_vstim_suite switch --detach 9931500ab4d603ea412e119b065372ba3c887d3d
git -C /home/pi/rpi_rig_monitor switch --detach fa0c2e74b6f07b6965c695a434341aa5d7952b22
sudo systemctl restart rpi-rig-supervisor.service
sudo systemctl restart <existing-Flask-monitor-unit>
```

## Physical validation still required

- Confirm the GUI PNG preview corresponds to the image on the physical display.
- Touch left and right sensors independently; verify the correct channel,
  contact state, recent onset, and counters.
- Compare monitor counts with classified `LICK` entries in `events.jsonl`.
- Confirm no unexpected GPIO output activity; the monitor must remain read-only.
- Confirm both cameras still record.
- Verify recorded image identities and hashes from session metadata.
- Measure visual onset with a photodiode and validate camera/stimulus sync.

These checks were not performed. A software preview and simulated lick events
do not establish physical stimulus onset, sensor operation, or synchronization.
