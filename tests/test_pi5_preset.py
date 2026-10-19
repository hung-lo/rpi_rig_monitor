import json
import sys
from pathlib import Path

from monitor.registry import ProtocolRegistry


def test_pi5_licks_preset_resolves_only_opt_in_inputs_and_supervisor_runtime(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    preset = json.loads(
        (repository / "deploy/pi5_hardware_smoke_licks.preset.example.json").read_text()
    )
    expected_flags = [
        "natural-images", "run", "--config",
        "/home/pi/rpi_vstim_suite/config/rigs/pi5_natural_image_demo.example.toml",
        "--image-count", "20", "--cameras", "--licks", "--operator-control",
        "--output-root", "/mnt/behavbox_ssd/sessions",
    ]
    assert preset["preset_id"] == "pi5_hardware_smoke_licks"
    assert preset["display_name"] == "Pi5 hardware smoke + licks"
    assert preset["arguments"] == expected_flags
    assert not {"--simulate", "--audio-coexistence", "--camera-preview"} & set(preset["arguments"])
    assert not {"--session-id", "--control-socket", "--telemetry-port", "--control-state-file"} & set(preset["arguments"])

    config = tmp_path / "rig.toml"
    config.write_text("[rig]\n")
    preset["arguments"][3] = str(config)
    preset["config_path"] = str(config)
    preset["required_files"] = [str(config)]
    registry_file = tmp_path / "protocols.json"
    registry_file.write_text(json.dumps({
        "schema_version": 1,
        "protocols": [{
            "protocol_id": "natural_images", "display_name": "Natural Images",
            "description": "Pi5 Natural Images", "adapter_type": "unix_json_v1",
            "engine_protocol_id": "natural-images",
            "executable": [sys.executable], "working_directory": str(tmp_path),
            "control_state_flag": "--control-state-file",
            "control_state_artifact_type": "rpi_vstim.operator_control_state",
            "runtime_fields": [{
                "name": "subject_id", "display_name": "Subject / Mouse ID",
                "required": True, "type": "subject_id", "cli_flag": "--mouse-id",
            }],
            "presets": [preset],
        }],
    }))
    registry = ProtocolRegistry.from_json_file(registry_file)
    launch = registry.resolve(
        "natural_images", preset["preset_id"], "supervisor-session",
        "/run/supervisor.sock", 5055, {"subject_id": "mouse-1"},
        "/run/control-state/supervisor-session.json",
    )
    assert launch.argv[-10:] == tuple([
        "--mouse-id", "mouse-1", "--control-socket", "/run/supervisor.sock",
        "--session-id", "supervisor-session", "--telemetry-port", "5055",
        "--control-state-file", "/run/control-state/supervisor-session.json",
    ])
    expected_resolved = list(expected_flags)
    expected_resolved[3] = str(config)
    assert launch.argv[1:12] == tuple(expected_resolved)
