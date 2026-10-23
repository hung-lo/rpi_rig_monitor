"""Trusted protocol/preset registry and deterministic launch resolution."""

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

PROTOCOL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
FIELD_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SUBJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ARTIFACT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
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
class SessionParameter:
    """One typed, server-approved scientific setting."""

    name: str
    display_name: str
    kind: str
    default: Any
    description: str
    cli_flag: str
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    units: Optional[str] = None

    def public_metadata(self, default=None):
        value = self.default if default is None else default
        return {"name": self.name, "display_name": self.display_name,
                "type": self.kind, "default": value, "minimum": self.minimum,
                "maximum": self.maximum, "units": self.units,
                "description": self.description, "editable_before_prepare": True}


@dataclass(frozen=True)
class Preset:
    preset_id: str
    display_name: str
    description: str
    arguments: Tuple[str, ...]
    required_files: Tuple[Path, ...] = ()
    config_path: Optional[Path] = None
    parameter_defaults: Mapping[str, Any] = None


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
    control_state_flag: Optional[str] = None
    control_state_artifact_type: Optional[str] = None
    parameters: Tuple[SessionParameter, ...] = ()
    session_metadata_flag: Optional[str] = None


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
    config_path: Optional[str] = None
    control_state_path: Optional[str] = None
    control_state_artifact_type: Optional[str] = None
    parameters: Mapping[str, Any] = None
    parameter_overrides: Mapping[str, Any] = None
    profile_identity: Optional[Mapping[str, Any]] = None
    session_metadata_path: Optional[str] = None


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
                missing_file = (any(not item.is_file() for item in preset.required_files)
                                or (preset.config_path is not None and not preset.config_path.is_file()))
                reason = (executable_error or ("Configured working directory is missing" if cwd_missing else None)
                          or ("Required preset configuration is missing" if missing_file else None))
                preset_parameters = []
                for parameter in protocol.parameters:
                    default = (preset.parameter_defaults or {}).get(parameter.name, parameter.default)
                    preset_parameters.append(parameter.public_metadata(default))
                presets.append({"preset_id": preset.preset_id, "display_name": preset.display_name,
                                "description": preset.description, "available": reason is None,
                                "unavailable_reason": reason, "parameters": preset_parameters})
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

    def resolve(self, protocol_id, preset_id, session_id, control_socket, telemetry_port, fields,
                control_state_path=None, parameters=None, profile_identity=None,
                parameter_overrides=None, session_metadata_path=None):
        protocol = self.get(protocol_id)
        if protocol is None:
            raise RegistryError("unsupported protocol")
        if not isinstance(preset_id, str) or not PROTOCOL_ID_RE.fullmatch(preset_id):
            raise RegistryError("invalid preset ID")
        preset = next((item for item in protocol.presets if item.preset_id == preset_id), None)
        if preset is None:
            raise RegistryError("unsupported preset")
        resolved_parameters = self.validate_parameters(
            protocol_id, preset_id, {} if parameters is None else parameters
        )
        defaults = {item.name: (preset.parameter_defaults or {}).get(item.name, item.default)
                    for item in protocol.parameters}
        if parameter_overrides is None:
            parameter_overrides = {key: value for key, value in resolved_parameters.items()
                                   if value != defaults[key]}
        else:
            parameter_overrides = dict(parameter_overrides)
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
        for parameter in protocol.parameters:
            if parameter.name in resolved_parameters:
                argv.extend((parameter.cli_flag, str(resolved_parameters[parameter.name])))
        argv.extend(("--control-socket", str(control_socket), "--session-id", session_id,
                     "--telemetry-port", str(telemetry_port)))
        resolved_state_path = None
        if protocol.control_state_flag is not None:
            if not control_state_path:
                raise RegistryError("protocol requires a supervisor control-state path")
            argv.extend((protocol.control_state_flag, str(control_state_path)))
            resolved_state_path = str(control_state_path)
        resolved_metadata_path = None
        if protocol.session_metadata_flag is not None:
            if not session_metadata_path:
                raise RegistryError("protocol requires a supervisor session-metadata path")
            argv.extend((protocol.session_metadata_flag, str(session_metadata_path)))
            resolved_metadata_path = str(session_metadata_path)
        return LaunchSpec(
            protocol_id=protocol.protocol_id, preset_id=preset.preset_id,
            protocol_display_name=protocol.display_name, preset_display_name=preset.display_name,
            adapter_type=protocol.adapter_type,
            engine_protocol_id=protocol.engine_protocol_id or protocol.protocol_id,
            argv=tuple(argv),
            cwd=str(protocol.working_directory) if protocol.working_directory else None,
            env={}, runtime_fields=values,
            config_path=str(preset.config_path) if preset.config_path else None,
            control_state_path=resolved_state_path,
            control_state_artifact_type=(protocol.control_state_artifact_type
                                         if resolved_state_path else None),
            parameters=resolved_parameters,
            parameter_overrides=parameter_overrides,
            profile_identity=profile_identity,
            session_metadata_path=resolved_metadata_path,
        )

    def validate_parameters(self, protocol_id, preset_id, parameters):
        protocol = self.get(protocol_id)
        if protocol is None:
            raise RegistryError("unsupported protocol")
        preset = next((item for item in protocol.presets if item.preset_id == preset_id), None)
        if preset is None:
            raise RegistryError("unsupported preset")
        if not isinstance(parameters, dict):
            raise RegistryError("session parameters must be an object")
        definitions = {item.name: item for item in protocol.parameters}
        if set(parameters) - set(definitions):
            raise RegistryError("unsupported session parameter")
        resolved = {}
        for name, definition in definitions.items():
            value = parameters.get(name, (preset.parameter_defaults or {}).get(name, definition.default))
            if definition.kind == "integer":
                if type(value) is not int:
                    raise RegistryError(name + " must be an integer")
                if ((definition.minimum is not None and value < definition.minimum) or
                        (definition.maximum is not None and value > definition.maximum)):
                    raise RegistryError(name + " is outside the supported range")
            else:
                raise RegistryError("unsupported session parameter type")
            resolved[name] = value
        return resolved


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
    if (protocol.control_state_flag is not None and (
            protocol.adapter_type != "unix_json_v1"
            or not isinstance(protocol.control_state_flag, str)
            or not re.fullmatch(r"--[a-z][a-z0-9-]*", protocol.control_state_flag)
            or protocol.control_state_flag in {"--control-socket", "--session-id", "--telemetry-port"})):
        raise RegistryError("invalid durable control-state CLI mapping")
    if protocol.control_state_flag is not None:
        if (not isinstance(protocol.control_state_artifact_type, str)
                or not ARTIFACT_TYPE_RE.fullmatch(protocol.control_state_artifact_type)):
            raise RegistryError("durable control-state artifact type is required")
    elif protocol.control_state_artifact_type is not None:
        raise RegistryError("control-state artifact type requires a control-state CLI mapping")
    if (protocol.session_metadata_flag is not None and
            (protocol.adapter_type != "unix_json_v1"
             or not isinstance(protocol.session_metadata_flag, str)
             or not re.fullmatch(r"--[a-z][a-z0-9-]*", protocol.session_metadata_flag)
             or protocol.session_metadata_flag in {"--control-socket", "--session-id", "--telemetry-port"})):
        raise RegistryError("invalid supervisor session-metadata CLI mapping")
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
        if preset.config_path is not None and not isinstance(preset.config_path, Path):
            raise RegistryError("preset config_path must be a path")
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
    if protocol.control_state_flag in field_flags:
        raise RegistryError("durable control-state CLI mapping duplicates a runtime field")
    if protocol.session_metadata_flag in field_flags:
        raise RegistryError("session-metadata CLI mapping duplicates a runtime field")
    parameter_names = set()
    parameter_flags = set(field_flags)
    if protocol.control_state_flag:
        parameter_flags.add(protocol.control_state_flag)
    if protocol.session_metadata_flag:
        if protocol.session_metadata_flag in parameter_flags:
            raise RegistryError("session-metadata CLI mapping is duplicated")
        parameter_flags.add(protocol.session_metadata_flag)
    for parameter in protocol.parameters:
        if not isinstance(parameter, SessionParameter):
            raise RegistryError("parameters must contain session parameter definitions")
        if (not isinstance(parameter.name, str) or not FIELD_ID_RE.fullmatch(parameter.name)
                or parameter.name in parameter_names):
            raise RegistryError("invalid or duplicate session parameter")
        parameter_names.add(parameter.name)
        if (not isinstance(parameter.display_name, str) or not parameter.display_name
                or not isinstance(parameter.description, str) or not parameter.description
                or parameter.kind != "integer"):
            raise RegistryError("invalid session parameter definition")
        if (not isinstance(parameter.cli_flag, str)
                or not re.fullmatch(r"--[a-z][a-z0-9-]*", parameter.cli_flag)
                or parameter.cli_flag in parameter_flags):
            raise RegistryError("invalid or duplicate session parameter CLI mapping")
        parameter_flags.add(parameter.cli_flag)
        if (parameter.minimum is None or type(parameter.minimum) is not int
                or parameter.maximum is None or type(parameter.maximum) is not int
                or parameter.minimum > parameter.maximum or type(parameter.default) is not int
                or not parameter.minimum <= parameter.default <= parameter.maximum):
            raise RegistryError("invalid session parameter bounds or default")
        if parameter.units is not None and not isinstance(parameter.units, str):
            raise RegistryError("session parameter units must be text")
    for preset in protocol.presets:
        defaults = preset.parameter_defaults or {}
        if not isinstance(defaults, dict) or set(defaults) - parameter_names:
            raise RegistryError("preset has unsupported parameter defaults")
        for parameter in protocol.parameters:
            value = defaults.get(parameter.name, parameter.default)
            if (type(value) is not int or value < parameter.minimum or value > parameter.maximum):
                raise RegistryError("preset parameter default is invalid: " + parameter.name)


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
    allowed = required | {"working_directory", "runtime_fields", "engine_protocol_id", "control_state_flag",
                          "control_state_artifact_type", "parameters", "session_metadata_flag"}
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
                preset_keys | {"description", "arguments", "required_files", "config_path",
                               "parameter_defaults"}):
            raise RegistryError("invalid preset entry in registry file")
        arguments = item.get("arguments", [])
        required_files = item.get("required_files", [])
        parameter_defaults = item.get("parameter_defaults", {})
        if (not isinstance(arguments, list) or not isinstance(required_files, list)
                or not isinstance(parameter_defaults, dict)):
            raise RegistryError("preset arguments and required_files must be lists")
        presets.append(Preset(item["preset_id"], item["display_name"], item.get("description", ""),
                              tuple(arguments), tuple(_config_path(value, base_dir) for value in required_files),
                              _config_path(item["config_path"], base_dir) if "config_path" in item else None,
                              parameter_defaults))
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
    parameters = []
    raw_parameters = raw.get("parameters", [])
    if not isinstance(raw_parameters, list):
        raise RegistryError("parameters must be a list")
    allowed_parameter_keys = {"name", "display_name", "type", "default", "description",
                              "cli_flag", "minimum", "maximum", "units"}
    for item in raw_parameters:
        required_parameter_keys = {"name", "display_name", "type", "default", "description",
                                   "cli_flag", "minimum", "maximum"}
        if (not isinstance(item, dict) or set(item) - allowed_parameter_keys
                or not required_parameter_keys.issubset(item)):
            raise RegistryError("invalid session parameter entry")
        parameters.append(SessionParameter(
            item["name"], item["display_name"], item["type"], item["default"],
            item["description"], item["cli_flag"], item["minimum"], item["maximum"],
            item.get("units"),
        ))
    return Protocol(raw["protocol_id"], raw["display_name"], raw["description"], raw["adapter_type"],
                    tuple(executable), tuple(presets), tuple(fields), working_directory,
                    raw.get("engine_protocol_id"), raw.get("control_state_flag"),
                    raw.get("control_state_artifact_type"), tuple(parameters),
                    raw.get("session_metadata_flag"))


def _config_path(value, base_dir):
    if not isinstance(value, str) or not value:
        raise RegistryError("configured paths must be non-empty strings")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()
