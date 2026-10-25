"""Load approved frozen Python only in an explicitly owned Newton worker."""

import hashlib
import importlib
import importlib.abc
import importlib.machinery
import inspect
import multiprocessing as mp
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from packages.info.program import ProgramConfig
from packages.library.assets import AssetLibrary
from packages.library.common import LibraryError
from packages.programs.authorization import require_runtime_approval
from packages.programs.library import ProgramLibrary
from packages.programs.subjects import fingerprint


def invalid(message):
    return LibraryError("program_runtime_invalid", message, 422)


@dataclass
class PreparedProgram:
    record: dict
    delegate: object
    config: ProgramConfig
    initialized: bool = False


class _FrozenModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolve only declared package files. No sys.path mutation or bytecode writes."""

    def __init__(self, package, record):
        self.package, self.record = package, record
        self.prefix = "_lw_program_" + uuid.uuid4().hex

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.prefix and not fullname.startswith(self.prefix + "."):
            return None
        relative = fullname.removeprefix(self.prefix).lstrip(".").replace(".", "/")
        files = self.record["files"]
        if not relative or relative + "/__init__.py" in files:
            filename = (relative + "/" if relative else "") + "__init__.py"
            filename = filename if filename in files else None
            package = True
        elif relative + ".py" in files:
            filename, package = relative + ".py", False
        elif any(name.startswith(relative + "/") for name in files):
            filename, package = None, True
        else:
            return None
        spec = importlib.machinery.ModuleSpec(fullname, self, is_package=package)
        spec.loader_state = filename
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        filename = module.__spec__.loader_state
        if module.__spec__.submodule_search_locations is not None:
            module.__path__ = []
        if filename is None:
            return
        path = self.package / filename
        if (
            not path.is_file()
            or path.is_symlink()
            or path.is_junction()
            or not path.resolve().is_relative_to(self.package)
        ):
            raise invalid("Frozen Python source is unavailable")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.record["files"][filename]["sha256"]:
            raise invalid("Frozen Python source changed before import")
        module.__file__ = str(path)
        exec(compile(raw, str(path), "exec"), module.__dict__)

    def close(self):
        if self in sys.meta_path:
            sys.meta_path.remove(self)
        for name in tuple(sys.modules):
            if name == self.prefix or name.startswith(self.prefix + "."):
                del sys.modules[name]


class ProgramLoader:
    def __init__(self, session_id, *, state_root):
        if mp.parent_process() is None or mp.current_process().name != "newton-" + session_id:
            raise LibraryError(
                "program_source_unsupported", "Program loading requires the owned Newton Runner", 422
            )
        self.state_root = Path(state_root)
        self.library = ProgramLibrary(AssetLibrary(self.state_root).roots)
        self.modules = []

    def load_evaluation(self, approval):
        proof = require_runtime_approval(approval, self.state_root)
        record, package = self.library.resolve(proof.program.id)
        if record["contentHash"] != proof.program.contentHash:
            raise invalid("Approved Python package hash differs")
        return self._load(record, package)

    def load_deployment(self, permit, *, scene, catalog, entity_key):
        from packages.programs.deployment import verify_runtime_deployment

        verified = verify_runtime_deployment(scene, catalog, permit, state_root=self.state_root)
        identity = verified.permit["materialIdentities"].get(entity_key)
        if identity is None or identity["kind"] != "program":
            raise invalid("Script binding has no committed material identity")
        record, package = self.library.resolve(identity["id"])
        if any(
            record[name] != identity[name]
            for name in ("contentHash", "codeHash", "configHash", "archiveSha256")
        ):
            raise invalid("Actual deployed script material differs")
        return self._load(record, package)

    def _load(self, record, package):
        modules = _FrozenModules(package, record)
        sys.meta_path.insert(0, modules)
        try:
            filename, name = record["metadata"]["entrypoint"].split(":")
            entry = Path(filename)
            module_path = entry.parent if entry.name == "__init__.py" else entry.with_suffix("")
            module_name = module_path.as_posix().replace("/", ".")
            module = importlib.import_module(
                modules.prefix + ("." + module_name if module_name != "." else "")
            )
            definition = getattr(module, name)
            if (
                not isinstance(definition, type)
                or definition.id != "robot_arm.script." + record["name"]
                or definition.type != "robot-arm/v1"
                or definition.tick_priority != 10
                or not isinstance(definition.Config, type)
                or not issubclass(definition.Config, ProgramConfig)
                or definition.Config.model_config.get("extra") != "forbid"
                or definition.Config.model_config.get("frozen") is not True
                or any(
                    not callable(getattr(definition, method, None))
                    or inspect.iscoroutinefunction(getattr(definition, method))
                    or inspect.isgeneratorfunction(getattr(definition, method))
                    or inspect.isasyncgenfunction(getattr(definition, method))
                    for method in ("init", "accept", "start", "tick", "cancel")
                )
            ):
                raise invalid("Entrypoint does not implement the declared Program contract")
            config = definition.Config.model_validate(record["metadata"]["config"])
            if fingerprint(config.model_dump(mode="json")) != fingerprint(record["metadata"]["config"]):
                raise invalid("Runtime Config normalization differs from the reviewed declarative values")
            prepared = PreparedProgram(record, definition(), config)
        except BaseException as error:
            modules.close()
            if isinstance(error, LibraryError):
                raise
            raise invalid(
                "Python entrypoint, Config or constructor failed: " + type(error).__name__
            ) from error
        self.modules.append(modules)
        return prepared

    def close(self):
        for modules in self.modules:
            modules.close()
        self.modules.clear()
