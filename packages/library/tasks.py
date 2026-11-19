"""Confined Task source snapshots. Pushing a package never imports or executes it."""

import json
import os
import re
import tempfile
import tomllib
from fnmatch import fnmatchcase
from pathlib import Path

from pydantic import ValidationError

from packages.info.conditions import validate_conditions
from packages.library.archive import (
    destination_parent,
    extract_package,
    import_lock,
    invalid,
    package_files,
    relative_path,
)
from packages.library.common import NAME, LibraryError, content_hash, document, load_entity_types
from packages.library.templates import resolve
from packages.library.transactions import durable_json
from packages.policies.observations import ObservationSpec
from packages.training.inputs import pack_package
from packages.training.schema import TaskMetadata


class TaskLibrary:
    def __init__(self, assets, scenes):
        self.assets, self.roots, self.scenes = assets, assets.roots, scenes
        self.user_root = self.roots["user"]
        self.demonstrations = None

    def _versions(self):
        values = {}
        for root in self.roots.values():
            parent = destination_parent(root, ("tasks",))
            if not parent.exists():
                continue
            for name in parent.iterdir():
                if not NAME.fullmatch(name.name) or not name.is_dir():
                    raise LibraryError("task_unavailable", "Unindexed Task package", 409)
                for version in destination_parent(root, ("tasks", name.name)).iterdir():
                    if not re.fullmatch(r"[1-9][0-9]*", version.name) or not version.is_dir():
                        raise LibraryError("task_unavailable", "Unindexed Task version", 409)
                    path = destination_parent(root, ("tasks", name.name, version.name))
                    values[f"task/{name.name}@{version.name}"] = path
        return values

    def _read(self, identifier, path):
        try:
            record = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
            package = destination_parent(path, ("package",))
            if record["id"] != identifier or content_hash(package) != record["contentHash"]:
                raise ValueError("Task package changed")
            scene = self.scenes().resolve(record["task"]["scene"])
            if scene.content_hash != record["sceneContentHash"]:
                raise ValueError("Pinned Task scene changed")
            return record
        except (OSError, KeyError, ValueError, LibraryError) as exc:
            raise LibraryError("task_unavailable", "Task snapshot changed or is unavailable", 409) from exc

    def list(self):
        return [self._read(identifier, path) for identifier, path in sorted(self._versions().items())]

    def show(self, identifier):
        if not re.fullmatch(r"task/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*", identifier):
            raise LibraryError("unknown_task", "Select an exact task/name@revision", 404)
        path = self._versions().get(identifier)
        if path is None:
            raise LibraryError("unknown_task", "Unknown Task version", 404)
        return self._read(identifier, path)

    def observation(self, identifier):
        record = self.show(identifier)
        path = self._versions()[identifier] / "package" / relative_path(record["task"]["observation"])
        return ObservationSpec.model_validate(document(path))

    def export(self, identifier):
        self.show(identifier)
        package = self._versions()[identifier] / "package"
        return pack_package(package)

    def require_execution(self, record):
        if record["task"].get("declarative") is not None:
            return
        if self._audited(record["id"], record["contentHash"]):
            return
        raise LibraryError("task_audit_required", "Python Task needs an exact operator audit", 403)

    def _audited(self, identifier, digest):
        name, version = identifier.removeprefix("task/").split("@")
        builtin = self.roots["builtin"] / "tasks" / name / version / "package"
        if builtin.is_dir() and content_hash(builtin) == digest:
            return True
        path = os.getenv("LW_TASK_AUDIT_FILE")
        if path:
            try:
                document = json.loads(Path(path).read_bytes())
                for entry in document:
                    if entry["contentHash"] == digest and entry["reviewer"].strip() and entry["note"].strip():
                        return True
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                raise LibraryError("task_audit_invalid", "Invalid host Task audit registry", 409) from error
        return False

    def _validate(self, package):
        try:
            files = package_files(package)
            if "task.yaml" not in files:
                raise ValueError("Task requires task.yaml")
            if (package / "task.yaml").stat().st_size > 1024 * 1024:
                raise ValueError("task.yaml exceeds 1 MiB")
            task = TaskMetadata.model_validate(document(package / "task.yaml"))
            observation = relative_path(task.observation).as_posix()
            if observation not in files:
                raise ValueError("observation must name an existing package-local file")
            obs_doc = document(package / observation)
            if (
                task.declarative is not None
                or "schema" in obs_doc
                or "observations" in obs_doc
                or task.episode.commandEnvelope is not None
            ):
                spec = ObservationSpec.model_validate(obs_doc)
                if spec.action.space != task.control.action:
                    raise ValueError("obs_spec action space must match task.control.action")
                command_obs = [item for item in spec.observations if item.source == "command"]
                if command_obs:
                    envelope = task.episode.commandEnvelope
                    if envelope is None:
                        raise ValueError("command observation requires task.episode.commandEnvelope")
                    for item in command_obs:
                        if len(envelope) != item.shape[0] or any(
                            not (lo <= float(env_lo) <= float(env_hi) <= hi)
                            for (env_lo, env_hi), lo, hi in zip(
                                envelope, item.lower, item.upper, strict=True
                            )
                        ):
                            raise ValueError(
                                "task.episode.commandEnvelope must match command observation width "
                                "and lie within [lower, upper]"
                            )
                elif task.episode.commandEnvelope is not None:
                    raise ValueError("commandEnvelope requires a command observation in obs_spec")
            if task.declarative is not None:
                if set(files) != {"task.yaml", observation}:
                    raise ValueError("Declarative Task contains only task.yaml and its observation data")
                if spec.schema_version != "lw.obs-spec/2":
                    raise ValueError("Declarative Task requires lw.obs-spec/2")
                motion = task.referenceMotion
                for item in spec.observations:
                    if item.source == "motion_phase" and (
                        motion is None or item.shape != [2 if motion.loop else 1]
                    ):
                        raise ValueError("Motion phase layout differs from the reference motion")
                    if item.source == "reference_motion_frame" and (
                        motion is None or item.shape != [7 + len(motion.jointKeys)]
                    ):
                        raise ValueError("Reference frame layout differs from the reference motion")
                if any(key.startswith("$") for item in spec.observations
                       for key in (item.key, item.frame) if key):
                    raise ValueError("Declarative observation sources require canonical paths")
                command = task.declarative.command
                if command_obs and (
                    not command or not envelope or len(command) != len(envelope)
                    or any(not lo <= a <= b <= hi for (a, b), (lo, hi) in zip(command, envelope, strict=True))
                ):
                    raise ValueError("Command sampling must be inside the admitted command envelope")
                if bool(command_obs) != bool(command):
                    raise ValueError("Command observation and sampling must be declared together")
                if task.declarative.reset.jointPosition and len(task.declarative.reset.jointPosition) != (
                    len(spec.action.jointKeys)
                ):
                    raise ValueError("Reset offsets must follow action joint order")
            else:
                if "pyproject.toml" not in files:
                    raise ValueError("Python Task requires pyproject.toml")
                project = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
                entry_group = "mjlab.tasks" if task.mjlab is not None else "isaaclab.tasks"
                entries = project.get("project", {}).get("entry-points", {}).get(entry_group, {})
                if not entries or not isinstance(entries, dict) or any(
                    not isinstance(key, str) or not isinstance(value, str) or not value.strip()
                    for key, value in entries.items()
                ):
                    raise ValueError(f"pyproject.toml requires a {entry_group} entry point")
            scenes = self.scenes()
            scenes.refresh()
            scene = scenes.resolve(task.scene)
            if task.declarative is not None:
                if scene.metadata["stage"].startswith("python:") or scene.metadata["sources"] != ["newton"]:
                    raise ValueError("Declarative Task requires a canonical Newton USD scene")
            if task.referenceMotion is not None:
                motion = task.referenceMotion
                assets = scene.asset_dependencies() + scene.template_dependencies()
                if {"id": motion.asset, "contentHash": motion.assetContentHash} not in assets:
                    raise ValueError("Reference motion must pin an actual Scene Asset dependency")
            if task.robot not in {item.get("key") for item in scene.bindings["entities"]}:
                raise ValueError("robot must identify an entity in the pinned scene")
            resolved = resolve(scene.bindings, scene.metadata["sources"][0], scene.template_assets)
            bindings = resolved.bootstrap()
            def entity_type(key):
                matched = [item["type"] for item in bindings["entities"] if item.get("key") == key
                           or item.get("match") and fnmatchcase(key, item["match"])]
                if len(matched) != 1:
                    raise ValueError("Task condition entity must match exactly one scene binding")
                return matched[0]

            types = load_entity_types() | scene.entity_types()
            interface, action = task.implements.split(".")
            if entity_type(task.robot) != interface or action not in types[interface]["actions"]:
                raise ValueError("implements must name an action of the selected robot type")
            validate_conditions([task.success], task.robot,
                                lambda entity, prop: types[entity_type(entity)]["properties"][prop]["schema"])
            if task.demonstrations:
                if self.demonstrations is None:
                    raise ValueError("Demonstration Set resolver is unavailable")
                self.demonstrations.resolve_inputs(
                    [item.model_dump(mode="json") for item in task.demonstrations],
                    task.model_dump(mode="json") | {"sceneContentHash": scene.content_hash},
                )
            return task.model_dump(mode="json"), scene
        except (OSError, ValueError, KeyError, ValidationError) as exc:
            raise invalid(str(exc), path="task.yaml") from exc

    def push(self, stream):
        root = self.user_root
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".task-import-", dir=root) as temporary:
            stage = Path(temporary)
            package = stage / "package"
            package.mkdir()
            extract_package(stream, package)
            digest = content_hash(package)
            with self.assets._lock, import_lock(root):
                task, scene = self._validate(package)
                name = task["name"]
                versions = [item for item in self.list() if item["name"] == name]
                same = next((item for item in versions if item["contentHash"] == digest), None)
                if same:
                    return {**same, "idempotent": True}
                revision = 1 + max((item["rev"] for item in versions), default=0)
                identifier = f"task/{name}@{revision}"
                if task.get("declarative") is None:
                    self.require_execution({"task": task, "id": identifier, "contentHash": digest})
                record = {"id": identifier, "name": name, "rev": revision,
                          "title": task["title"], "contentHash": digest, "task": task,
                          "sceneContentHash": scene.content_hash}
                durable_json(stage / "metadata.json", record)
                destination = destination_parent(root, ("tasks", name), create=True) / str(revision)
                if destination.exists() or destination.is_symlink() or destination.is_junction():
                    raise invalid("Task revision already exists")
                os.rename(stage, destination)
                return {**record, "idempotent": False}
