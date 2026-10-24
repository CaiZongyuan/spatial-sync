"""Immutable script material inspection. Uploaded Python is parsed, never imported."""

import ast
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import yaml

from packages.info.schema_subset import compile_schema
from packages.library.archive import destination_parent, extract_package, import_lock, package_files
from packages.library.common import NAME, TYPE_ROOT, LibraryError, document
from packages.library.transactions import durable_json
from packages.programs.metadata import ProgramMetadata
from packages.training.inputs import pack_package

MAX_FILES = 256
MAX_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 1024 * 1024
PUBLIC_IMPORTS = {
    "packages.info.program": {"Program", "ProgramConfig", "Reject", "Ctx", "UNKNOWN"},
    "packages.sim_runtime.physics_port": {"PhysicsPort"},
}
HELPER_IMPORTS = {"__future__", "math", "numpy", "collections", "dataclasses", "typing", "pydantic"}
REFERENCE = re.compile(r"program/robot_arm\.script\.([a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)")


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def invalid(message, *, path=""):
    return LibraryError("program_invalid", message, 422, path=path)


def _module_tree(name, raw, files):
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=name)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise invalid("Program source must be valid UTF-8 Python", path=name) from exc
    parent = PurePosixPath(name).parent.parts
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"eval", "exec", "compile", "__import__"}:
                raise invalid("Dynamic code/import calls are outside the Program contract", path=name)
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] not in HELPER_IMPORTS for alias in node.names):
                raise invalid("Import must use public Program/PhysicsPort or supported helpers", path=name)
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            if node.level > len(parent) + 1:
                raise invalid("Relative imports must stay in the program package", path=name)
            base = parent[: len(parent) + 1 - node.level]
            modules = [node.module] if node.module else [alias.name for alias in node.names]
            for module in modules:
                relative = "/".join((*base, *module.split(".")))
                if relative + ".py" not in files and relative + "/__init__.py" not in files:
                    raise invalid("Relative helper import is not in the frozen package", path=name)
        elif node.module in PUBLIC_IMPORTS:
            if any(alias.name not in PUBLIC_IMPORTS[node.module] for alias in node.names):
                raise invalid(
                    "Import names must belong to the public Program/PhysicsPort interface", path=name
                )
        elif (node.module or "").split(".")[0] not in HELPER_IMPORTS:
            raise invalid("Newton/internal runtime imports are outside the Program contract", path=name)
    return tree


def inspect_package(path):
    """Validate declarative material and hash its original bytes without executing code."""
    try:
        files = package_files(path)
        if "program.yaml" not in files or len(files) > MAX_FILES:
            raise invalid("Program requires program.yaml and at most 256 files")
        if any(name not in ("program.yaml", "README.md") and not name.endswith(".py") for name in files):
            raise invalid("Only program.yaml, Python source and optional README.md are supported")
        if sum((path / name).stat().st_size for name in files) > MAX_BYTES:
            raise invalid("Program package exceeds 8 MiB")
        if (path / "program.yaml").stat().st_size > 64 * 1024:
            raise invalid("Program metadata exceeds 64 KiB")
        material, total = {}, 0
        for name in sorted(files):
            with (path / name).open("rb") as stream:
                material[name] = stream.read(MAX_BYTES - total + 1)
            total += len(material[name])
            if total > MAX_BYTES:
                raise invalid("Program package exceeds 8 MiB")
        metadata = ProgramMetadata.model_validate(yaml.safe_load(material["program.yaml"]))
        interface, action = metadata.implements.rsplit(".", 1)
        entity_type = document(TYPE_ROOT / (interface.replace("/", ".") + ".yaml"))
        if action not in entity_type["actions"]:
            raise invalid("Program action is not declared by its interface")
        compile_schema(metadata.configSchema).validate_python(metadata.config)
        filename, class_name = metadata.entrypoint.split(":")
        if filename not in files:
            raise invalid("Program entrypoint must name a frozen source file")
        hashes, code, content = {}, hashlib.sha256(), hashlib.sha256()
        for name, raw in material.items():
            digest = hashlib.sha256(raw).digest()
            hashes[name] = {"sha256": digest.hex(), "size": len(raw)}
            content.update(name.encode() + b"\0" + digest)
            if not name.endswith(".py"):
                continue
            if len(raw) > MAX_SOURCE_BYTES:
                raise invalid("A Python source file exceeds 1 MiB", path=name)
            tree = _module_tree(name, raw, files)
            if name == filename and not any(
                isinstance(node, ast.ClassDef) and node.name == class_name for node in tree.body
            ):
                raise invalid("Entrypoint must name a declared top-level class", path=name)
            code.update(name.encode() + b"\0" + digest)
        return {
            "metadata": metadata.model_dump(mode="json"),
            "contentHash": content.hexdigest(),
            "codeHash": code.hexdigest(),
            "configHash": fingerprint({"schema": metadata.configSchema, "values": metadata.config}),
            "files": hashes,
        }
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError, RecursionError) as exc:
        raise invalid("Program metadata, configuration or source is invalid") from exc


class ProgramLibrary:
    def __init__(self, roots):
        self.roots = {name: Path(path).expanduser().resolve() for name, path in roots.items()}

    def _versions(self):
        versions = {}
        for root in self.roots.values():
            parent = destination_parent(root, ("programs",))
            if not parent.exists():
                continue
            for name in parent.iterdir():
                if not NAME.fullmatch(name.name):
                    raise ValueError("Unindexed program directory")
                folder = destination_parent(root, ("programs", name.name))
                for revision in folder.iterdir():
                    if not re.fullmatch(r"[1-9][0-9]*", revision.name):
                        raise ValueError("Unindexed program revision")
                    reference = f"program/robot_arm.script.{name.name}@{revision.name}"
                    if reference in versions:
                        raise ValueError("Ambiguous program version")
                    versions[reference] = destination_parent(root, ("programs", name.name, revision.name))
        return versions

    def resolve(self, reference):
        match = REFERENCE.fullmatch(reference) if isinstance(reference, str) else None
        if match is None:
            raise LibraryError(
                "unknown_program", "Select an exact program/robot_arm.script.name@revision", 404
            )
        try:
            folder = self._versions().get(reference)
            if folder is None:
                raise LibraryError("unknown_program", "Unknown program version", 404)
            for name in ("metadata.json", "archive.tar"):
                path = folder / name
                if not path.is_file() or path.is_symlink() or path.is_junction():
                    raise ValueError("Program metadata/archive is not an owned regular file")
            record = json.loads((folder / "metadata.json").read_bytes())
            package = destination_parent(folder, ("package",))
            inspected = inspect_package(package)
            if (
                record["schema"] != "lw.program-version/1"
                or record["id"] != reference
                or record["name"] != match[1]
                or record["rev"] != int(match[2])
                or record["executionScope"] != "simulation-runner"
                or any(record[name] != value for name, value in inspected.items())
                or hashlib.sha256((folder / "archive.tar").read_bytes()).hexdigest()
                != record["archiveSha256"]
            ):
                raise ValueError("Immutable program material changed")
            return record, package
        except (OSError, ValueError, KeyError, TypeError, LibraryError) as exc:
            if isinstance(exc, LibraryError) and exc.detail["code"] == "unknown_program":
                raise
            raise LibraryError(
                "program_unavailable", "Program snapshot changed or is unavailable", 409
            ) from exc

    def show(self, reference):
        return self.resolve(reference)[0]

    def list(self):
        try:
            return [self.show(reference) for reference in sorted(self._versions())]
        except (OSError, ValueError) as exc:
            raise LibraryError("program_unavailable", "Program index is unavailable", 409) from exc

    def export(self, reference):
        _, package = self.resolve(reference)
        return (package.parent / "archive.tar").read_bytes()

    @staticmethod
    def inspect(stream):
        with tempfile.TemporaryDirectory(prefix="lw-program-inspect-") as temporary:
            package = Path(temporary)
            extract_package(stream, package)
            return inspect_package(package)

    def push(self, stream, actor):
        root = self.roots["user"]
        root.mkdir(parents=True, exist_ok=True)
        with import_lock(root):
            with tempfile.TemporaryDirectory(prefix=".program-", dir=root) as temporary:
                stage = Path(temporary)
                package = stage / "package"
                package.mkdir()
                extract_package(stream, package)
                inspected = inspect_package(package)
                name = inspected["metadata"]["name"]
                versions = [value for value in self.list() if value["name"] == name]
                previous = next(
                    (value for value in versions if value["contentHash"] == inspected["contentHash"]), None
                )
                if previous is not None:
                    return previous | {"idempotent": True}
                revision = max((value["rev"] for value in versions), default=0) + 1
                archive = pack_package(package)
                (stage / "archive.tar").write_bytes(archive)
                record = {
                    "schema": "lw.program-version/1",
                    "id": f"program/robot_arm.script.{name}@{revision}",
                    "name": name,
                    "rev": revision,
                    **inspected,
                    "archiveSha256": hashlib.sha256(archive).hexdigest(),
                    "executionScope": "simulation-runner",
                    "requestedBy": actor.model_dump(mode="json"),
                    "createdAt": datetime.now(UTC).isoformat(),
                }
                durable_json(stage / "metadata.json", record)
                parent = destination_parent(root, ("programs", name), create=True)
                destination = parent / str(revision)
                if destination.exists() or destination.is_symlink() or destination.is_junction():
                    raise invalid("Program version already exists")
                os.rename(stage, destination)
                return self.show(record["id"]) | {"idempotent": False}
