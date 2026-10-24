"""Recoverable archive-to-workspace transitions; callers supply frozen expectations."""

import json
import os
import re

from packages.library.common import LibraryError, content_hash
from packages.library.transactions import durable_json


def conflict():
    raise LibraryError("harness_transition_conflict", "Harness changed; preserve the editor's work", 409)


def workspace_hash(path):
    from packages.library.agents import harness_files

    harness_files(path)
    return content_hash(path)


def finish(agents, path, directory, intent):
    """Complete only this recorded swap; an unrecognised workspace never gets replaced."""
    from packages.library.agents import confined

    if intent["status"] == "committed":
        return intent  # A historical retry cannot overwrite a later editor/version.
    harness = confined(path, ("harness",))
    staged = confined(directory, ("staged",))
    previous = confined(directory, ("previous",))
    target = agents.version(path.name, intent["targetVersion"])
    if target["contentHash"] != intent["targetHash"]:
        conflict()
    metadata = agents._metadata(path)
    if metadata not in (intent["beforeMetadata"], intent["targetMetadata"]):
        conflict()
    current_hash = workspace_hash(harness) if harness.exists() else None
    if current_hash != intent["targetHash"]:
        if current_hash is not None:
            if current_hash != intent["expectedHash"] or previous.exists():
                conflict()
            # Recheck immediately before the destructive publication point.
            if workspace_hash(harness) != intent["expectedHash"]:
                conflict()
            os.rename(harness, previous)
            if workspace_hash(previous) != intent["expectedHash"]:
                os.rename(previous, harness)
                conflict()
        elif not previous.exists() or workspace_hash(previous) != intent["expectedHash"]:
            conflict()
        if not staged.exists() or workspace_hash(staged) != intent["targetHash"]:
            conflict()
        os.rename(staged, harness)
    if workspace_hash(harness) != intent["targetHash"]:
        conflict()
    durable_json(path / "agent.yaml", intent["targetMetadata"])
    if intent["outcomeVersion"] is not None:
        version = agents.version(path.name, intent["outcomeVersion"])
        durable_json(path / f"versions/v{version['version']}.json", version | {"decision": intent["outcome"]})
    receipt = intent | {"status": "committed"}
    durable_json(directory / "intent.json", receipt)
    return receipt


def recover_path(agents, path):
    from packages.library.agents import confined

    results = []
    for file in sorted(confined(path, ("transitions",)).glob("*/intent.json")):
        directory = confined(path, ("transitions", file.parent.name))
        if file.is_symlink() or file.is_junction() or file.stat().st_nlink != 1:
            conflict()
        intent = json.loads(file.read_bytes())
        if intent["status"] != "committed":
            finish(agents, path, directory, intent)
            results.append({"agentId": "agent/" + path.name, "requestId": intent["requestId"],
                            "status": "committed"})
    return results


def transition(agents, identifier, *, target_version, expected_version, expected_hash, request_id,
               outcome_version=None, outcome="rejected", advance_reference=False):
    from packages.library.agents import confined, unpack_harness
    from packages.library.archive import import_lock

    if (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)
            or outcome not in ("accepted", "rejected")
            or not isinstance(expected_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)):
        raise LibraryError("invalid_harness_transition", "Invalid Harness transition", 422)
    with import_lock(agents.user_root):
        path = agents._writable(identifier)
        directory = confined(path, ("transitions", request_id), create=True)
        file = directory / "intent.json"
        requested = {"requestId": request_id, "targetVersion": target_version,
                     "expectedVersion": expected_version, "expectedHash": expected_hash,
                     "outcomeVersion": outcome_version, "outcome": outcome,
                     "advanceReference": advance_reference}
        try:
            if file.exists():
                if file.is_symlink() or file.is_junction() or file.stat().st_nlink != 1:
                    conflict()
                intent = json.loads(file.read_bytes())
                if any(intent.get(key) != value for key, value in requested.items()):
                    conflict()
                return finish(agents, path, directory, intent)
            recover_path(agents, path)
            metadata = agents._metadata(path)
            if metadata["currentVersion"] != expected_version:
                conflict()
            if workspace_hash(confined(path, ("harness",))) != expected_hash:
                conflict()
            target, archive = agents._read_version(identifier, target_version)
            if outcome_version is not None:
                agents.version(identifier, outcome_version)
            staged = confined(directory, ("staged",), create=True)
            if not any(staged.iterdir()):
                unpack_harness(archive, staged)
            if workspace_hash(staged) != target["contentHash"]:
                conflict()
            after = metadata | {"currentVersion": target_version}
            if advance_reference:
                after["referenceVersion"] = target_version
            intent = requested | {"status": "prepared", "targetHash": target["contentHash"],
                                  "beforeMetadata": metadata, "targetMetadata": after}
            durable_json(file, intent)
            return finish(agents, path, directory, intent)
        except OSError as error:
            raise LibraryError(
                "harness_transition_unavailable", "Harness transition awaits recovery", 503
            ) from error


def recover(agents):
    from packages.library.agents import confined
    from packages.library.archive import import_lock

    results = []
    with import_lock(agents.user_root):
        for metadata in sorted(confined(agents.user_root, ("agents",)).glob("*/agent.yaml")):
            path = confined(agents.user_root, ("agents", metadata.parent.name))
            try:
                results.extend(recover_path(agents, path))
            except (LibraryError, OSError, ValueError, KeyError):
                for file in confined(path, ("transitions",)).glob("*/intent.json"):
                    results.append({"agentId": "agent/" + path.name, "requestId": file.parent.name,
                                    "status": "blocked"})
    return results
