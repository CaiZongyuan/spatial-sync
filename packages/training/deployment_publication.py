"""Publish a frozen Scene/Controller intent once; Controller current is the commit point."""

import json
import threading
from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatchcase
from hashlib import sha256
from pathlib import Path

import yaml

from packages.info.plans import PlanError
from packages.library.archive import destination_parent, import_lock, package_files
from packages.library.common import NAME, LibraryError, content_hash, document
from packages.library.robot_resources import Controller, ControllerVersion
from packages.library.transactions import durable_json
from packages.policies.subjects import fixed_bindings
from packages.programs.subjects import SimulationSubject, fingerprint

from .deployment_authority import read_committed_permit  # noqa: F401


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


@dataclass(frozen=True)
class DeploymentPlan:
    promotion_id: str
    scene: object
    subject: SimulationSubject
    controller: dict
    version_fields: dict
    context_hash: str


class DeploymentPublication:
    def __init__(
        self,
        assets,
        scenes,
        resources,
        importer,
        state_root,
        *,
        validate_context,
        commit_context,
        after_effect=None,
    ):
        self.assets, self.scenes, self.resources, self.importer = assets, scenes, resources, importer
        self.root = Path(state_root) / "controller-publications"
        if self.root.is_symlink() or self.root.is_junction():
            raise PlanError("publication_unavailable", "Publication state cannot be a filesystem link")
        self.root.mkdir(parents=True, exist_ok=True)
        self.library = assets.roots["user"]
        self.validate_context, self.commit_context = validate_context, commit_context
        self.after_effect = after_effect or (lambda *_: None)
        self._lock = threading.RLock()

    def _directory(self, identifier, *, create=False):
        if not isinstance(identifier, str) or not NAME.fullmatch(identifier):
            raise PlanError("invalid_publication", "Invalid publication identity", status=422)
        return destination_parent(self.root, (identifier,), create=create)

    def read(self, identifier):
        return document(self._directory(identifier) / "record.json")

    def _save(self, record):
        durable_json(self._directory(record["id"], create=True) / "record.json", record)
        return deepcopy(record)

    def _current(self, identifier):
        metadata = self.resources._documents("controllers", Controller).get(identifier)
        if metadata is None:
            raise PlanError("unknown_controller", "Controller metadata is unavailable")
        version = metadata["currentVersion"]
        hashes = set()
        if version is not None:
            for root in self.resources.roots.values():
                relative = Path("controllers") / identifier / f"{version}.yaml"
                if (root / relative).exists():
                    value = self.resources._read(root, relative, ControllerVersion, identifier)
                    hashes.add(value["contentHash"])
            if len(hashes) != 1:
                raise PlanError("controller_current_changed", "Current Controller snapshot is unavailable")
        return {
            "metadataHash": metadata["contentHash"],
            "version": version,
            "versionHash": next(iter(hashes), None),
        }

    def _reserve_controller(self, identifier):
        ledger = self.library / ".controller-revisions.json"
        if ledger.is_symlink() or ledger.is_junction():
            raise PlanError("publication_unavailable", "Controller reservations cannot be a filesystem link")
        values = document(ledger) if ledger.exists() else {}
        if any(
            not NAME.fullmatch(key) or type(value) is not int or value < 1 for key, value in values.items()
        ):
            raise PlanError("publication_unavailable", "Controller revision reservations are invalid")
        existing = [
            int(file.stem)
            for root in self.resources.roots.values()
            for file in (root / "controllers" / identifier).glob("*.yaml")
            if file.stem.isdecimal()
        ]
        version = max([values.get(identifier, 0), *existing]) + 1
        durable_json(ledger, values | {identifier: version})
        return version

    @staticmethod
    def _derived_bindings(scene, subject, bindings):
        result, changed = [], 0
        for authored in bindings["entities"]:
            selected = [
                key
                for key in subject.baseBindings
                if key == authored.get("key") or authored.get("match") and fnmatchcase(key, authored["match"])
            ]
            if subject.target.entityKey not in selected:
                result.append(deepcopy(authored))
                continue
            for key in selected:
                if key == subject.target.entityKey:
                    replacement = deepcopy(subject.binding)
                    if "alerts" in authored:
                        replacement["alerts"] = deepcopy(authored["alerts"])
                    result.append(replacement)
                    changed += 1
                else:
                    other = {name: value for name, value in deepcopy(authored).items() if name != "match"}
                    result.append(other | {"key": key})
        if changed != 1:
            raise PlanError("publication_binding_changed", "Expected one authored target declaration")
        return {"scene": scene.name, "entities": result}

    def prepare(self, plan: DeploymentPlan):
        with self._lock:
            directory = self._directory(plan.promotion_id, create=True)
            intent = {
                "baseScene": {"id": plan.scene.id, "contentHash": plan.scene.content_hash},
                "subject": plan.subject.document(),
                "controller": plan.controller,
                "versionFields": plan.version_fields,
                "contextHash": plan.context_hash,
                "libraryRoot": str(self.library),
            }
            if (directory / "record.json").exists():
                previous = self.read(plan.promotion_id)
                if previous["intentHash"] != fingerprint(intent):
                    raise PlanError("publication_changed", "Publication retry differs from its frozen intent")
                return previous
            plan.scene.require_launchable()
            if (plan.scene.id, plan.scene.content_hash) != (
                plan.subject.evaluationScene.id,
                plan.subject.evaluationScene.contentHash,
            ):
                raise PlanError("publication_scene_changed", "Subject does not belong to the base Scene")
            fields = plan.version_fields
            allowed = set(ControllerVersion.model_fields) - {"controller", "version", "scene"}
            required = {key for key, value in ControllerVersion.model_fields.items() if value.is_required()}
            required -= {"controller", "version", "scene"}
            candidate = plan.subject.document()["candidateIdentity"]
            artifact = {key: candidate[key] for key in ("id", "contentHash")}
            policy = candidate["id"] if candidate["kind"] == "policy" else None
            program_artifact = artifact if candidate["kind"] == "program" else None
            if (
                not required <= set(fields) <= allowed
                or fields["embodiment"] != plan.controller["embodiment"]
                or fields["program"] != plan.subject.binding["program"]
                or fields["policy"] != policy
                or fields.get("programArtifact") != program_artifact
            ):
                raise PlanError("publication_material_changed", "Controller and Subject composition differ")
            stage = directory / "scene"
            stage.mkdir()
            for name in package_files(plan.scene.path):
                if name != "validation.json":
                    target = stage / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((plan.scene.path / name).read_bytes())
            plan.scene.require_launchable()
            metadata = document(stage / "scene.yaml")
            metadata["derivedFrom"] = intent["baseScene"] | {"promotion": plan.promotion_id}
            (stage / "scene.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
            bindings = self._derived_bindings(plan.scene, plan.subject, document(stage / "devices.yaml"))
            (stage / "devices.yaml").write_text(yaml.safe_dump(bindings, sort_keys=False), encoding="utf-8")
            metadata = {key: plan.controller[key] for key in Controller.model_fields}
            with self.assets._lock, import_lock(self.library):
                expected_current = self._current(metadata["id"])
                if expected_current["metadataHash"] != plan.controller["contentHash"]:
                    raise PlanError("controller_current_changed", "Controller changed during preparation")
                version = self._reserve_controller(metadata["id"])
            record = {
                "schema": "lw.controller-publication/1",
                "id": plan.promotion_id,
                "intentHash": fingerprint(intent),
                "intent": intent,
                "phase": "prepared",
                "sceneUploadHash": content_hash(stage),
                "controllerVersion": version,
                "expectedCurrent": expected_current,
                "newCurrent": metadata | {"currentVersion": version},
            }
            return self._save(record)

    def _scene(self, record):
        value = record["scene"]
        source = record["intent"]["subject"]["evaluationScene"]["source"]
        scene = self.scenes.resolve(value["id"], launch=True, source=source)
        if scene.content_hash != value["contentHash"]:
            raise PlanError("publication_scene_changed", "Published Scene bytes differ")
        expected = dict(record["intent"]["subject"]["baseBindings"])
        subject = record["intent"]["subject"]
        expected[subject["target"]["entityKey"]] = subject["bindingHash"]
        actual = fixed_bindings(scene, subject["evaluationScene"]["source"])
        if {key: fingerprint(binding) for key, binding in actual.items()} != expected:
            raise PlanError("publication_binding_changed", "Published Scene bindings differ")
        return scene

    def _version(self, record):
        identifier = record["newCurrent"]["id"]
        path = destination_parent(self.library, ("controllers", identifier), create=True)
        return path / f"{record['controllerVersion']}.yaml"

    def _check(self, record):
        self.validate_context(record)
        if self._current(record["newCurrent"]["id"]) != record["expectedCurrent"]:
            raise PlanError("controller_current_changed", "Another Controller current replaced this context")

    def _own_commit(self, record):
        if "controller" not in record:
            return False
        actual = self._current(record["newCurrent"]["id"])
        if actual["metadataHash"] != sha256(_json_bytes(record["newCurrent"])).hexdigest():
            return False
        self._scene(record)
        if sha256(self._version(record).read_bytes()).hexdigest() != record["controller"]["contentHash"]:
            raise PlanError("publication_controller_changed", "Published Controller snapshot differs")
        return True

    def committed(self, identifier):
        """Read the actual commit point even when the final journal write was interrupted."""
        if not (self._directory(identifier) / "record.json").exists():
            return False
        record = self.read(identifier)
        return record["phase"] == "committed" or self._own_commit(record)

    def publish(self, identifier):
        with self._lock:
            record = self.read(identifier)
            if record["phase"] in ("committed", "blocked"):
                return record
            try:
                if self._own_commit(record):
                    record["phase"] = "committed"
                    return self._save(record)
                self._check(record)
                if record["phase"] == "prepared":
                    stage = self._directory(identifier) / "scene"
                    if content_hash(stage) != record["sceneUploadHash"]:
                        raise PlanError("publication_scene_changed", "Prepared Scene bytes differ")
                    result = self.importer.import_staged(stage, allowed_root=self.root)
                    record["scene"] = {"id": result["sceneVersion"], "contentHash": result["contentHash"]}
                    self._scene(record)
                    version = record["intent"]["versionFields"] | {
                        "controller": record["newCurrent"]["id"],
                        "version": record["controllerVersion"],
                        "scene": record["scene"]["id"],
                    }
                    version = ControllerVersion.model_validate(version).model_dump()
                    record["controller"] = {
                        "document": version,
                        "contentHash": sha256(_json_bytes(version)).hexdigest(),
                    }
                    record["phase"] = "scene_published"
                    self._save(record)
                    self.after_effect("scene_published", record)
                if record["phase"] == "scene_published":
                    self._check(record)
                    with self.assets._lock, import_lock(self.library):
                        self._check(record)
                        path = self._version(record)
                        if path.exists():
                            if sha256(path.read_bytes()).hexdigest() != record["controller"]["contentHash"]:
                                raise PlanError(
                                    "publication_controller_changed", "Reserved version is foreign"
                                )
                        else:
                            durable_json(path, record["controller"]["document"])
                    record["phase"] = "controller_version_written"
                    self._save(record)
                    self.after_effect("controller_version_written", record)
                if record["phase"] == "controller_version_written":
                    with self.commit_context(record), self.assets._lock, import_lock(self.library):
                        self._check(record)
                        self._scene(record)
                        actual_hash = sha256(self._version(record).read_bytes()).hexdigest()
                        if actual_hash != record["controller"]["contentHash"]:
                            raise PlanError(
                                "publication_controller_changed", "Controller changed before commit"
                            )
                        path = self._version(record).with_name("controller.yaml")
                        durable_json(path, record["newCurrent"])
                        self.after_effect("current_written", record)
                        record["phase"] = "committed"
                        self._save(record)
                return deepcopy(record)
            except (PlanError, LibraryError) as error:
                record.update(phase="blocked", error={"code": error.detail["code"]})
                return self._save(record)
