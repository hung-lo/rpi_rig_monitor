"""Trusted protocol/preset registry and deterministic launch resolution."""

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

PROTOCOL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
FIELD_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SUBJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
CONTROL_ADAPTERS = {"unix_json_v1"}


class RegistryError(ValueError):
    """Raised for an invalid registry or launch selection."""


@dataclass(frozen=True)
class RuntimeField:
    name: str
    display_name: str
    required: bool
    kind: str = "subject_id"
    cli_flag: Optional[str] = None

    def public_metadata(self):
        return {"name": self.name, "display_name": self.display_name,
                "type": self.kind, "required": self.required}


@dataclass(frozen=True)
class Preset:
    preset_id: str
    display_name: str
    description: str
    arguments: Tuple[str, ...]
    required_files: Tuple[Path, ...] = ()


@dataclass(frozen=True)
class Protocol:
    protocol_id: str
    display_name: str
    description: str
    adapter_type: str
    executable: Tuple[str, ...]
    presets: Tuple[Preset, ...]
    runtime_fields: Tuple[RuntimeField, ...] = ()
    working_directory: Optional[Path] = None
    engine_protocol_id: Optional[str] = None


@dataclass(frozen=True)
class LaunchSpec:
    protocol_id: str
    preset_id: str
    protocol_display_name: str
    preset_display_name: str
    adapter_type: str
    engine_protocol_id: str
    argv: Tuple[str, ...]
    cwd: Optional[str]
    env: Mapping[str, str]
    runtime_fields: Mapping[str, str]


def validate_subject_id(value):
    if not isinstance(value, str) or not SUBJECT_ID_RE.fullmatch(value):
        raise RegistryError("subject_id must contain 1-64 letters, digits, underscores, or hyphens and start with a letter or digit")
    return value


class ProtocolRegistry:
    """Registry definitions are trusted server configuration, never browser input."""

    def __init__(self, protocols):
        self._protocols = {}
        for protocol in protocols:
            _validate_protocol(protocol)
            if protocol.protocol_id in self._protocols:
                raise RegistryError("duplicate protocol ID: " + protocol.protocol_id)
            self._protocols[protocol.protocol_id] = protocol

    @classmethod
    def from_json_file(cls, path, base_protocols=()):
        path = Path(path).expanduser().resolve()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RegistryError("unable to read protocol registry: " + str(exc))
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "protocols"}:
            raise RegistryError("registry file must contain schema_version and protocols")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or not isinstance(raw["protocols"], list):
            raise RegistryError("registry schema_version must be 1 and protocols must be a list")
        return cls(tuple(base_protocols) + tuple(_protocol_from_json(item, path.parent) for item in raw["protocols"]))

    def get(self, protocol_id):
        return self._protocols.get(protocol_id)

    def public_metadata(self):
        result = []
        for protocol in sorted(self._protocols.values(), key=lambda item: item.protocol_id):
            _executable, executable_error = _resolve_executable(protocol.executable)
            cwd_missing = protocol.working_directory is not None and not protocol.working_directory.is_dir()
            presets = []
            for preset in protocol.presets:
                missing_file = any(not item.is_file() for item in preset.required_files)
                reason = (executable_error or ("Configured working directory is missing" if cwd_missing else None)
                          or ("Required preset configuration is missing" if missing_file else None))
                presets.append({"preset_id": preset.preset_id, "display_name": preset.display_name,
                                "description": preset.description, "available": reason is None,
                                "unavailable_reason": reason})
            available = any(item["available"] for item in presets)
            reason = None if available else next(
                (item["unavailable_reason"] for item in presets if item["unavailable_reason"]),
                "No available presets",
            )
            result.append({"protocol_id": protocol.protocol_id, "display_name": protocol.display_name,
                           "description": protocol.description, "adapter_type": protocol.adapter_type,
                           "available": available, "unavailable_reason": reason,
                           "runtime_fields": [field.public_metadata() for field in protocol.runtime_fields],
                           "presets": presets})
        return result

    def resolve(self, protocol_id, preset_id, session_id, control_socket, telemetry_port, fields):
        protocol = self.get(protocol_id)
        if protocol is None:
            raise RegistryError("unsupported protocol")
        if not isinstance(preset_id, str) or not PROTOCOL_ID_RE.fullmatch(preset_id):
            raise RegistryError("invalid preset ID")
        preset = next((item for item in protocol.presets if item.preset_id == preset_id), None)
        if preset is None:
            raise RegistryError("unsupported preset")
        if not isinstance(fields, dict):
            raise RegistryError("runtime fields must be an object")
        definitions = {field.name: field for field in protocol.runtime_fields}
        if set(fields) - set(definitions):
            raise RegistryError("unsupported runtime field")
        values = {}
        for field in protocol.runtime_fields:
            if field.name not in fields:
                if field.required:
                    raise RegistryError(field.name + " is required")
                continue
            value = fields[field.name]
            if field.kind == "subject_id":
                value = validate_subject_id(value)
            else:  # All registry field types must have an explicit validator.
                raise RegistryError("unsupported runtime field type")
            values[field.name] = value

        metadata = next(item for item in self.public_metadata() if item["protocol_id"] == protocol_id)
        preset_metadata = next(item for item in metadata["presets"] if item["preset_id"] == preset_id)
        if not preset_metadata["available"]:
            raise RegistryError(preset_metadata["unavailable_reason"] or "protocol is unavailable")
        executable, reason = _resolve_executable(protocol.executable)
        if reason:
            raise RegistryError(reason)
        argv = list(executable) + list(preset.arguments)
        for field in protocol.runtime_fields:
            if field.name in values and field.cli_flag:
                argv.extend((field.cli_flag, values[field.name]))
        argv.extend(("--control-socket", str(control_socket), "--session-id", session_id,
                     "--telemetry-port", str(telemetry_port)))
        return LaunchSpec(
            protocol_id=protocol.protocol_id, preset_id=preset.preset_id,
            protocol_display_name=protocol.display_name, preset_display_name=preset.display_name,
            adapter_type=protocol.adapter_type,
            engine_protocol_id=protocol.engine_protocol_id or protocol.protocol_id,
            argv=tuple(argv),
            cwd=str(protocol.working_directory) if protocol.working_directory else None,
            env={}, runtime_fields=values,
        )


def default_registry(registry_file=None):
    fake = Protocol(
        protocol_id="fake", display_name="Fake task (hardware-free)",
        description="Detached local task for hardware-free operator and recovery checks.",
        adapter_type="unix_json_v1", executable=(sys.executable, "-m", "monitor.fake_task"),
        presets=(Preset("standard", "Standard", "30 second fake task", ("--duration", "30")),),
        runtime_fields=(RuntimeField("subject_id", "Subject / Mouse ID", False, cli_flag="--subject-id"),),
    )
    registry = ProtocolRegistry((fake,))
    registry_file = registry_file or os.environ.get("RIG_MONITOR_PROTOCOL_REGISTRY")
    if registry_file:
        return ProtocolRegistry.from_json_file(registry_file, base_protocols=(fake,))
    return registry


def _validate_protocol(protocol):
    if not isinstance(protocol, Protocol):
        raise RegistryError("protocol entries must be Protocol definitions")
    if not isinstance(protocol.protocol_id, str) or not PROTOCOL_ID_RE.fullmatch(protocol.protocol_id):
        raise RegistryError("invalid protocol ID")
    if not protocol.display_name or not isinstance(protocol.display_name, str):
        raise RegistryError("protocol display name is required")
    if not isinstance(protocol.description, str):
        raise RegistryError("protocol description must be text")
    if not isinstance(protocol.adapter_type, str) or protocol.adapter_type not in CONTROL_ADAPTERS:
        raise RegistryError("invalid control adapter: " + str(protocol.adapter_type))
    if (protocol.engine_protocol_id is not None and
            (not isinstance(protocol.engine_protocol_id, str)
             or not PROTOCOL_ID_RE.fullmatch(protocol.engine_protocol_id))):
        raise RegistryError("invalid engine protocol ID")
    if not protocol.executable or any(not isinstance(item, str) or not item or "\0" in item
                                      for item in protocol.executable):
        raise RegistryError("executable must be a non-empty list of argv strings")
    if not protocol.presets:
        raise RegistryError("protocol must define at least one preset")
    preset_ids = set()
    for preset in protocol.presets:
        if not isinstance(preset, Preset):
            raise RegistryError("presets must contain preset definitions")
        if not isinstance(preset.preset_id, str) or not PROTOCOL_ID_RE.fullmatch(preset.preset_id):
            raise RegistryError("invalid preset ID")
        if preset.preset_id in preset_ids:
            raise RegistryError("duplicate preset ID: " + preset.preset_id)
        preset_ids.add(preset.preset_id)
        if (not isinstance(preset.display_name, str) or not preset.display_name
                or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in preset.arguments)):
            raise RegistryError("preset display name and argv strings are required")
        if not isinstance(preset.description, str):
            raise RegistryError("preset description must be text")
        if any(not isinstance(path, Path) for path in preset.required_files):
            raise RegistryError("preset required_files must be paths")
    field_names = set()
    field_flags = set()
    for field in protocol.runtime_fields:
        if not isinstance(field, RuntimeField):
            raise RegistryError("runtime_fields must contain field definitions")
        if (not isinstance(field.name, str) or not FIELD_ID_RE.fullmatch(field.name)
                or field.name in field_names):
            raise RegistryError("invalid or duplicate runtime field")
        field_names.add(field.name)
        if (not isinstance(field.display_name, str) or not field.display_name
                or not isinstance(field.kind, str) or field.kind not in {"subject_id"}
                or type(field.required) is not bool):
            raise RegistryError("invalid runtime field definition")
        if field.cli_flag is not None:
            if (not isinstance(field.cli_flag, str)
                    or not re.fullmatch(r"--[a-z][a-z0-9-]*", field.cli_flag)
                    or field.cli_flag in field_flags):
                raise RegistryError("invalid or duplicate runtime field CLI mapping")
            field_flags.add(field.cli_flag)
    if protocol.working_directory is not None and not isinstance(protocol.working_directory, Path):
        raise RegistryError("working_directory must be a Path")


def _resolve_executable(executable):
    command = executable[0]
    if os.path.sep in command:
        path = Path(command)
        if path.is_file() and os.access(str(path), os.X_OK):
            return (str(path.resolve()),) + tuple(executable[1:]), None
    else:
        found = shutil.which(command)
        if found:
            return (str(Path(found).resolve()),) + tuple(executable[1:]), None
    return None, "Configured executable is not installed"


def _protocol_from_json(raw, base_dir):
    required = {"protocol_id", "display_name", "description", "adapter_type", "executable", "presets"}
    allowed = required | {"working_directory", "runtime_fields", "engine_protocol_id"}
    if not isinstance(raw, dict) or not required.issubset(raw) or set(raw) - allowed:
        raise RegistryError("invalid protocol entry in registry file")
    executable = raw["executable"]
    if not isinstance(executable, list):
        raise RegistryError("executable must be a list of argv strings")
    if executable and isinstance(executable[0], str) and os.path.sep in executable[0]:
        command_path = Path(executable[0]).expanduser()
        if not command_path.is_absolute():
            command_path = base_dir / command_path
        executable = [str(command_path.resolve())] + executable[1:]
    working_directory = raw.get("working_directory")
    if working_directory is not None:
        working_directory = _config_path(working_directory, base_dir)
    presets = []
    if not isinstance(raw["presets"], list):
        raise RegistryError("presets must be a list")
    for item in raw["presets"]:
        preset_keys = {"preset_id", "display_name"}
        if not isinstance(item, dict) or not preset_keys.issubset(item) or set(item) - (
                preset_keys | {"description", "arguments", "required_files"}):
            raise RegistryError("invalid preset entry in registry file")
        arguments = item.get("arguments", [])
        required_files = item.get("required_files", [])
        if not isinstance(arguments, list) or not isinstance(required_files, list):
            raise RegistryError("preset arguments and required_files must be lists")
        presets.append(Preset(item["preset_id"], item["display_name"], item.get("description", ""),
                              tuple(arguments), tuple(_config_path(value, base_dir) for value in required_files)))
    fields = []
    raw_fields = raw.get("runtime_fields", [])
    if not isinstance(raw_fields, list):
        raise RegistryError("runtime_fields must be a list")
    for item in raw_fields:
        if not isinstance(item, dict) or set(item) - {"name", "display_name", "required", "type", "cli_flag"}:
            raise RegistryError("invalid runtime field entry")
        if not {"name", "display_name", "required"}.issubset(item) or type(item["required"]) is not bool:
            raise RegistryError("runtime field requires name, display_name, and boolean required")
        fields.append(RuntimeField(item["name"], item["display_name"], item["required"],
                                   item.get("type", "subject_id"), item.get("cli_flag")))
    return Protocol(raw["protocol_id"], raw["display_name"], raw["description"], raw["adapter_type"],
                    tuple(executable), tuple(presets), tuple(fields), working_directory,
                    raw.get("engine_protocol_id"))


def _config_path(value, base_dir):
    if not isinstance(value, str) or not value:
        raise RegistryError("configured paths must be non-empty strings")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()
