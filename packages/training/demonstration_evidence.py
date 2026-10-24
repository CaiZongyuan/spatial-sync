"""Read existing recording bytes to establish one successful Task episode."""

import hashlib
import json
from copy import deepcopy

from packages.info.conditions import condition_state
from packages.info.log import read_segment, segment_metadata
from packages.info.plans import PlanError
from packages.info.recording import recording_metadata
from packages.info.store import StateStore
from packages.library.archive import package_files, relative_path
from packages.library.common import content_hash
from packages.protocol.schema import EntityObservations, EntitySnapshot, InvocationUpdate


def _reject(code, message):
    raise PlanError(code, message, status=422)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def recording_evidence(directory, task):
    """Completion is storage status; success comes from recorded Task observations."""
    try:
        files = set(package_files(directory))
        digest = content_hash(directory)
        metadata = recording_metadata(json.loads((directory / "recording.json").read_bytes()))
        definition = task["task"]
        if metadata.get("kind") != "session" or metadata.get("status") != "complete":
            _reject("demo_recording_incomplete", "A complete simulation recording is required")
        if (
            metadata.get("sceneVersion") != definition["scene"]
            or metadata.get("sceneContentHash") is None
            or metadata["sceneContentHash"] != task["sceneContentHash"]
        ):
            _reject("demo_scene_mismatch", "Recording and Task need the same known immutable scene")
        segments = metadata["segments"]
        revisions = {segment.get("sceneRevision", metadata.get("sceneRevision")) for segment in segments}
        if not segments or len(revisions) != 1 or None in revisions:
            _reject("demo_episode_ambiguous", "Select a recording with one complete episode revision")
        revision = next(iter(revisions))
        reference = metadata.get("revisions", {}).get(revision, {}).get("catalog", "catalog.json")
        catalog_name = relative_path(reference)
        catalog = json.loads((directory / catalog_name).read_bytes())
        binding = catalog["entities"][definition["robot"]]
        interface, action = definition["implements"].rsplit(".", 1)
        if (
            catalog.get("sceneRevision") != revision or binding.get("type") != interface
            or not isinstance(binding.get("program"), str) or not binding["program"]
        ):
            _reject("demo_program_unknown", "Recorded Catalog does not identify the Task's Device Program")
        store = StateStore(revision)
        invocations, epochs, seen = {}, set(), set()
        for segment in segments:
            name = relative_path(segment["file"]).as_posix()
            if name not in files or name in seen:
                _reject("demo_recording_invalid", "Recording segments are missing or repeated")
            seen.add(name)
            raw = (directory / name).read_bytes()
            result = read_segment(raw)
            if result.truncated or segment["bytes"] != len(raw):
                _reject("demo_recording_incomplete", "A recording segment is truncated or changed")
            segment_metadata(name, result, revision)
            for record in result.records:
                if record.kind == 3:
                    _reject("demo_observation_gap", "A recording gap cannot prove a successful episode")
                if record.kind != 2:
                    continue
                value = json.loads(record.payload)
                if value.get("type") in ("entity.snapshot", "entity.observations"):
                    model = EntitySnapshot if value["type"] == "entity.snapshot" else EntityObservations
                    message = model.model_validate(value)
                    epochs.add(message.epoch)
                    if message.sceneRevision != revision or not store.apply(message):
                        _reject("demo_recording_invalid", "Recorded observations are inconsistent")
                    if isinstance(message, EntitySnapshot):
                        invocations.update({item.invocationId: item.model_dump(mode="json")
                                            for item in message.invocations})
                elif value.get("type") == "invocation.update":
                    invocation = InvocationUpdate.model_validate(value)
                    if invocation.sceneRevision != revision:
                        _reject("demo_recording_invalid", "Recorded invocation belongs to another revision")
                    invocations[invocation.invocationId] = invocation.model_dump(mode="json")
        if len(epochs) != 1:
            _reject("demo_episode_ambiguous", "Reset or absent observations need separate episode recordings")
        matches = [item for item in invocations.values()
                   if item["entityKey"] == definition["robot"] and item["action"] == action]
        if len(matches) != 1:
            _reject("demo_episode_ambiguous", "One invocation must identify the Task episode and params")
        invocation = matches[0]
        if invocation["status"] != "succeeded":
            _reject("demo_action_unsuccessful", "The recorded Task action did not succeed")
        success = condition_state(
            [definition["success"]], definition["robot"], store.values(), store.metadata()
        )
        if success is not True:
            _reject("demo_success_unknown" if success is None else "demo_not_successful",
                    "Recorded observations do not establish Task success")
        if content_hash(directory) != digest:
            _reject("demo_recording_changed", "Recording changed while establishing success")
        return {
            "recordingId": metadata["sessionId"],
            "recordingContentHash": digest,
            "task": task["id"],
            "taskContentHash": task["contentHash"],
            "scene": definition["scene"],
            "sceneContentHash": task["sceneContentHash"],
            "robot": definition["robot"],
            "implements": definition["implements"],
            "params": deepcopy(invocation["params"]),
            "invocationId": invocation["invocationId"],
            "sourceProgram": {
                "id": binding["program"],
                "bindingHash": _digest(binding),
                "implementationHash": None,
                "implementationCoverage": "not_recorded",
            },
            "successEvidence": {
                "kind": "recorded_task_condition",
                "conditionHash": _digest(definition["success"]),
                "sceneRevision": revision,
                "epoch": next(iter(epochs)),
            },
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise PlanError(
            "demo_recording_invalid", "Required recording evidence is missing or invalid", status=422
        ) from error
