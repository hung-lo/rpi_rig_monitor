# Registering an external protocol

The monitor does not import experiment packages. A protocol can run from
`rpi_vstim_suite`, a separately installed Python package, or another
repository. The registry starts the approved executable as a separate process;
the process implements the configured local control adapter.

## Deployment registry

Set `RIG_MONITOR_PROTOCOL_REGISTRY=/etc/rpi-rig-monitor/protocols.json` in the
supervisor service environment, or pass `--protocol-registry` when starting
`monitor.supervisor`. The JSON file is trusted executable configuration and
must be writable only by the rig administrator. The built-in `fake` entry is
always present; external entries are merged into it.

Schema version 1 accepts these fields:

```json
{
  "schema_version": 1,
  "protocols": [
    {
      "protocol_id": "external_demo",
      "display_name": "External Demo",
      "description": "Example separate task process",
      "adapter_type": "unix_json_v1",
      "executable": ["/opt/external-demo/bin/external-demo"],
      "working_directory": "/opt/external-demo",
      "runtime_fields": [
        {"name": "subject_id", "display_name": "Subject / Mouse ID", "required": true,
         "type": "subject_id", "cli_flag": "--subject-id"}
      ],
      "presets": [
        {"preset_id": "standard", "display_name": "Standard", "description": "Approved session",
         "arguments": ["--profile", "standard", "--config", "/opt/external-demo/config/standard.toml"],
         "required_files": ["/opt/external-demo/config/standard.toml"]}
      ]
    }
  ]
}
```

Executable and preset argv are arrays of fixed strings. Relative paths in
`working_directory` and `required_files` resolve relative to the registry file;
relative executable paths do too. A command name such as `rpi-vstim` resolves
through the supervisor's `PATH`. Static preset arguments are trusted and never
come from a browser request. The current runtime field schema supports the
validated `subject_id` type; unknown fields and types are rejected.

The supervisor provides `--control-socket`, `--session-id`, and
`--telemetry-port` after the registered arguments for `unix_json_v1` tasks. The
supervisor creates the session ID and passes the same ID to the task. It records
the stable monitor `protocol_id`, optional engine-native `engine_protocol_id`,
`preset_id`, subject ID, adapter, cwd, and exact resolved argv. On recovery it
uses that saved record and queries the existing task socket; it does not rebuild
or launch from the current registry.

The task process inherits the supervisor service environment, with
`RIG_MONITOR_CONTROL_TOKEN` removed. The browser cannot alter the executable,
arguments, cwd, environment, socket path, preset configuration, or session ID.
The supervisor always uses `shell=False`.

Missing executables, working directories, and required preset files make only
the affected protocol or preset unavailable. The registry API reports a short
reason without revealing filesystem paths or argv. Malformed definitions,
duplicate IDs, and unsupported adapters fail supervisor startup; Flask then
reports the supervisor as unavailable. A valid registry reload affects only
future launches. A saved active task remains adoptable even if its entry is
removed or changed.

## Control adapter

`unix_json_v1` uses one UTF-8 JSON request per Unix-socket connection. The task
must implement `status`, `start`, `stop`, and `abort`; status returns the task's
session ID, engine protocol ID, lifecycle, elapsed/ETA values when known, and
runtime capabilities. Mutating requests include a request ID and return an
acknowledgement. The task validates every command against its lifecycle and
deduplicates retries. Runtime capabilities come from the task, not the registry
or preset. See [the control contract](rpi_vstim_interface.md).

The task owns scientific timing, hardware, safe cleanup, and authoritative
scientific logs. It must continue independently of the browser and Flask. The
monitor owns process launch and observation; it never imports the task, controls
GPIO, or becomes a timing source.

## Future Natural Images registration (not enabled)

The intended stable monitor ID is `natural_images`; the VStim CLI currently
reports the engine-native protocol name `natural-images`. The registry supports
that distinction explicitly:

```yaml
protocol_id: natural_images
engine_protocol_id: natural-images
display_name: Natural Images
adapter_type: unix_json_v1
executable: [rpi-vstim]
preset: short_validation
```

This is illustrative only and is not a runnable monitor entry. Real Natural
Images integration remains pending a safe hardware-free VStim operator-control
simulation path. The monitor must not register it as available until that path
and its control lifecycle are validated.

## Separate Go/NoGo repository

A future Go/NoGo repository needs an installed executable, an approved preset
and config, a `unix_json_v1` control endpoint, lifecycle/status/capability
reporting, validated semantic actions where supported, safe cleanup, and its own
protocol-specific scientific logs. Deployment then adds a trusted registry
entry pointing at that installation. The repository does not need to be
imported by the monitor or merged into `rpi_vstim_suite`.
