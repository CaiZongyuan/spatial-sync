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
from packages.library.common import NAME, TYPE_ROOT, LibraryError, content_hash, document
from packages.library.templates import resolve
from packages.library.transactions import durable_json
from packages.training.schema import TaskMetadata


class TaskLibrary:
    def __init__(self, assets, scenes):
        self.assets, self.roots, self.scenes = assets, assets.roots, scenes
        self.user_root = self.roots["user"]

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

    def _validate(self, package):
        try:
            files = package_files(package)
            if not {"task.yaml", "pyproject.toml"}.issubset(files):
                raise ValueError("Task requires task.yaml and pyproject.toml")
            if (package / "task.yaml").stat().st_size > 1024 * 1024:
                raise ValueError("task.yaml exceeds 1 MiB")
            task = TaskMetadata.model_validate(document(package / "task.yaml"))
            observation = relative_path(task.observation).as_posix()
            if observation not in files:
                raise ValueError("observation must name an existing package-local file")
            project = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
            entries = project.get("project", {}).get("entry-points", {}).get("isaaclab.tasks", {})
            if not entries or not isinstance(entries, dict) or any(
                not isinstance(key, str) or not isinstance(value, str) or not value.strip()
                for key, value in entries.items()
            ):
                raise ValueError("pyproject.toml requires an isaaclab.tasks entry point")
            scenes = self.scenes()
            scenes.refresh()
            scene = scenes.resolve(task.scene)
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

            types = {item["type"]: item for item in (document(path) for path in TYPE_ROOT.glob("*.yaml"))}
            interface, action = task.implements.split(".")
            if entity_type(task.robot) != interface or action not in types[interface]["actions"]:
                raise ValueError("implements must name an action of the selected robot type")
            validate_conditions([task.success], task.robot,
                                lambda entity, prop: types[entity_type(entity)]["properties"][prop]["schema"])
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
                record = {"id": f"task/{name}@{revision}", "name": name, "rev": revision,
                          "title": task["title"], "contentHash": digest, "task": task,
                          "sceneContentHash": scene.content_hash}
                durable_json(stage / "metadata.json", record)
                destination = destination_parent(root, ("tasks", name), create=True) / str(revision)
                if destination.exists() or destination.is_symlink() or destination.is_junction():
                    raise invalid("Task revision already exists")
                os.rename(stage, destination)
                return {**record, "idempotent": False}
