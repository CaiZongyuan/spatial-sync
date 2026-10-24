"""Immutable Demonstrations and versioned indexes over unchanged recording packages."""

import json
import os
import re
import shutil
import tempfile
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from packages.info.plans import PlanError
from packages.library.archive import destination_parent, import_lock, package_files
from packages.library.common import content_hash
from packages.library.transactions import durable_json

SET_ID = re.compile(r"demoset/([a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)")
RECORDING_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}")


class Demonstrations:
    def __init__(self, assets):
        self.assets = assets
        self.root = assets.roots["user"]

    def _sets(self):
        root = destination_parent(self.root, ("demonstrations", "sets"))
        if not root.exists():
            return []
        values = []
        for name in root.iterdir():
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name.name):
                raise PlanError("demo_set_unavailable", "Invalid Demonstration Set index")
            parent = destination_parent(self.root, ("demonstrations", "sets", name.name))
            for version in parent.iterdir():
                if not re.fullmatch(r"[1-9][0-9]*", version.name):
                    raise PlanError("demo_set_unavailable", "Invalid Demonstration Set version")
                values.append(self.show(f"demoset/{name.name}@{version.name}"))
        return values

    def show(self, identifier):
        match = SET_ID.fullmatch(identifier)
        if not match:
            raise PlanError("unknown_demo_set", "Select an exact demoset/name@revision", status=404)
        try:
            path = destination_parent(self.root, ("demonstrations", "sets", match[1], match[2]))
            value = json.loads((path / "manifest.json").read_bytes())
            if value["schemaVersion"] != 1 or value["id"] != identifier or not value["demonstrations"]:
                raise ValueError("Invalid Demonstration Set manifest")
            package_files(path)
            return value | {"contentHash": content_hash(path)}
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise PlanError(
                "demo_set_unavailable", "Demonstration Set is missing or changed", status=409
            ) from error

    def _latest(self):
        latest = {}
        for value in self._sets():
            previous = latest.get(value["name"])
            if previous is None or value["revision"] > previous["revision"]:
                latest[value["name"]] = value
        return latest

    def list(self, task=None):
        values = []
        for version in self._latest().values():
            for entry in version["demonstrations"]:
                if task is None or entry["task"] == task:
                    values.append(deepcopy(entry) | {"set": {
                        "id": version["id"], "contentHash": version["contentHash"],
                    }})
        return values

    def find(self, recording_id):
        return next((entry for entry in self.list() if entry["recordingId"] == recording_id), None)

    def retry(self, entry):
        original = self.show(entry["addedInSet"])
        return deepcopy(entry) | {
            "set": {"id": original["id"], "contentHash": original["contentHash"]}, "idempotent": True,
        }

    def _recording_path(self, identifier):
        if not RECORDING_ID.fullmatch(identifier):
            raise PlanError("unknown_demo_recording", "Unknown Demonstration recording", status=404)
        return destination_parent(self.root, ("demonstrations", "recordings", identifier))

    def recording(self, identifier, expected_hash):
        try:
            path = self._recording_path(identifier)
            if not path.exists() or not package_files(path) or content_hash(path) != expected_hash:
                raise ValueError("Demonstration recording changed")
            return path
        except (OSError, ValueError) as error:
            raise PlanError(
                "demo_recording_changed", "Frozen Demonstration recording is unavailable", status=409
            ) from error

    def add(self, evidence, source, actor):
        """Publication is one version rename; an orphan frozen recording is harmless on retry."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self.assets._lock, import_lock(self.root):
            existing = self.find(evidence["recordingId"])
            if existing is not None:
                if existing["task"] != evidence["task"]:
                    raise PlanError("demo_task_conflict", "This recording already has a fixed source Task")
                return self.retry(existing)
            files = package_files(source)
            if not files:
                raise PlanError("demo_recording_changed", "Recording package is empty", status=409)
            digest = evidence["recordingContentHash"]
            if content_hash(source) != digest:
                raise PlanError("demo_recording_changed", "Validated recording changed before freezing")
            frozen = self._recording_path(evidence["recordingId"])
            if frozen.exists():
                self.recording(evidence["recordingId"], digest)
            else:
                parent = destination_parent(self.root, ("demonstrations", "recordings"), create=True)
                with tempfile.TemporaryDirectory(prefix=".freeze-", dir=parent) as temporary:
                    stage = Path(temporary) / "recording"
                    shutil.copytree(source, stage)
                    package_files(stage)
                    if content_hash(stage) != digest or content_hash(source) != digest:
                        raise PlanError("demo_recording_changed", "Recording changed mid-freeze", status=409)
                    os.rename(stage, frozen)
            name = evidence["task"].split("/", 1)[1].rsplit("@", 1)[0]
            previous = self._latest().get(name)
            revision = previous["revision"] + 1 if previous else 1
            set_id = f"demoset/{name}@{revision}"
            entry = deepcopy(evidence) | {
                "id": "demo-" + uuid.uuid4().hex,
                "recordingContentHash": digest,
                "recordingUrl": f"/api/v1/demos/recordings/{evidence['recordingId']}/recording.json",
                "markedAt": datetime.now(UTC).isoformat(),
                "markedBy": actor.model_dump(mode="json"),
                "addedInSet": set_id,
            }
            manifest = {
                "schemaVersion": 1, "id": set_id, "name": name, "revision": revision,
                "demonstrations": [*(previous["demonstrations"] if previous else []), entry],
            }
            parent = destination_parent(self.root, ("demonstrations", "sets", name), create=True)
            with tempfile.TemporaryDirectory(prefix=".demoset-", dir=self.root) as temporary:
                stage = Path(temporary) / "package"
                stage.mkdir()
                durable_json(stage / "manifest.json", manifest)
                os.rename(stage, parent / str(revision))
            published = self.show(manifest["id"])
            return entry | {"set": {"id": published["id"], "contentHash": published["contentHash"]},
                            "idempotent": False}

    def resolve_inputs(self, declarations, task):
        values = []
        for declaration in declarations:
            dataset = self.show(declaration["set"])
            for entry in dataset["demonstrations"]:
                if any(
                    entry[key] != task[key] for key in ("scene", "sceneContentHash", "robot", "implements")
                ):
                    raise PlanError("demo_task_mismatch", "Demonstrations have different fixed Task bindings")
                self.recording(entry["recordingId"], entry["recordingContentHash"])
            values.append({"id": dataset["id"], "contentHash": dataset["contentHash"],
                           "purpose": declaration["purpose"]})
        return values

    def set_package(self, identifier, expected_hash):
        dataset = self.show(identifier)
        if dataset["contentHash"] != expected_hash:
            raise PlanError("demo_set_changed", "Pinned Demonstration Set content changed")
        match = SET_ID.fullmatch(identifier)
        return destination_parent(self.root, ("demonstrations", "sets", match[1], match[2]))

    def recording_inputs(self, references):
        values = {}
        for reference in references:
            self.set_package(reference["id"], reference["contentHash"])
            for entry in self.show(reference["id"])["demonstrations"]:
                identifier, digest = entry["recordingId"], entry["recordingContentHash"]
                if identifier in values and values[identifier]["contentHash"] != digest:
                    raise PlanError("demo_recording_changed", "DemoSet recording identities disagree")
                self.recording(identifier, digest)
                values[identifier] = {"id": identifier, "contentHash": digest}
        return list(values.values())
