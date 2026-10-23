# Session settings, profiles, and manual bait

## Supported session setting

The registry drives both the editor and launch construction. The first and only
Natural Images setting is:

| ID | Type | Allowed range | Preset default | Engine argument |
| --- | --- | --- | --- | --- |
| `image_count` | integer | 1–2800 | 5 for `operator_simulation`; 20 for each Pi5 smoke preset | `--image-count` |

The Natural Images runner already uses this value to select from the seeded
session plan. The monitor rejects extra settings and does not accept browser
provided argv, config paths, environment, or working directories. No rule
switch, reward, or trial count setting is exposed because those settings are
not part of the current plain Natural Images run contract.

The current fixed presets remain the same: `operator_simulation`,
`pi5_hardware_smoke`, and `pi5_hardware_smoke_licks`. The settings control
changes only the validated `image_count`; it does not turn on cameras, lick
GPIO, KMS, audio, reward, or other preset options.

## Saved profiles

Profiles are scoped to a protocol and base preset. Each file uses JSON schema
version 1 and contains a generated safe profile ID, name, version, typed
parameter values, and timestamps. The supervisor stores profiles in:

```text
<supervisor-state-dir>/profiles/<protocol_id>/<preset_id>/<profile_id>.json
```

In the example systemd deployment this is under
`/var/lib/rpi-rig-monitor/profiles/`. Files use mode 0600, directories use
mode 0700, and replacement is atomic. Profile mutations use the monitor's
bearer-token authorization. An optimistic version number prevents overwriting
another operator's update. A corrupted or deprecated profile is marked
invalid; the monitor does not silently replace its values with defaults.
Supported values can be corrected explicitly, saved as a new profile, or the
invalid profile can be deleted after confirmation.

The browser persists only the selected protocol, preset, and profile ID as a
convenience. Profile contents remain in supervisor storage. A profile edit
never changes an active task. PREPARE resolves one immutable launch specification
and persists its parameter values, profile identity/version, and explicit
overrides. VStim records that snapshot in `provenance.json` and
`session_manifest.json`, along with the effective resolved config and seeded
plan. Later profile changes do not change those session artifacts.

## Manual bait status and safety

The existing control API supports the semantic `bait_left` and `bait_right`
actions. The browser renders a channel only if the active engine advertises the
matching runtime capability. Commands travel through authenticated Flask,
the persistent supervisor, and the experiment control socket. The monitor has
no GPIO import or output path.

The current Natural Images runner does not connect remote actions to its
reward arbiter and advertises no bait capability. All three Natural Images
presets keep reward outputs disabled; the monitor therefore shows an explanatory
unavailable message and no active bait button. Fake task bait is simulated and
does not deliver water. An HTTP/supervisor acknowledgement is shown as engine
acceptance only. If the engine cannot report delivery completion, the UI says
that completion is unknown and does not claim water was delivered.

The engine's calibrated reward controller, reward arbiter, manual water budget,
source attribution, and optional GPIO23 button remain engine-owned. They are
not enabled by this monitor work. A reward-capable task must wire GUI actions
through the same arbiter as task rewards and any physical operator button,
validate READY/RUNNING state and calibration, enforce serialization and the
manual water cap, and publish accepted/completed/failed status before its
capability can be advertised. Natural Images will require a separate explicit
reward-enabled mode if that is ever scientifically approved.

## Deployment example

No deployment is performed as part of this change. When approved, first deploy
both repositories while the rig is idle and retain a copy of the current
registry:

```sh
sudo cp -a /etc/rpi-rig-monitor/protocols.json \
  /etc/rpi-rig-monitor/protocols.json.before-session-settings
```

Check out the reviewed monitor and VStim commits in their Pi checkouts and use
the existing VStim installation procedure to update the editable package. Do
not replace the trusted registry with the example file. Merge the Natural
Images `parameters` schema and `session_metadata_flag` into the existing entry,
retaining its executable, paths, and preset arguments. Keep the persistent
supervisor state directory unchanged. After the operator confirms the rig is
idle, restart the supervisor and Flask service using the deployment's normal
procedure. Then verify `/api/protocols`, the 5/20/20 defaults, and the
`operator_simulation` prepare/start/complete flow before selecting a hardware
preset.

The example entry is
[`../deploy/natural_images_registry.example.json`](../deploy/natural_images_registry.example.json).
Confirm Natural Images still advertises `bait_left: false` and
`bait_right: false`.

## Rollback

When the rig is idle, restore the saved trusted registry and check out the
pre-feature code revisions:

```sh
git -C /home/pi/rpi_rig_monitor checkout 6f7a609eacf52b8423b0bfca96e7403ee2136d2f
git -C /home/pi/rpi_vstim_suite checkout 4d2453a12a733b1b16c1d98f473cf2e3366054cb
```

Use the established VStim package procedure, then restart the supervisor and
Flask service through the local deployment procedure. Verify the previous
protocol list and preset defaults. Do not delete the profile directory during
rollback; profiles are inert templates and may be useful if the feature is
re-enabled. Do not change or remove existing session artifacts. A running task
must be stopped or allowed to finish through its engine control path before
deployment rollback.

## Tests and limitations

Monitor tests use temporary state directories and fake/external processes.
VStim metadata tests use temporary mode-0600 files. The optional cross-repository
Natural Images integration uses `--simulate` and `FakeDisplayBackend`. These
tests do not acquire GPIO or prove physical reward calibration. No manual bait
capability is currently advertised by a real registered Pi5 protocol.
