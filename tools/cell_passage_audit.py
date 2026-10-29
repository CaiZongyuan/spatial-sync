"""Read-only Cell Passage evidence checks; recording validity is not experiment success."""

import hashlib
import json
import math
from itertools import product

from packages.info.log import read_segment, segment_metadata
from packages.info.recording import recording_metadata
from packages.info.store import StateStore
from packages.protocol.math import compose_pose, normalize_quaternion, rotate_vector
from packages.protocol.schema import EntityObservations, EntitySnapshot
from packages.transport import FrameCodec
from tools.cell_passage_recipe import CLIENT_CONTRACT


def _body_poses(manifest, frame):
    nodes = {row["entityKey"]: row["streamId"] for row in manifest["nodes"] if row["kind"] == "body"}
    values = {row[0]: {"p": row[1:4], "q": row[4:8]} for row in frame["poses"]}
    if len(values) != len(frame["poses"]):
        raise ValueError("Spatial sample has duplicate stream identities")
    for pose in values.values():
        if len(pose["p"]) != 3 or not all(math.isfinite(value) for value in pose["p"]):
            raise ValueError("Spatial sample contains unknown/nonfinite body positions")
        normalize_quaternion(pose["q"])
    return {key: values[stream] for key, stream in nodes.items()}


def audit_spatial_state(manifest, before, after, expected_sites):
    """Cross-check actual body poses against actual Sites over a positive simTime interval.

    This uses the existing labware seating gates, without axis conversion or
    rewriting source facts. It validates an end state, not transport collisions.
    """
    try:
        if (not expected_sites or any(
            before[name] != after[name] for name in ("sessionId", "sceneRevision", "epoch")
        )):
            raise ValueError("Spatial samples must identify one nonempty episode")
        if any(before[name] != manifest[name] for name in ("sessionId", "sceneRevision")):
            raise ValueError("Spatial samples do not belong to the supplied Manifest")
        dt = after["simTime"] - before["simTime"]
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("Spatial settling needs two distinct simulation times")
        sites = {row["entityKey"]: row for row in manifest["sites"]}
        previous, current = _body_poses(manifest, before), _body_poses(manifest, after)

        def site_pose(key, body_poses):
            value = sites[key]
            local = value["localPose"]
            if value.get("parentBodyKey"):
                return compose_pose(body_poses[value["parentBodyKey"]], local)
            return local

        tolerance = CLIENT_CONTRACT["mechanicalTolerances"]
        rows, errors = [], []
        for body, site in expected_sites.items():
            desired, previous_desired = site_pose(site, current), site_pose(site, previous)
            actual, previous_actual = current[body], previous[body]
            position = math.dist(actual["p"], desired["p"])
            a, b = normalize_quaternion(actual["q"]), normalize_quaternion(desired["q"])
            angle = math.degrees(2 * math.acos(min(1.0, abs(sum(x * y for x, y in zip(a, b, strict=True))))))
            relative = [(actual["p"][i] - previous_actual["p"][i]
                         - desired["p"][i] + previous_desired["p"][i]) / dt for i in range(3)]
            speed = math.hypot(*relative)
            if not all(math.isfinite(value) for value in (position, angle, speed)):
                raise ValueError("Spatial sample contains nonfinite physical observations")
            valid = (position < tolerance["positionM"] and angle <= tolerance["orientationDegrees"]
                     and speed < tolerance["relativeSpeedMps"])
            rows.append({"body": body, "site": site, "positionErrorM": position,
                         "orientationErrorDegrees": angle, "relativeSpeedMps": speed, "valid": valid})
            if not valid:
                errors.append("Body is not aligned and settled: " + body)
        return {"status": "invalid" if errors else "spatial_state_valid", "errors": errors, "bodies": rows,
                "nativeSource": manifest["source"]["kind"] == "newton", "experimentQualified": False}
    except (ValueError, KeyError, TypeError) as error:
        return {"status": "invalid", "error": str(error), "experimentQualified": False}


def audit_tip_destinations(manifest, frame, layout, material):
    """Check real discarded-tip bounds in the actual waste-body frame, not just state labels."""
    try:
        if any(frame[name] != manifest[name] for name in ("sessionId", "sceneRevision")):
            raise ValueError("Tip poses do not belong to the supplied Manifest")
        poses = _body_poses(manifest, frame)
        region, geometry = layout["solidWasteRegion"], layout["tipGeometry"]
        allowance = region["contactAllowanceM"]
        if not math.isfinite(allowance) or allowance < 0:
            raise ValueError("An authored finite contact allowance is required")
        lower, upper = region["lowerLocal"], region["upperLocal"]
        tip_lower, tip_upper = geometry["lowerLocal"], geometry["upperLocal"]
        for lo, hi in ((lower, upper), (tip_lower, tip_upper)):
            if (len(lo) != 3 or len(hi) != 3 or not all(math.isfinite(value) for value in [*lo, *hi])
                    or any(a >= b for a, b in zip(lo, hi, strict=True))):
                raise ValueError("Actual authored geometry bounds are required")
        waste = poses[region["bodyKey"]]
        quaternion = normalize_quaternion(waste["q"])
        inverse = [-quaternion[0], -quaternion[1], -quaternion[2], quaternion[3]]
        tips = {row["id"]: row for row in layout["tips"]}
        states = {row["id"]: row["state"] for row in material["tips"]}
        if material["tip"]["id"] or tips.keys() != states.keys() or len(states) != len(material["tips"]):
            raise ValueError("Unknown, duplicate or still-installed tip inventory")
        checked, errors = [], []
        corners = list(product(*zip(tip_lower, tip_upper, strict=True)))
        for identity, state in states.items():
            if state == "unused":
                continue
            if state != "discarded":
                raise ValueError("Unfinished or unknown tip state: " + identity)
            pose = poses[tips[identity]["bodyKey"]]
            valid = True
            for corner in corners:
                world = compose_pose(pose, {"p": corner, "q": [0, 0, 0, 1]})["p"]
                local = rotate_vector(inverse, [world[i] - waste["p"][i] for i in range(3)])
                if any(value < lower[i] - allowance or value > upper[i] + allowance
                       for i, value in enumerate(local)):
                    valid = False
            checked.append({"id": identity, "body": tips[identity]["bodyKey"], "contained": valid})
            if not valid:
                errors.append("Discarded tip protrudes from the actual waste region: " + identity)
        return {"status": "invalid" if errors else "tip_destinations_valid", "errors": errors,
                "discardedTips": checked, "unusedTips": sum(state == "unused" for state in states.values()),
                "contactAllowanceM": allowance, "experimentQualified": False}
    except (ValueError, KeyError, TypeError) as error:
        return {"status": "invalid", "error": str(error), "experimentQualified": False}


def audit_recording(metadata, files, target, plan_ids):
    """Validate closed public recording bytes and retained completion of every part.

    ``files`` maps the metadata's segment names to bytes obtained through the
    existing recording endpoint or an owned local recording directory. This
    reader never edits a recording or treats it as an executable checkpoint.
    """
    try:
        metadata = recording_metadata(metadata)
        revision = target["sceneRevision"]
        if (metadata["status"] != "complete" or metadata["kind"] != "session"
                or metadata["sessionId"] != target["sessionId"]
                or set(metadata["revisions"]) != {revision} or not metadata["segments"]):
            raise ValueError("A closed, single-scene recording for this exact session is required")
        reference = metadata["revisions"][revision]
        codec = FrameCodec(metadata["sessionId"], revision, reference["revisionNumericId"])
        store = StateStore(revision)
        epochs, spatial_epochs, seen = set(), set(), set()
        plans, operation_ids = {}, set()
        frames, total_bytes = 0, 0
        digest = hashlib.sha256()
        for segment in metadata["segments"]:
            name = segment["file"]
            if name in seen or name not in files:
                raise ValueError("Missing or repeated recording segment")
            seen.add(name)
            raw = files[name]
            parsed = read_segment(raw)
            if parsed.truncated or len(raw) != segment["bytes"]:
                raise ValueError("Recording segment is truncated or differs from its metadata")
            checked = segment_metadata(name, parsed, revision)
            if checked["sceneRevision"] != revision or segment["sceneRevision"] != revision:
                raise ValueError("Recording crosses scene revisions")
            digest.update(name.encode("utf8") + b"\x00" + raw)
            total_bytes += len(raw)
            for row in parsed.records:
                if row.kind == 3:
                    raise ValueError("A recording gap cannot prove a continuous passage")
                if row.kind == 1:
                    spatial_epochs.add(codec.decode(row.payload).epoch)
                    frames += 1
                    continue
                value = json.loads(row.payload)
                if "epoch" in value:
                    epochs.add(value["epoch"])
                if "sceneRevision" in value and value["sceneRevision"] != revision:
                    raise ValueError("Recorded information belongs to a different scene")
                if value.get("type") in ("entity.snapshot", "entity.observations"):
                    model = EntitySnapshot if value["type"] == "entity.snapshot" else EntityObservations
                    if not store.apply(model.model_validate(value)):
                        raise ValueError("Recorded observations are inconsistent")
                if value.get("type") == "plan.update" and value.get("update") == "full":
                    plan = value["plan"]
                    if plan["id"] in plan_ids:
                        plans[plan["id"]] = plan
                if value.get("type") == "entity.event" and value.get("event") == "operationCompleted":
                    operation_ids.add(value["data"]["receipt"]["operationId"])
        if epochs != {target["epoch"]} or spatial_epochs != {target["epoch"]} or not frames:
            raise ValueError("Recording contains an absent, reset or different episode")
        if any(identity not in plans or plans[identity]["status"] != "completed"
               or any(step["status"] not in ("done", "skipped") for step in plans[identity]["steps"])
               for identity in plan_ids):
            raise ValueError("Recording does not retain completion of every expected Plan part")
        return {"status": "recording_valid", "segments": len(seen), "frames": frames,
                "bytes": total_bytes, "segmentDigest": digest.hexdigest(),
                "completedPlans": len(plans), "recordedOperationReceipts": len(operation_ids),
                "experimentQualified": False}
    except (ValueError, KeyError, TypeError) as error:
        return {"status": "invalid", "error": str(error), "experimentQualified": False}
