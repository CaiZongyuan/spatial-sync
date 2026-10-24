"""Credential-free Harness packages and immutable, monotonically allocated versions."""

import difflib
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path

import yaml
from pydantic import TypeAdapter

from packages.library.archive import destination_parent, extract_package, import_lock, package_files
from packages.library.common import NAME, LibraryError, content_hash, document
from packages.library.transactions import durable_json
from packages.models import ModelConfig, Thinking

MAX_HARNESS_BYTES = 2 * 1024 * 1024
REQUIRED = {"AGENTS.md", "skills.lock", "mcp.yaml"}
THINKING = TypeAdapter(Thinking)
ENV_REF = re.compile(r"\$\{[A-Z][A-Z0-9_]*\}")
SECRET = re.compile(
    r"(?i)(?:-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\b(?:sk|ghp|github_pat|xox[baprs])-[_a-z0-9-]{12,}"
    r"|\bBearer\s+(?!\$\{)[a-z0-9._~+/-]{8,}"
    r"|https?://[^\s/@:]+:[^\s/@]+@"
    r"|\b(?:[a-z0-9_]*(?:api[_-]?key|token|password|secret|credential)"
    r"[a-z0-9_]*|AWS_ACCESS_KEY_ID|AWS_SESSION_TOKEN|ANTHROPIC_AUTH_TOKEN)[\"']?\s*[:=]\s*"
    r"[\"']?(?!\$\{|env:|null\b|\"?\s*$)[^\s\"'#,}]{3,})"
)


def invalid_agent(code="invalid_harness", status=422):
    # Never include parser diagnostics, paths or submitted content in public errors.
    return LibraryError(code, "Invalid or unavailable credential-free Agent Harness", status)


def agent_name(identifier):
    value = identifier.removeprefix("agent/")
    if not NAME.fullmatch(value):
        raise invalid_agent("unknown_agent", 404)
    return value


def confined(root, parts, *, create=False):
    if root.is_symlink() or root.is_junction():
        raise invalid_agent()
    return destination_parent(root, parts, create=create)


def harness_files(path):
    """Allow portable UTF-8 authoring inputs; configuration uses environment references only."""
    try:
        if path.is_symlink() or path.is_junction():
            raise ValueError
        files = package_files(path)
        if not REQUIRED.issubset(files) or len(files) > 128:
            raise ValueError
        size = 0
        for name in files:
            if SECRET.search(name):
                raise ValueError
            if name not in REQUIRED and not (
                name.startswith("hooks/") and not any(part.startswith(".") for part in name.split("/"))
            ):
                raise ValueError
            file = path / name
            size += file.stat().st_size
            if file.stat().st_nlink != 1 or size > MAX_HARNESS_BYTES:
                raise ValueError
            text = file.read_text(encoding="utf-8")
            if "\0" in text or SECRET.search(text):
                raise ValueError
        skills = yaml.safe_load((path / "skills.lock").read_text(encoding="utf-8"))
        if (not isinstance(skills, dict) or set(skills) != {"skills"}
            or not isinstance(skills["skills"], list)):
            raise ValueError
        for skill in skills["skills"]:
            if set(skill) != {"name", "version"} or not NAME.fullmatch(skill["name"]) or not isinstance(
                skill["version"], str
            ) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", skill["version"]):
                raise ValueError
        mcp = document(path / "mcp.yaml")
        if set(mcp) != {"servers"} or not isinstance(mcp["servers"], dict):
            raise ValueError
        for name, server in mcp["servers"].items():
            if (not NAME.fullmatch(name) or not isinstance(server, dict)
                or set(server) - {"command", "args", "env"}):
                raise ValueError
            if not isinstance(server.get("command"), str) or not server["command"]:
                raise ValueError
            if not isinstance(server.get("args", []), list) or any(
                not isinstance(arg, str) for arg in server.get("args", [])
            ):
                raise ValueError
            arguments = server.get("args", [])
            for index, argument in enumerate(arguments):
                if re.fullmatch(r"--?(?:api[-_]?key|token|password|secret|credential)", argument, re.I):
                    if index + 1 == len(arguments) or not ENV_REF.fullmatch(arguments[index + 1]):
                        raise ValueError
            if not isinstance(server.get("env", {}), dict) or any(
                not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or not isinstance(value, str)
                or not ENV_REF.fullmatch(value) for key, value in server.get("env", {}).items()
            ):
                raise ValueError
        return files
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
        raise invalid_agent() from None


def pack_harness(path):
    files = harness_files(path)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in files:
            data = (path / name).read_bytes()
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(data), 0o644, 0
            archive.addfile(entry, io.BytesIO(data))
    data = stream.getvalue()
    # Validate the exact frozen bytes, including a concurrent edit of the workspace.
    with tempfile.TemporaryDirectory() as temporary:
        unpack_harness(data, Path(temporary))
    return data


def unpack_harness(data, path):
    try:
        if len(data) > MAX_HARNESS_BYTES + 128 * 1024:
            raise ValueError
        extract_package(io.BytesIO(data), path)
        return harness_files(path)
    except (LibraryError, ValueError, OSError):
        raise invalid_agent() from None


class AgentLibrary:
    def __init__(self, roots):
        self.roots = roots
        self.user_root = roots["user"]

    def _path(self, identifier):
        name = agent_name(identifier)
        for root in reversed(list(self.roots.values())):
            path = confined(root, ("agents", name))
            if (path / "agent.yaml").is_file():
                return path
        raise invalid_agent("unknown_agent", 404)

    def _metadata(self, path):
        try:
            file = path / "agent.yaml"
            if file.is_symlink() or file.is_junction() or file.stat().st_nlink != 1:
                raise ValueError
            data = document(file)
            if set(data) != {"id", "title", "model", "thinking", "referenceVersion", "currentVersion"}:
                raise ValueError
            if data["id"] != "agent/" + path.name or not isinstance(data["title"], str) or not data["title"]:
                raise ValueError
            # Model configuration arrives separately from the model catalog; no invented default.
            for key in ("model", "thinking"):
                if data[key] is not None and (
                    not isinstance(data[key], str) or SECRET.search(data[key])
                ):
                    raise ValueError
            if data["thinking"] is not None:
                THINKING.validate_python(data["thinking"])
            if data["model"] is not None:
                provider, separator, model_id = data["model"].partition("/")
                if not separator:
                    raise ValueError
                ModelConfig(provider=provider, modelId=model_id)
            if any(type(data[key]) is not int or data[key] < 1
                   for key in ("referenceVersion", "currentVersion")):
                raise ValueError
            if SECRET.search(data["title"]):
                raise ValueError
            return data
        except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
            raise invalid_agent() from None

    def show(self, identifier):
        return self._metadata(self._path(identifier))

    def list(self):
        names = set()
        for root in self.roots.values():
            for file in confined(root, ("agents",)).glob("*/agent.yaml"):
                names.add(agent_name(file.parent.name))
        return [self.show(name) for name in sorted(names)]

    def _read_version(self, identifier, version):
        if type(version) is not int or version < 1:
            raise invalid_agent("unknown_harness_version", 404)
        path = self._path(identifier)
        try:
            confined(path, ("versions",))
            record_file = path / f"versions/v{version}.json"
            archive = path / f"snapshots/v{version}.tar"
            confined(path, ("snapshots",))
            if any(file.is_symlink() or file.is_junction() or file.stat().st_nlink != 1
                   for file in (record_file, archive)):
                raise ValueError
            record = json.loads(record_file.read_bytes())
            data = archive.read_bytes()
            if (set(record) != {"version", "baseVersion", "decision", "contentHash", "archiveHash", "files"}
                or record["version"] != version or type(record["version"]) is not int
                or record["decision"] not in ("reference", "candidate", "accepted", "rejected")
                or (record["baseVersion"] is not None and (
                    type(record["baseVersion"]) is not int or not 1 <= record["baseVersion"] < version
                )) or not re.fullmatch(r"[a-f0-9]{64}", record["contentHash"])
                or hashlib.sha256(data).hexdigest() != record["archiveHash"]):
                raise ValueError
            with tempfile.TemporaryDirectory() as temporary:
                temporary = Path(temporary)
                files = unpack_harness(data, temporary)
                if (content_hash(temporary) != record["contentHash"] or record["files"] != [
                    {"path": name, "bytes": (temporary / name).stat().st_size} for name in files
                ]):
                    raise ValueError
            return record, data
        except FileNotFoundError:
            raise invalid_agent("unknown_harness_version", 404) from None
        except (OSError, ValueError, KeyError, TypeError):
            raise invalid_agent("harness_changed", 409) from None

    def version(self, identifier, version):
        return self._read_version(identifier, version)[0]

    def versions(self, identifier):
        path = self._path(identifier)
        confined(path, ("versions",))
        values = []
        for file in (path / "versions").glob("v*.json"):
            match = re.fullmatch(r"v([1-9][0-9]*)\.json", file.name)
            if not match:
                raise invalid_agent()
            values.append(self.version(identifier, int(match[1])))
        return sorted(values, key=lambda value: value["version"])

    def export(self, identifier, version):
        return self._read_version(identifier, version)[1]

    def diff(self, identifier, version, against):
        before, after = self.export(identifier, against), self.export(identifier, version)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left, right = root / "before", root / "after"
            left.mkdir()
            right.mkdir()
            names = set(unpack_harness(before, left)) | set(unpack_harness(after, right))
            changes = []
            for name in sorted(names):
                old = (left / name).read_text(encoding="utf-8") if (left / name).exists() else ""
                new = (right / name).read_text(encoding="utf-8") if (right / name).exists() else ""
                if old != new:
                    changes.append({"path": name, "patch": "".join(difflib.unified_diff(
                        old.splitlines(keepends=True), new.splitlines(keepends=True),
                        fromfile=f"v{against}/{name}", tofile=f"v{version}/{name}",
                    ))})
            return {"agent": "agent/" + agent_name(identifier), "version": version, "against": against,
                    "changes": changes}

    def _writable(self, identifier):
        source = self._path(identifier)
        target = confined(self.user_root, ("agents", agent_name(identifier)), create=True)
        if source != target:
            # Copy verified inputs only; unrelated files beside agent.yaml are never opened.
            target.rmdir()
            metadata = self._metadata(source)
            harness = pack_harness(confined(source, ("harness",)))
            with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
                staged = Path(temporary) / "agent"
                (staged / "harness").mkdir(parents=True)
                unpack_harness(harness, staged / "harness")
                (staged / "snapshots").mkdir()
                for record in self.versions(identifier):
                    data = self.export(identifier, record["version"])
                    with (staged / f"snapshots/v{record['version']}.tar").open("xb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    durable_json(staged / f"versions/v{record['version']}.json", record)
                durable_json(staged / "agent.yaml", metadata)
                os.rename(staged, target)
        return target

    def _reserve(self, path):
        ledger_path = self.user_root / ".agent-versions.json"
        try:
            if ledger_path.is_symlink() or ledger_path.is_junction():
                raise ValueError
            ledger = json.loads(ledger_path.read_bytes()) if ledger_path.exists() else {}
            if not isinstance(ledger, dict) or any(
                not NAME.fullmatch(key) or type(value) is not int or value < 1
                for key, value in ledger.items()
            ):
                raise ValueError
            used = [record["version"] for record in self.versions(path.name)]
            version = max(ledger.get(path.name, 0), *used, 0) + 1
            ledger[path.name] = version
            durable_json(ledger_path, ledger)
            return version
        except (OSError, ValueError, TypeError):
            raise invalid_agent("agents_unavailable", 503) from None

    def _publish(self, path, harness, version, base):
        data = pack_harness(harness)
        with tempfile.TemporaryDirectory() as temporary:
            frozen = Path(temporary)
            files = unpack_harness(data, frozen)
            record = {"version": version, "baseVersion": base,
                      "decision": "reference" if base is None else "candidate",
                      "contentHash": content_hash(frozen), "archiveHash": hashlib.sha256(data).hexdigest(),
                      "files": [{"path": name, "bytes": (frozen / name).stat().st_size} for name in files]}
        snapshots = confined(path, ("snapshots",), create=True)
        versions = confined(path, ("versions",), create=True)
        # Exclusive creation: neither a version number nor an existing snapshot can be overwritten.
        with (snapshots / f"v{version}.tar").open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if (versions / f"v{version}.json").exists():
            raise invalid_agent("harness_conflict", 409)
        durable_json(versions / f"v{version}.json", record)
        return record

    def _snapshot(self, path):
        metadata = self._metadata(path)
        current = self.version(path.name, metadata["currentVersion"])
        harness = confined(path, ("harness",))
        harness_files(harness)
        if current["contentHash"] == content_hash(harness):
            return current
        version = self._reserve(path)
        record = self._publish(path, harness, version, metadata["currentVersion"])
        durable_json(path / "agent.yaml", metadata | {"currentVersion": version})
        return record

    def snapshot(self, identifier, data=None):
        with import_lock(self.user_root):
            path = self._writable(identifier)
            from packages.library.agent_transitions import recover_path

            recover_path(self, path)
            previous = self._snapshot(path)
            if data is None:
                return previous
            with tempfile.TemporaryDirectory(dir=path) as temporary:
                staged = Path(temporary) / "harness"
                staged.mkdir()
                unpack_harness(data, staged)
                if content_hash(staged) == previous["contentHash"]:
                    return previous
                metadata = self._metadata(path)
                version = self._reserve(path)
                record = self._publish(path, staged, version, previous["version"])
                backup = Path(temporary) / "previous"
                os.rename(path / "harness", backup)
                try:
                    os.rename(staged, path / "harness")
                    durable_json(path / "agent.yaml", metadata | {"currentVersion": version})
                except BaseException:
                    if (path / "harness").exists():
                        shutil.rmtree(path / "harness")
                    os.rename(backup, path / "harness")
                    raise
                return record

    def transition(self, identifier, **request):
        from packages.library.agent_transitions import transition

        return transition(self, identifier, **request)

    def recover(self):
        from packages.library.agent_transitions import recover

        return recover(self)

    def create(self, identifier, data, title=None):
        name = agent_name(identifier)
        title = title or name
        if len(title) > 120 or SECRET.search(title):
            raise invalid_agent()
        with import_lock(self.user_root):
            try:
                self._path(name)
            except LibraryError as error:
                if error.detail["code"] != "unknown_agent":
                    raise
            else:
                raise invalid_agent("agent_conflict", 409)
            parent = confined(self.user_root, ("agents",), create=True)
            target = confined(self.user_root, ("agents", name))
            if target.exists():
                raise invalid_agent("agent_conflict", 409)
            with tempfile.TemporaryDirectory(dir=parent) as temporary:
                staged = Path(temporary) / name
                harness = staged / "harness"
                harness.mkdir(parents=True)
                unpack_harness(data, harness)
                self._publish(staged, harness, 1, None)
                metadata = {"id": "agent/" + name, "title": title, "model": None, "thinking": None,
                            "referenceVersion": 1, "currentVersion": 1}
                durable_json(staged / "agent.yaml", metadata)
                os.rename(staged, target)
            return metadata
