"""Supervisor-owned, versioned session parameter profiles."""

import datetime
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

from .registry import PROTOCOL_ID_RE, RegistryError

PROFILE_ID_RE = re.compile(r"^profile-[0-9a-f]{32}$")
PROFILE_NAME_MAX = 64


class ProfileError(ValueError):
    """Raised for invalid, stale, or unavailable profile operations."""


class ProfileStore:
    """Persist reusable templates outside Flask, under the supervisor state dir."""

    def __init__(self, root, registry):
        self.root = Path(root).expanduser().resolve()
        self.registry = registry
        self.lock_path = self.root / ".profiles.lock"

    def _locked(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(str(self.root), 0o700)
        handle = self.lock_path.open("a+")
        os.chmod(str(self.lock_path), 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    def _scope(self, protocol_id, preset_id):
        if not isinstance(protocol_id, str) or not PROTOCOL_ID_RE.fullmatch(protocol_id):
            raise ProfileError("invalid protocol ID")
        if not isinstance(preset_id, str) or not PROTOCOL_ID_RE.fullmatch(preset_id):
            raise ProfileError("invalid preset ID")
        protocol = self.registry.get(protocol_id)
        if protocol is None or not any(item.preset_id == preset_id for item in protocol.presets):
            raise ProfileError("unsupported protocol or preset")
        return self.root / protocol_id / preset_id

    @staticmethod
    def _validate_name(name):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > PROFILE_NAME_MAX:
            raise ProfileError("profile name must contain 1-64 characters")
        if any(ord(char) < 32 or ord(char) == 127 for char in name):
            raise ProfileError("profile name contains a control character")
        if "/" in name or "\\" in name:
            raise ProfileError("profile name cannot contain path separators")
        return name.strip()

    def _read(self, path):
        try:
            info = path.lstat()
            if (not path.is_file() or path.is_symlink() or info.st_uid != os.geteuid()
                    or (info.st_mode & 0o777) != 0o600 or info.st_size > 65536):
                raise ProfileError("profile file permissions or size are invalid")
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ProfileError("profile not found")
        except (OSError, ValueError) as exc:
            raise ProfileError("profile is unreadable or corrupted: " + str(exc))
        if not isinstance(value, dict):
            raise ProfileError("profile document is not an object")
        return value

    @staticmethod
    def _utc_now():
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _validate_values(self, protocol_id, preset_id, parameters):
        try:
            return self.registry.validate_parameters(protocol_id, preset_id, parameters)
        except RegistryError as exc:
            raise ProfileError(str(exc))

    def _write(self, path, document):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(str(path.parent), 0o700)
        fd, name = tempfile.mkstemp(prefix=".profile-", suffix=".json", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(document, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            os.replace(name, str(path))
            directory = os.open(str(path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                os.unlink(name)
            except OSError:
                pass

    @staticmethod
    def _schema_valid(document):
        return set(document) == {
            "schema_version", "profile_id", "protocol_id", "preset_id", "name",
            "version", "parameters", "created_at", "updated_at",
        }

    @staticmethod
    def _profile_id(profile_id):
        if not isinstance(profile_id, str) or not PROFILE_ID_RE.fullmatch(profile_id):
            raise ProfileError("invalid profile ID")
        return profile_id

    def list_profiles(self, protocol_id, preset_id):
        scope = self._scope(protocol_id, preset_id)
        lock = self._locked()
        try:
            if not scope.is_dir():
                return []
            result = []
            for path in sorted(scope.glob("profile-*.json")):
                profile_id = path.stem
                if not PROFILE_ID_RE.fullmatch(profile_id):
                    continue
                try:
                    document = self._read(path)
                    valid_identity = (
                        self._schema_valid(document)
                        and
                        type(document.get("schema_version")) is int and document["schema_version"] == 1
                        and document.get("profile_id") == profile_id
                        and document.get("protocol_id") == protocol_id
                        and document.get("preset_id") == preset_id
                        and type(document.get("version")) is int and document["version"] >= 1
                        and isinstance(document.get("name"), str)
                        and isinstance(document.get("parameters"), dict)
                    )
                    if not valid_identity:
                        raise ProfileError("profile schema or identity is invalid")
                    parameters = self._validate_values(protocol_id, preset_id, document["parameters"])
                    result.append({"profile_id": profile_id, "name": document["name"],
                                   "version": document["version"], "parameters": parameters,
                                   "valid": True, "error": None,
                                   "created_at": document.get("created_at"),
                                   "updated_at": document.get("updated_at")})
                except ProfileError as exc:
                    try:
                        document = self._read(path)
                    except ProfileError:
                        document = {}
                    supported = {item.name for item in self.registry.get(protocol_id).parameters}
                    raw_parameters = document.get("parameters", {})
                    review_values = ({key: value for key, value in raw_parameters.items()
                                      if key in supported} if isinstance(raw_parameters, dict) else {})
                    result.append({"profile_id": profile_id,
                                   "name": document.get("name", "Invalid profile"),
                                   "version": document.get("version"), "parameters": review_values,
                                   "valid": False, "error": str(exc),
                                   "created_at": document.get("created_at"),
                                   "updated_at": document.get("updated_at")})
            return result
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def get_profile(self, protocol_id, preset_id, profile_id, expected_version=None):
        scope = self._scope(protocol_id, preset_id)
        profile_id = self._profile_id(profile_id)
        lock = self._locked()
        try:
            document = self._read(scope / (profile_id + ".json"))
            if (not self._schema_valid(document)
                    or type(document.get("schema_version")) is not int or document["schema_version"] != 1
                    or document.get("profile_id") != profile_id
                    or document.get("protocol_id") != protocol_id
                    or document.get("preset_id") != preset_id
                    or type(document.get("version")) is not int):
                raise ProfileError("profile schema or identity is invalid")
            if expected_version is not None and document["version"] != expected_version:
                raise ProfileError("profile was changed by another operator; reload it")
            parameters = self._validate_values(protocol_id, preset_id, document.get("parameters"))
            return {"profile_id": profile_id, "name": document["name"],
                    "version": document["version"], "parameters": parameters,
                    "created_at": document["created_at"], "updated_at": document["updated_at"]}
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def save_profile(self, protocol_id, preset_id, name, parameters):
        scope = self._scope(protocol_id, preset_id)
        name = self._validate_name(name)
        parameters = self._validate_values(protocol_id, preset_id, parameters)
        lock = self._locked()
        try:
            scope.mkdir(mode=0o700, parents=True, exist_ok=True)
            for path in scope.glob("profile-*.json"):
                try:
                    document = self._read(path)
                except ProfileError:
                    continue
                if (document.get("schema_version") == 1 and
                        isinstance(document.get("name"), str) and
                        document["name"].casefold() == name.casefold()):
                    raise ProfileError("a profile with that name already exists")
            profile_id = "profile-" + os.urandom(16).hex()
            now = self._utc_now()
            document = {"schema_version": 1, "profile_id": profile_id,
                        "protocol_id": protocol_id, "preset_id": preset_id,
                        "name": name, "version": 1, "parameters": parameters,
                        "created_at": now, "updated_at": now}
            self._write(scope / (profile_id + ".json"), document)
            return {"profile_id": profile_id, "name": name, "version": 1,
                    "parameters": parameters, "created_at": now, "updated_at": now}
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def update_profile(self, protocol_id, preset_id, profile_id, expected_version, name, parameters):
        scope = self._scope(protocol_id, preset_id)
        profile_id = self._profile_id(profile_id)
        if type(expected_version) is not int or expected_version < 1:
            raise ProfileError("expected profile version is required")
        name = self._validate_name(name)
        parameters = self._validate_values(protocol_id, preset_id, parameters)
        lock = self._locked()
        try:
            path = scope / (profile_id + ".json")
            document = self._read(path)
            if (not self._schema_valid(document)
                    or type(document.get("schema_version")) is not int or document["schema_version"] != 1
                    or document.get("profile_id") != profile_id
                    or document.get("protocol_id") != protocol_id
                    or document.get("preset_id") != preset_id):
                raise ProfileError("profile schema or identity is invalid; save corrected values as a new profile")
            if document.get("version") != expected_version:
                raise ProfileError("profile was changed by another operator; reload it")
            for other in scope.glob("profile-*.json"):
                if other == path:
                    continue
                try:
                    other_doc = self._read(other)
                except ProfileError:
                    continue
                if (other_doc.get("schema_version") == 1 and
                        isinstance(other_doc.get("name"), str) and
                        other_doc["name"].casefold() == name.casefold()):
                    raise ProfileError("a profile with that name already exists")
            document.update({"name": name, "version": expected_version + 1,
                             "parameters": parameters, "updated_at": self._utc_now()})
            self._write(path, document)
            return {"profile_id": profile_id, "name": name,
                    "version": document["version"], "parameters": parameters,
                    "created_at": document["created_at"], "updated_at": document["updated_at"]}
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def delete_profile(self, protocol_id, preset_id, profile_id, expected_version):
        scope = self._scope(protocol_id, preset_id)
        profile_id = self._profile_id(profile_id)
        if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
            raise ProfileError("expected profile version is invalid")
        lock = self._locked()
        try:
            path = scope / (profile_id + ".json")
            try:
                info = path.lstat()
            except OSError:
                raise ProfileError("profile not found")
            if (not path.is_file() or path.is_symlink() or info.st_uid != os.geteuid()
                    or (info.st_mode & 0o777) != 0o600 or info.st_size > 65536):
                raise ProfileError("profile file permissions or size are invalid")
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                if expected_version is not None:
                    raise ProfileError("corrupted profile can only be deleted without a version")
                path.unlink()
                return {"deleted": True, "profile_id": profile_id}
            if not isinstance(document, dict):
                raise ProfileError("profile identity is invalid")
            if (document.get("profile_id") != profile_id
                    or document.get("protocol_id") != protocol_id
                    or document.get("preset_id") != preset_id):
                raise ProfileError("profile identity is invalid")
            if expected_version is None or document.get("version") != expected_version:
                raise ProfileError("profile was changed by another operator; reload it")
            path.unlink()
            return {"deleted": True, "profile_id": profile_id}
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
