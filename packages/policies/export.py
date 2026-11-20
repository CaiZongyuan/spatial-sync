"""Freeze a portable single-Policy inference archive from verified library bytes."""

import hashlib
import io
import json
import math
import zipfile
from pathlib import Path

import onnx

from packages.info.plans import PlanError
from packages.library.archive import relative_path
from packages.library.common import BUILTIN_ROOT, REPO_ROOT, LibraryError, content_hash, document
from packages.library.structural import USDGraph
from packages.policies.calibration import JointCalibration
from packages.policies.library import digest, read_json
from packages.policies.observations import ObservationSpec


def _signature(value):
    tensor = value.type.tensor_type
    return {
        "name": value.name,
        "dtype": onnx.TensorProto.DataType.Name(tensor.elem_type),
        "shape": [dim.dim_value for dim in tensor.shape.dim],
    }


def _physical(manager, scene, task, action):
    from apps.node.usd_mjcf import derive_usd_mjcf

    channels = [
        {
            "jointKey": key,
            "unit": unit,
            "physicalLimits": [None, None],
            "kp": None,
            "kv": None,
            "forceLimit": None,
            "armature": None,
        }
        for key, unit in zip(action.jointKeys, action.units, strict=True)
    ]
    stage_name = scene.metadata["stage"]
    if stage_name.startswith("python:"):
        return channels, None, None
    payload = scene.runtime_payload()
    graph = USDGraph(payload["package"], payload["assets"], payload["expectedAssets"])
    stage = graph.compose(stage_name)
    if stage is None or graph.errors:
        raise ValueError("Policy Scene USD dependency composition differs")
    derived = derive_usd_mjcf(
        stage, prim_path=task["robot"], include_ground_plane=False, include_static_colliders=False
    )
    joints = {joint.prim_path: joint for joint in derived.joints}
    for channel in channels:
        joint = joints[channel["jointKey"]]
        channel.update(
            kp=joint.stiffness,
            kv=joint.damping,
            forceLimit=joint.max_force,
            armature=joint.armature,
            physicalLimits=[joint.lower, joint.upper] if joint.limited else [None, None],
        )
    from packages.sim_runtime.usd_settings import authored_settings

    dt, _ = authored_settings(stage)
    dt = (scene.metadata.get("simulation") or {}).get("physicsDt", dt)
    return channels, dt, _robot_asset(scene, task["robot"], stage, graph)


def _robot_asset(scene, entity, stage, graph):
    target = stage.GetPrimAtPath(entity)
    authored = next((item for item in scene.bindings["entities"] if item.get("key") == entity), {})
    template = authored.get("template")
    referenced = []
    # A direct USD reference must identify the robot actually used by this target.
    for snapshot in scene.runtime_assets.values():
        if not snapshot["id"].startswith("robot/") or (template and template != snapshot["id"]):
            continue
        layer = graph.layer(snapshot, snapshot["metadata"]["stage"])
        if any(
            spec.path.pathString == "/Asset" and spec.layer.identifier == layer.identifier
            for spec in target.GetPrimStack()
        ):
            referenced.append(snapshot)
    return referenced[0] if len(referenced) == 1 else None


def _retained_source_matches(directory, record):
    if "source" in record:
        source = directory / record["source"]
        return source.is_file() and digest(source) == record["sourceSha256"]
    # The frozen package attests to the original bytes under its historical path.
    provenance_path = directory / "provenance.json"
    if not provenance_path.is_file():
        return False
    provenance = read_json(provenance_path)
    entrypoint = record["assetSourceEntrypoint"]
    return (
        provenance.get("sourceEntrypoint") == entrypoint
        and provenance.get("sourceFiles", {}).get(entrypoint, {}).get("sha256") == record["sourceSha256"]
    )


def _archive_retained_source(path, record, files):
    licenses = []
    for license_entry in record["sourceLicenses"]:
        name = relative_path(license_entry["document"])
        data = (path.with_suffix("") / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != license_entry["sha256"]:
            raise ValueError("Retained source license document changed")
        document_path = f"provenance/{path.stem}/source/{name.as_posix()}"
        files[document_path] = data
        licenses.append(license_entry | {"document": document_path})
    return {
        "repository": record["sourceRepository"],
        "revision": record["sourceRevision"],
        "entrypoint": record["sourceEntrypoint"],
        "sha256": record["sourceSha256"],
        "licenses": licenses,
    }


def _retained_calibration(manager, scene, asset, task):
    root = BUILTIN_ROOT / "retained-calibrations"
    for path in sorted(root.glob("*.json")):
        record = read_json(path)
        if "sceneContentHash" in record and scene.content_hash != record["sceneContentHash"]:
            continue
        builder = record.get("legacyBuilder")
        namespace = ""
        if (builder and scene.metadata["stage"] == "python:" + builder["id"]
                and task["robot"] == builder["entity"]):
            if any(
                not (REPO_ROOT / file).is_file() or digest(REPO_ROOT / file) != sha
                for file, sha in builder["files"].items()
            ):
                continue
            version = manager.library_assets.versions.get(record["asset"])
            if version is None:
                continue
            asset = {
                "id": version.id, "path": str(version.path), "contentHash": version.content_hash,
                "metadata": version.metadata,
            }
            namespace = builder["jointNamespace"]
        if not asset or (asset["id"], asset["contentHash"]) != (record["asset"], record["assetContentHash"]):
            continue
        directory = Path(asset["path"])
        if content_hash(directory) != record["assetContentHash"]:
            continue
        if not _retained_source_matches(directory, record):
            continue
        return asset, record, path, namespace
    return None


def _source_calibration(manager, scene, asset, task, action, files):
    unavailable = {"status": "unavailable", "joints": [], "baseTransform": None}
    explicit = asset and "joint-calibration.json" in asset["files"]
    record = {}
    if explicit:
        path = Path(asset["files"]["joint-calibration.json"])
        calibration = JointCalibration.model_validate_json(path.read_bytes())
        namespace = ""
    else:
        retained = _retained_calibration(manager, scene, asset, task)
        if retained is None:
            return unavailable | {"reason": "No frozen source-joint calibration was recorded"}, None
        asset, record, path, namespace = retained
        calibration = JointCalibration.model_validate(record["mapping"])
    joints = {row.jointKey: row for row in calibration.joints}
    prefix = task["robot"] + "/" + namespace
    keys = ["/Asset/" + key.removeprefix(prefix) for key in action.jointKeys]
    if (any(not key.startswith(prefix) for key in action.jointKeys)
            or not set(keys) <= set(joints)
            or [joints[key].unit for key in keys] != action.units):
        if explicit:
            raise ValueError("Source joint calibration differs from the Policy action order or units")
        return unavailable | {"reason": "Policy channels do not match the retained calibration"}, None
    mapping_path = "source-calibration.json"
    mapping_bytes = path.read_bytes()
    files[mapping_path] = mapping_bytes
    if "sourceLicenses" in record:
        asset = asset | {"retainedSource": _archive_retained_source(path, record, files)}
    return {
        "status": "verified",
        "asset": asset["id"],
        "assetContentHash": asset["contentHash"],
        "mappingPath": mapping_path,
        "mappingSha256": hashlib.sha256(mapping_bytes).hexdigest(),
        "baseTransform": calibration.baseTransform,
        "joints": [joints[key].model_dump(mode="json") for key in keys],
        "equation": "canonical = sign * source + zeroOffset",
    }, asset


def _provenance(manager, policy, scene, motion, files, calibration_asset):
    task_record = manager.tasks.show(policy["task"])
    if task_record["contentHash"] != policy["taskContentHash"]:
        raise ValueError("Export Task bytes differ from the frozen Policy")
    task_path = manager.tasks._versions()[policy["task"]] / "package"
    assets = scene.runtime_assets | scene.template_assets
    if calibration_asset:
        assets.setdefault(calibration_asset["id"], calibration_asset)
    sources = [
        (policy["task"], task_record["contentHash"], task_path, None),
        (scene.id, scene.content_hash, scene.path, None),
        *(
            (item["id"], item["contentHash"], Path(item["path"]), item["metadata"])
            for item in assets.values()
        ),
    ]
    licenses = []
    for index, (identity, sha, path, metadata) in enumerate(sources):
        if content_hash(path) != sha:
            raise ValueError(f"Export provenance changed for {identity}")
        declarations = []
        for filename in sorted(path.iterdir()):
            if (
                filename.name.casefold()
                in {"license", "license.txt", "license.md", "notice", "provenance.json"}
                and filename.is_file()
            ):
                if filename.stat().st_size > 1024 * 1024:
                    raise ValueError("License or provenance document exceeds export budget")
                archive_name = f"provenance/{index}/{filename.name}"
                files[archive_name] = filename.read_bytes()
                declarations.append(archive_name)
        entry = {"id": identity, "contentHash": sha, "documents": declarations}
        if metadata is not None:
            entry["assetKind"] = metadata.get("kind")
        if calibration_asset and identity == calibration_asset["id"]:
            source = calibration_asset.get("retainedSource")
            if source:
                entry["source"] = source
                declarations.extend(license_entry["document"] for license_entry in source["licenses"])
        licenses.append(entry)
    if motion:
        licenses.append({"id": "reference-motion.json", "license": motion["license"]})
    return licenses


def policy_export(manager, identifier):
    policy, package = manager.policies.resolve(identifier)
    try:
        task = read_json(package / "task.json")
        scene = manager.library.resolve(policy["scene"])
        if scene.content_hash != policy["sceneContentHash"]:
            raise ValueError("Policy Scene bytes changed")
        scene.entity_type_snapshots()
        obs = document(package / "obs_spec.yaml")
        spec = ObservationSpec.model_validate(obs)
        model_path = package / policy["onnx"]
        model = onnx.load_model(str(model_path), load_external_data=False)
        job_export = read_json(package / "artifacts/export.json")
        model_relative = model_path.relative_to(package / "artifacts")
        model_name = f"model/{model_relative.as_posix()}"
        files = {model_name: model_path.read_bytes()}
        for entry in job_export["externalData"]:
            name = relative_path(entry["path"]).as_posix()
            path = package / "artifacts" / name
            if digest(path) != entry["sha256"]:
                raise ValueError("Frozen external ONNX tensor data changed")
            files[f"model/{name}"] = path.read_bytes()
        if digest(model_path) != policy["onnxSha256"]:
            raise ValueError("Frozen model changed")
        channels, authored_dt, asset = _physical(manager, scene, task, spec.action)
        calibration, calibration_asset = _source_calibration(manager, scene, asset, task, spec.action, files)
        provenance = _provenance(
            manager, policy, scene, task.get("referenceMotion"), files, calibration_asset
        )
        try:
            qualification = manager.policy_evidence.qualification(identifier)
            evidence = {"status": "verified", "scope": qualification["scope"]}
        except (PlanError, LibraryError):
            evidence = {"status": "unverified"}

        rows = getattr(getattr(manager.evaluations, "store", None), "rows", ())
        evaluations = [
            {key: row.get(key) for key in ("evaluationId", "successes", "episodes", "decision")}
            for row in rows
            if row.get("split") == "train" and (row.get("candidateIdentity") or {}).get("id") == identifier
        ]
        backend = task.get("mjlab") or {}
        dt = backend.get("physics_dt", authored_dt)
        hz = task["control"]["hz"]
        ratio = 1 / (hz * dt) if dt else None
        decimation = backend.get("decimation")
        if decimation is None and ratio and math.isclose(ratio, round(ratio), abs_tol=1e-6):
            decimation = round(ratio)
        manifest = {
            "schema": "lw.policy-export/1",
            "policy": identifier,
            "policyContentHash": policy["contentHash"],
            "model": {
                "path": model_name,
                "sha256": policy["onnxSha256"],
                "input": _signature(model.graph.input[0]),
                "output": _signature(model.graph.output[0]),
                "opsets": {item.domain: item.version for item in model.opset_import},
                "externalData": job_export["externalData"],
            },
            "observation": spec.model_dump(mode="json", by_alias=True, exclude_none=True),
            "normalization": (
                "inside-exported-graph" if evidence["status"] == "verified" else "as-frozen-in-model"
            ),
            "coordinateSystem": {"up": "Z", "quaternion": "xyzw", "gravityWorld": [0, 0, -1]},
            "previousAction": {"initial": "zeros", "reset": "invocation-or-episode", "step": "raw-output"},
            "actionOrder": [
                "scale",
                "offset-or-delta",
                "maxDelta-from-measured",
                "declared-limits",
                "physical-limits",
                "source-joint-transform",
            ],
            "control": {
                "hz": task["control"]["hz"],
                "physicsDt": dt,
                "decimation": decimation,
            },
            "actuators": channels,
            "sourceCalibration": calibration,
            "versions": policy["versions"],
            "qualification": evidence,
            "evaluations": evaluations,
            "provenance": provenance,
            "phase": {
                "clock": "simulation-seconds-since-start",
                "loopOrder": ["sin", "cos"],
                "oneShot": "seconds-remaining-hold-last",
                "orientationInterpolation": "shortest-arc-slerp",
            },
        }
        if task.get("referenceMotion"):
            manifest["referenceMotion"] = "reference-motion.json"
            files["reference-motion.json"] = json.dumps(task["referenceMotion"], allow_nan=False).encode()
        for name, source in {
            "infer.py": "export_runner.py", "motion.py": "motion.py",
            "action_contract.py": "action_contract.py",
        }.items():
            files[name] = Path(__file__).with_name(source).read_bytes()
        files["manifest.json"] = json.dumps(manifest, sort_keys=True, allow_nan=False).encode()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                archive.writestr(zipfile.ZipInfo(name), data, compress_type=zipfile.ZIP_DEFLATED)
        return output.getvalue()
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise LibraryError(
            "policy_export_unavailable", "Policy export inputs are invalid or unavailable", 409
        ) from error
