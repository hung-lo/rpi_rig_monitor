import json
import sys
import tempfile
import unittest
from pathlib import Path

from monitor.registry import (
    Preset,
    Protocol,
    ProtocolRegistry,
    RegistryError,
    RuntimeField,
    default_registry,
    validate_subject_id,
)


class RegistryTests(unittest.TestCase):
    def _protocol(self, protocol_id="external_demo", presets=None, executable=None, fields=None):
        return Protocol(
            protocol_id=protocol_id,
            display_name="External demo",
            description="Separate process test fixture",
            adapter_type="unix_json_v1",
            executable=tuple(executable or (sys.executable, "-m", "example_task")),
            presets=tuple(presets or (Preset("standard", "Standard", "", ("--profile", "fixed")),)),
            runtime_fields=tuple(fields or (RuntimeField("subject_id", "Subject / Mouse ID", False,
                                                         cli_flag="--subject-id"),)),
        )

    def test_builtin_registry_has_fake_protocol_and_safe_metadata(self):
        registry = default_registry()
        metadata = registry.public_metadata()
        self.assertEqual([item["protocol_id"] for item in metadata], ["fake"])
        self.assertEqual(metadata[0]["presets"][0]["preset_id"], "standard")
        self.assertEqual(metadata[0]["runtime_fields"][0]["name"], "subject_id")
        serialized = json.dumps(metadata)
        self.assertNotIn(sys.executable, serialized)
        self.assertNotIn("monitor.fake_task", serialized)

    def test_duplicate_protocol_and_preset_ids_are_rejected(self):
        protocol = self._protocol()
        with self.assertRaisesRegex(RegistryError, "duplicate protocol"):
            ProtocolRegistry((protocol, protocol))
        duplicate_presets = (Preset("standard", "One", "", ()), Preset("standard", "Two", "", ()))
        with self.assertRaisesRegex(RegistryError, "duplicate preset"):
            ProtocolRegistry((self._protocol(presets=duplicate_presets),))

    def test_invalid_ids_adapters_argv_and_runtime_fields_are_rejected(self):
        for value in ("../../bin/bash", "; rm -rf /", "", "UpperCase"):
            with self.subTest(value=value), self.assertRaises(RegistryError):
                ProtocolRegistry((self._protocol(protocol_id=value),))
        with self.assertRaisesRegex(RegistryError, "adapter"):
            ProtocolRegistry((Protocol("valid", "Valid", "", "shell", (sys.executable,),
                                       (Preset("standard", "Standard", "", ()),)),))
        with self.assertRaisesRegex(RegistryError, "argv strings"):
            ProtocolRegistry((self._protocol(executable=(sys.executable, None)),))
        with self.assertRaisesRegex(RegistryError, "argv strings"):
            ProtocolRegistry((self._protocol(presets=(Preset("standard", "Standard", "", ("bad\0arg",)),)),))
        with self.assertRaisesRegex(RegistryError, "runtime field"):
            ProtocolRegistry((self._protocol(fields=(RuntimeField("subject_id", "Subject", True,
                                                                  cli_flag="--x"),
                                                     RuntimeField("subject_id", "Again", False,
                                                                  cli_flag="--y"))),))

    def test_missing_executable_or_required_config_marks_optional_protocol_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "not-installed-task"
            protocol = self._protocol(executable=(str(missing),), presets=(
                Preset("standard", "Standard", "", (), (Path(directory) / "missing.toml",)),
            ))
            registry = ProtocolRegistry((protocol,))
            item = registry.public_metadata()[0]
            self.assertFalse(item["available"])
            self.assertEqual(item["unavailable_reason"], "Configured executable is not installed")
            self.assertFalse(item["presets"][0]["available"])
            with self.assertRaisesRegex(RegistryError, "not installed"):
                registry.resolve("external_demo", "standard", "session-1", "/tmp/control.sock", 0, {})
            config_registry = ProtocolRegistry((self._protocol(presets=(Preset(
                "standard", "Standard", "", (), config_path=Path(directory) / "missing.toml"),)),))
            self.assertFalse(config_registry.public_metadata()[0]["available"])

    def test_registry_file_builds_deterministic_structured_launch_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "external_task.py"
            config = root / "protocol.toml"
            script.write_text("# external task fixture\n")
            config.write_text("[task]\nmode = 'test'\n")
            registry_file = root / "registry.json"
            registry_file.write_text(json.dumps({
                "schema_version": 1,
                "protocols": [{
                    "protocol_id": "external_demo", "display_name": "External Demo",
                    "description": "Separate executable", "adapter_type": "unix_json_v1",
                    "executable": [sys.executable, str(script)], "working_directory": directory,
                    "runtime_fields": [{"name": "subject_id", "display_name": "Subject / Mouse ID",
                                        "required": True, "type": "subject_id", "cli_flag": "--subject-id"}],
                    "presets": [{"preset_id": "steady", "display_name": "Steady", "description": "",
                                 "arguments": ["--config", str(config)], "required_files": [str(config)],
                                 "config_path": str(config)}],
                }],
            }))
            registry = ProtocolRegistry.from_json_file(registry_file)
            spec = registry.resolve("external_demo", "steady", "session-1", "/tmp/control.sock", 5055,
                                    {"subject_id": "Fucci-Tri_7"})
            self.assertEqual(spec.protocol_id, "external_demo")
            self.assertEqual(spec.preset_id, "steady")
            self.assertEqual(spec.adapter_type, "unix_json_v1")
            self.assertEqual(spec.argv, (str(Path(sys.executable).resolve()), str(script), "--config", str(config),
                                         "--subject-id", "Fucci-Tri_7", "--control-socket", "/tmp/control.sock",
                                         "--session-id", "session-1", "--telemetry-port", "5055"))
            self.assertEqual(spec.cwd, directory)
            self.assertEqual(spec.config_path, str(config))
            self.assertEqual(dict(spec.env), {})
            self.assertNotIn(str(config), json.dumps(registry.public_metadata()))
            self.assertEqual(registry.public_metadata()[0]["protocol_id"], "external_demo")

    def test_durable_state_path_is_server_supplied_by_registered_cli_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "external_task.py"
            script.write_text("# external task fixture\n")
            registry_file = root / "registry.json"
            registry_file.write_text(json.dumps({
                "schema_version": 1,
                "protocols": [{
                    "protocol_id": "external_demo", "display_name": "External Demo",
                    "description": "Separate executable", "adapter_type": "unix_json_v1",
                    "executable": [sys.executable, str(script)],
                    "control_state_flag": "--control-state-file",
                    "control_state_artifact_type": "external_demo.operator_state",
                    "presets": [{"preset_id": "standard", "display_name": "Standard"}],
                }],
            }))
            registry = ProtocolRegistry.from_json_file(registry_file)
            state_path = str(root / "state" / "session-1.json")
            launch = registry.resolve("external_demo", "standard", "session-1", "/tmp/task.sock", 5055,
                                      {}, state_path)
            self.assertEqual(launch.control_state_path, state_path)
            self.assertEqual(launch.control_state_artifact_type, "external_demo.operator_state")
            self.assertEqual(launch.argv[-2:], ("--control-state-file", state_path))
            self.assertNotIn("control_state_flag", json.dumps(registry.public_metadata()))
            with self.assertRaisesRegex(RegistryError, "control-state path"):
                registry.resolve("external_demo", "standard", "session-1", "/tmp/task.sock", 5055, {})

    def test_durable_state_cli_mapping_must_be_safe_and_unique(self):
        with self.assertRaisesRegex(RegistryError, "durable control-state"):
            ProtocolRegistry((Protocol("external_demo", "External demo", "", "unix_json_v1",
                                       (sys.executable,), (Preset("standard", "Standard", "", ()),),
                                       control_state_flag="--control-socket",
                                       control_state_artifact_type="external_demo.operator_state"),))
        with self.assertRaisesRegex(RegistryError, "artifact type is required"):
            ProtocolRegistry((Protocol("external_demo", "External demo", "", "unix_json_v1",
                                       (sys.executable,), (Preset("standard", "Standard", "", ()),),
                                       control_state_flag="--control-state-file"),))
        with self.assertRaisesRegex(RegistryError, "duplicates a runtime field"):
            ProtocolRegistry((Protocol("external_demo", "External demo", "", "unix_json_v1",
                                       (sys.executable,), (Preset("standard", "Standard", "", ()),),
                                       runtime_fields=(RuntimeField("subject_id", "Subject", False,
                                                                    cli_flag="--control-state-file"),),
                                       control_state_flag="--control-state-file",
                                       control_state_artifact_type="external_demo.operator_state"),))

    def test_unknown_protocol_preset_and_runtime_fields_are_rejected(self):
        registry = default_registry()
        with self.assertRaisesRegex(RegistryError, "unsupported protocol"):
            registry.resolve("unknown", "standard", "session", "/tmp/control.sock", 0, {})
        with self.assertRaisesRegex(RegistryError, "unsupported preset"):
            registry.resolve("fake", "unknown", "session", "/tmp/control.sock", 0, {})
        with self.assertRaisesRegex(RegistryError, "unsupported runtime field"):
            registry.resolve("fake", "standard", "session", "/tmp/control.sock", 0,
                             {"subject_id": "mouse", "executable": "/bin/sh"})

    def test_subject_ids_accept_lab_names_and_reject_paths_controls_and_shell_text(self):
        for value in ("V1_2", "example_mouse", "Fucci-Tri_7"):
            self.assertEqual(validate_subject_id(value), value)
        for value in ("../mouse", "a/b", "a\\b", "mouse;touch", "mouse$(id)", "bad\nname",
                      "bad\x00name", "x" * 65, "-leading"):
            with self.subTest(value=value), self.assertRaises(RegistryError):
                validate_subject_id(value)

    def test_registry_file_rejects_bad_schema_and_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text('{"schema_version":2,"protocols":[]}')
            with self.assertRaisesRegex(RegistryError, "schema_version"):
                ProtocolRegistry.from_json_file(path)
            entry = {"protocol_id": "dup", "display_name": "Duplicate", "description": "",
                     "adapter_type": "unix_json_v1", "executable": [sys.executable],
                     "presets": [{"preset_id": "standard", "display_name": "Standard"}]}
            path.write_text(json.dumps({"schema_version": 1, "protocols": [entry, entry]}))
            with self.assertRaisesRegex(RegistryError, "duplicate protocol"):
                ProtocolRegistry.from_json_file(path)


if __name__ == "__main__":
    unittest.main()
