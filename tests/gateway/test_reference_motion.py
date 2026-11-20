import copy
import hashlib
import io
import json
import tarfile
import zipfile

import pytest
from fastapi.testclient import TestClient
from onnx import TensorProto, helper

from apps.api.app.main import create_app
from apps.node.mjlab_launcher import compiler_hash
from packages.library.common import BUILTIN_ROOT, document
from packages.motion.snapshot import declarative_files
from tests.fixtures.training_task import training_plan
from tests.gateway.test_remote_training_nodes import (
    MJLAB_VERSIONS,
    OPERATOR,
    VIEWER,
    beat,
    register,
    remote_settings,
)

URL = "/api/v1/library/reference-motions"
ROBOT = "/World/Lab/Microduck"
BOW = BUILTIN_ROOT / "tasks/microduck-bow/1/package"
MAPPING = {
    "pelvis": "/Asset/trunk_base",
    "trunk": "/Asset/trunk_base",
    "head": "/Asset/jaw_soft",
    "leftFoot": "/Asset/ankle_left/left_foot",
    "rightFoot": "/Asset/ankle_right/right_foot",
}


def imitation_request(reference):
    task = document(BOW / "task.yaml")
    task.update(name="microduck-imitate", title="Imitation test")
    del task["episode"]["commandEnvelope"]
    task["declarative"] = {
        "baseBody": ROBOT + "/trunk_base",
        "episodeSeconds": 8.0,
        "rewards": [
            {"kind": kind, "weight": 1.0}
            for kind in (
                "joint_tracking",
                "root_pose_tracking",
                "root_velocity_tracking",
                "body_local_tracking",
            )
        ],
    }
    task["loopBudget"]["episodes"] = 60
    task["completion"] = {"mode": "phase_clip", "minHeight": 0.09}
    observation = document(BOW / "obs_spec.yaml")
    observation["schema"] = "lw.obs-spec/2"
    observation["observations"] = [
        row for row in observation["observations"] if row["source"] != "command"
    ] + [
        {
            "name": source,
            "source": source,
            "unit": "1",
            "frame": "world",
            "shape": [width],
            "offset": [0.0] * width,
            "scale": [1.0] * width,
        }
        for source, width in (
            ("motion_phase", 1),
            ("reference_motion_frame", 21),
        )
    ]
    return {
        "reference": {key: reference[key] for key in ("id", "sha256")},
        "task": task,
        "observation": observation,
    }


def keyframes(assets):
    from apps.node.usd_mjcf import derive_usd_mjcf

    asset = assets.resolve("robot/microduck@1")
    model = derive_usd_mjcf(
        asset.path / asset.metadata["stage"], prim_path="/Asset", include_ground_plane=True
    )
    joints = [joint for joint in model.joints if joint.actuated]
    pose = {
        "position": list(model.root_pos),
        "orientation": [*model.root_quat[1:], model.root_quat[0]],
        "joints": [joint.default_position for joint in joints],
    }
    motion = {
        "asset": asset.id,
        "assetContentHash": asset.content_hash,
        "jointKeys": [joint.prim_path for joint in joints],
        "loop": False,
        "license": "CC0-1.0",
        "frames": [pose | {"time": 0.0}, pose | {"time": 0.2}],
    }
    return {
        "asset": asset.id,
        "assetContentHash": asset.content_hash,
        "mapping": MAPPING,
        "keyframes": motion,
        "keyframeLicense": {"license": "CC0-1.0", "attribution": "Fixture"},
    }


def test_reference_keyframes_task_and_exact_transfer(tmp_path, monkeypatch):
    settings = remote_settings()
    app = create_app(settings)
    with TestClient(app) as client:
        manager = app.state.manager
        request = keyframes(manager.library_assets)
        assert (client.post(URL, headers=VIEWER, json=request)).status_code == 403
        wrong = request | {"assetContentHash": "0" * 64}
        wrong["keyframes"] = request["keyframes"] | {"assetContentHash": "0" * 64}
        rejected = client.post(URL, headers=OPERATOR, json=wrong)
        assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "reference_asset_changed"
        invalid = keyframes(manager.library_assets)
        invalid["keyframes"]["frames"][1]["joints"] = [20.0] * 14
        response = client.post(URL, headers=OPERATOR, json=invalid)
        assert response.status_code == 422 and response.json()["detail"]["code"] == "reference_joint_speed"
        typo = copy.deepcopy(request)
        for frame in typo["keyframes"]["frames"]:
            frame["bodyLocal"] = {"/Asset/does-not-exist": [50.0, 0.0, 0.0]}
        rejected = client.post(URL, headers=OPERATOR, json=typo)
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "reference_keyframe_keypoint_invalid"

        yaw = copy.deepcopy(request)
        initial, final = yaw["keyframes"]["frames"]
        channel = yaw["keyframes"]["jointKeys"].index("/Asset/joints/left_hip_yaw")
        final["time"] = .5
        final["joints"] = initial["joints"].copy()
        final["joints"][channel] += .5
        rejected = client.post(URL, headers=OPERATOR, json=yaw)
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["maxFootSlipM"] >= .0228

        published = client.post(URL, headers=OPERATOR, json=request)
        assert published.status_code == 201, published.text
        reference = published.json()
        assert reference["source"]["keyframes"]["license"]["license"] == "CC0-1.0"
        assert reference["report"]["minSupportMarginM"] > 0.005
        assert reference["report"]["maxGroundGapM"] < 0.004
        assert reference["motion"]["frames"][-1]["time"] == pytest.approx(0.6)
        url = URL + "/" + reference["sha256"] + "/1"
        assert (client.get(url, headers=VIEWER)).json() == reference
        assert (client.get(url + "/preview", headers=VIEWER)).json()["frames"] == reference["motion"][
            "frames"
        ]

        body = imitation_request(reference)
        path = "/api/v1/tasks/reference-motion"
        assert (client.post(path, headers=VIEWER, json=body)).status_code == 403
        from pxr import Gf, UsdGeom

        from packages.library.structural import USDGraph

        compose = USDGraph.compose

        def displaced_site(graph, filename):
            stage = compose(graph, filename)
            point = stage.GetPrimAtPath(ROBOT + "/ankle_left/left_foot") if stage else None
            if point:
                original = UsdGeom.XformCache().GetLocalToWorldTransform(point).ExtractTranslation()
                stage.SetEditTarget(stage.GetSessionLayer())
                UsdGeom.Xformable(point).AddTranslateOp(opSuffix="witness").Set(Gf.Vec3d(.1, 0, 0))
                displaced = UsdGeom.XformCache().GetLocalToWorldTransform(point).ExtractTranslation()
                assert (displaced - original).GetLength() == pytest.approx(.1)
            return stage

        with monkeypatch.context() as substitute:
            substitute.setattr(USDGraph, "compose", displaced_site)
            rejected = client.post(path, headers=OPERATOR, json=body)
            assert rejected.status_code == 422
            assert "calibrated Asset coordinates" in rejected.text

        published = client.post(path, headers=OPERATOR, json=body)
        assert published.status_code == 201, published.text
        task = published.json()
        assert task["task"]["referenceMotionRef"] == body["reference"]
        assert (client.post(path, headers=OPERATOR, json=body)).json()["idempotent"]

        plan = client.post(
            "/api/v1/plans",
            headers=OPERATOR,
            json=training_plan(
                task["id"],
                algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"},
                evaluation={"scene": task["task"]["scene"], "episodes": 30, "threshold": 24},
            ),
        )
        assert plan.status_code == 201, plan.text
        frozen = plan.json()["goalSuite"]["trajectoryCompletion"]
        assert frozen["mode"] == "phase_clip" and frozen["trajectory"]["hingeRad"] == .05
        node = register(client, "reference-probe")
        requested = client.post(
            "/api/v1/jobs",
            headers=OPERATOR,
            json={
                "kind": "plan_validation",
                "planId": plan.json()["id"],
                "seed": 42,
                "targetNodeId": "reference-probe",
            },
        )
        assert requested.status_code == 202, requested.text
        jobs = beat(
            client,
            node,
            compilerHash=compiler_hash(),
            trainerVersions=MJLAB_VERSIONS,
            trainingEntries={"mjlab": "mjlab:managed"},
            gpus=[{"id": "GPU-fixture", "name": "GPU-free admission fixture"}],
        )
        assert jobs.status_code == 200, jobs.text
        (assignment,) = jobs.json()["assignments"]
        base = f"/api/v1/jobs/{assignment['jobId']}/inputs"
        assert client.get(base, headers=node).status_code == 403
        lease = node | {"X-Lease-Id": assignment["leaseId"]}
        dispatch = client.get(base, headers=lease).json()
        assert dispatch["compilerHash"] == compiler_hash()
        (package,) = [item for item in dispatch["packages"] if item["kind"] == "task"]
        assert package["contentHash"] == task["contentHash"]
        transfer = client.get(package["url"], headers=lease)
        assert transfer.status_code == 200, transfer.text
        with tarfile.open(fileobj=io.BytesIO(transfer.content)) as archive:
            archive.extractall(tmp_path / "leased", filter="data")
        frozen = declarative_files(task["task"], tmp_path / "leased")
        assert hashlib.sha256(frozen).hexdigest() == reference["sha256"]
        assert json.loads(frozen)["source"] == reference["source"]
        (tmp_path / "leased/reference-motion.json").write_bytes(b"{}")
        with pytest.raises(ValueError, match="declared digest"):
            declarative_files(task["task"], tmp_path / "leased")

    reopened = create_app(settings)
    with TestClient(reopened) as client:
        assert (client.get(url, headers=VIEWER)).json() == reference
        assert (client.get("/api/v1/tasks/microduck-imitate/1", headers=VIEWER)).json()["task"] == task[
            "task"
        ]


def assert_policy_archive(client, manager, task, ref, temporary):
    """Run the real freeze and public export with an explicit engineering weight fixture."""
    temporary.mkdir()
    width = manager.tasks.observation(task["id"]).size
    action_count = 14
    weight = helper.make_tensor(
        "weight", TensorProto.FLOAT, [width, action_count], [0.0] * width * action_count
    )
    graph = helper.make_graph(
        [helper.make_node("MatMul", ["observations", "weight"], ["actions"])],
        "controlled-reference-archive",
        [helper.make_tensor_value_info("observations", TensorProto.FLOAT, [1, width])],
        [helper.make_tensor_value_info("actions", TensorProto.FLOAT, [1, action_count])],
        [weight],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    model.ir_version = 10
    model_path = temporary / "policy.onnx"
    model_path.write_bytes(model.SerializeToString())
    model_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    export = {"checkpointSha256": "a" * 64, "policySha256": model_hash, "externalData": []}
    export_path = temporary / "export.json"
    export_path.write_text(json.dumps(export))
    job = {
        "jobId": "controlled-reference-archive",
        "scene": task["task"]["scene"],
        "sceneContentHash": task["sceneContentHash"],
        "versions": {},
        "artifacts": [
            {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
            for path in (model_path, export_path)
        ],
    }
    policy = manager.policies._freeze(
        manager,
        job["jobId"] + "/fixture",
        job,
        task,
        temporary,
        ["policy.onnx", "export.json"],
        "policy.onnx",
        export,
    )
    route = f"/api/v1/policies/{policy['name']}/{policy['rev']}/export"
    assert client.get(route, headers=VIEWER).status_code == 403
    response = client.get(route, headers=OPERATOR)
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        actual = archive.read("provenance/reference-motion.json")
        assert manifest["referenceMotionRef"] == task["task"]["referenceMotionRef"]
        assert hashlib.sha256(actual).hexdigest() == ref["sha256"]
        assert json.loads(actual)["source"] == ref["source"]
        assert json.loads(archive.read(manifest["referenceMotion"])) == ref["motion"]


@pytest.mark.parametrize("mixed_motion", [False, True], ids=["straight", "swing-with-flexion"])
def test_dominant_leg_requires_mapping_for_its_motion(mixed_motion):
    import math

    from packages.info.plans import PlanError
    from packages.motion.reference_contract import ReferenceRequest
    from packages.motion.retarget import build_reference

    app = create_app(remote_settings())
    with TestClient(app):
        asset = app.state.manager.library_assets.resolve("robot/microduck@1")
        frames = []
        for time in (0., .5, 1.):
            alpha = time * math.pi / 2
            bend = .4 * time
            distal = alpha + (bend if mixed_motion else 0.)
            positions = {
                "rightHip": (0., -.1, 0.),
                "rightKnee": (.3 * math.sin(alpha), -.1, -.3 * math.cos(alpha)),
                "rightAnkle": (.3 * (math.sin(alpha) + math.sin(distal)), -.1,
                               -.3 * (math.cos(alpha) + math.cos(distal))),
                "leftHip": (0., .1, 0.),
                "leftKnee": (0., .1, -.3),
                "leftAnkle": (.3 * math.sin(bend), .1, -.3 - .3 * math.cos(bend)),
            }
            for side in ("left", "right"):
                for name, height in (("Shoulder", .6), ("Elbow", .4), ("Wrist", .2)):
                    positions[side + name] = (0., .2 if side == "left" else -.2, height)
            frames.append({"timeSeconds": time, "joints": {
                name: {"position": values} for name, values in positions.items()
            }})
        side, gain = ("right", .1) if mixed_motion else ("left", -.1)
        request = ReferenceRequest.model_validate({
            "asset": asset.id, "assetContentHash": asset.content_hash,
            "clip": {"id": "motion-clip/" + "0" * 64 + "@1", "sha256": "0" * 64},
            "mapping": MAPPING | {"bends": {
                side + "Leg": {"joint": f"/Asset/joints/{side}_knee", "gain": gain},
            }},
        })
        with pytest.raises(PlanError) as caught:
            build_reference(asset, request, {"frames": frames})
        detail = caught.value.detail
        assert detail["limb"] == "rightLeg"
        if mixed_motion:
            assert detail["code"] == "reference_unmapped_limb_swing"
            assert detail["swingRad"] == pytest.approx(math.pi / 2)
            assert detail["bendRad"] == pytest.approx(.4)
        else:
            assert detail["code"] == "reference_unmapped_active_limb"
            assert detail["excursionRad"] == pytest.approx(math.pi / 2)



@pytest.mark.parametrize(
    "duration,dwell,expected",
    [(22.4, .25, 422), (19.74901, .25001, 422), (19.70, .25001, 201)],
)
def test_pinned_plan_respects_sampled_host_deadline(duration, dwell, expected):
    app = create_app(remote_settings())
    with TestClient(app) as client:
        request = keyframes(app.state.manager.library_assets)
        request["keyframes"]["frames"][-1]["time"] = duration - .4
        published = client.post(URL, headers=OPERATOR, json=request)
        assert published.status_code == 201, published.text
        recipe = imitation_request(published.json())
        recipe["task"]["declarative"]["episodeSeconds"] = 25.
        recipe["task"]["completion"]["dwellSeconds"] = dwell
        task = client.post("/api/v1/tasks/reference-motion", headers=OPERATOR, json=recipe)
        assert task.status_code == 201, task.text
        plan = client.post("/api/v1/plans", headers=OPERATOR, json=training_plan(
            task.json()["id"],
            evaluation={"scene": recipe["task"]["scene"], "episodes": 30, "threshold": 24},
        ))
        assert plan.status_code == expected, plan.text
        if expected == 422:
            message = plan.json()["detail"]["message"]
            assert "20 s deadline" in message
            if duration == 19.74901:
                assert "20.016 s at sampled control times" in message
