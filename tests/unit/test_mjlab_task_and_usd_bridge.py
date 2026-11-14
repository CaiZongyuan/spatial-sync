"""Unit tests for mjlab Task metadata/library validation and general USD-to-MuJoCo derivation."""

import io
import json
import tarfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.training.schema import TaskMetadata
from tests.fixtures.training_task import task_archive, task_document, training_plan


def _mjlab_task_document(name="mjlab-probe", **mjlab_fields):
    doc = task_document(name)
    doc.pop("isaaclab")
    doc["mjlab"] = {
        "task_id": "Lab-Arm-Reach-Mjlab-v0",
        "sceneInput": "scene-package",
        **mjlab_fields,
    }
    return doc


def _mjlab_task_archive(
    tmp_path: Path, *, doc: dict | None = None, entry_group: str = "mjlab.tasks"
) -> bytes:
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "task.yaml").write_text(yaml.safe_dump(doc or _mjlab_task_document()), encoding="utf-8")
    (pkg / "obs_spec.yaml").write_text(
        "observation: [joint_positions]\naction: joint_position_delta\n",
        encoding="utf-8",
    )
    (pkg / "pyproject.toml").write_text(
        f'[project]\nname="mjlab-probe"\nversion="0.1.0"\n'
        f'[project.entry-points."{entry_group}"]\nprobe="probe:register"\n',
        encoding="utf-8",
    )
    (pkg / "probe.py").write_text(
        'raise RuntimeError("Task package must never execute on push")\n',
        encoding="utf-8",
    )
    return task_archive(pkg)


def test_task_metadata_discriminates_isaaclab_xor_mjlab_and_checks_control_rate():
    isaac_doc = task_document("isaac-probe")
    parsed_isaac = TaskMetadata.model_validate(isaac_doc)
    dumped_isaac = parsed_isaac.model_dump(mode="json")
    assert "isaaclab" in dumped_isaac and "mjlab" not in dumped_isaac

    mjlab_doc = _mjlab_task_document("mjlab-probe", physics_dt=0.005, decimation=4)
    mjlab_doc["control"]["hz"] = 50.0
    parsed_mjlab = TaskMetadata.model_validate(mjlab_doc)
    dumped_mjlab = parsed_mjlab.model_dump(mode="json")
    assert "mjlab" in dumped_mjlab and "isaaclab" not in dumped_mjlab
    assert dumped_mjlab["mjlab"]["task_id"] == "Lab-Arm-Reach-Mjlab-v0"
    assert "presets" not in dumped_mjlab["mjlab"]

    with_presets = _mjlab_task_document("mjlab-presets", presets=["flat"])
    with pytest.raises(ValidationError):
        TaskMetadata.model_validate(with_presets)

    neither = task_document("neither")
    neither.pop("isaaclab")
    with pytest.raises(ValidationError):
        TaskMetadata.model_validate(neither)

    both = task_document("both")
    both["mjlab"] = {"task_id": "Lab-Arm-Reach-Mjlab-v0"}
    with pytest.raises(ValidationError):
        TaskMetadata.model_validate(both)

    bad_rate = _mjlab_task_document("bad-rate", physics_dt=0.002, decimation=10)
    bad_rate["control"]["hz"] = 30.0
    with pytest.raises(ValidationError, match="control.hz"):
        TaskMetadata.model_validate(bad_rate)


def test_task_library_and_job_admission_support_mjlab_backend_and_versions(tmp_path: Path):
    token = "fixture-node-secret"
    app = create_app(Settings(node_token=token))
    with TestClient(app) as client:
        wrong_entry = _mjlab_task_archive(tmp_path / "wrong", entry_group="isaaclab.tasks")
        rejected_push = client.post(
            "/api/v1/tasks", content=wrong_entry, headers={"Content-Type": "application/x-tar"}
        )
        assert rejected_push.status_code == 422
        assert "mjlab.tasks" in rejected_push.text

        usd_only_doc = _mjlab_task_document("mjlab-usd-only", sceneInput="usd")
        usd_only_tar = _mjlab_task_archive(tmp_path / "usd_only", doc=usd_only_doc)
        usd_only_task = client.post(
            "/api/v1/tasks", content=usd_only_tar, headers={"Content-Type": "application/x-tar"}
        )
        assert usd_only_task.status_code == 201, usd_only_task.text

        valid_tar = _mjlab_task_archive(tmp_path / "valid")
        pushed = client.post(
            "/api/v1/tasks", content=valid_tar, headers={"Content-Type": "application/x-tar"}
        )
        assert pushed.status_code == 201, pushed.text
        task = pushed.json()
        assert "mjlab" in task["task"] and "isaaclab" not in task["task"]

        # Python-stage scene (`robot-and-tubes@2`) rejects sceneInput="usd" for mjlab too
        plan_usd = client.post(
            "/api/v1/plans",
            json=training_plan(usd_only_task.json()["id"], budget={"gpuHours": 1}),
        ).json()
        client.post(f"/api/v1/approvals/{plan_usd['approvalId']}/decision", json={"decision": "approve"})
        bad_job = client.post("/api/v1/jobs", json={"planId": plan_usd["id"], "seed": 42})
        assert bad_job.status_code == 422
        assert bad_job.json()["detail"]["code"] == "task_scene_input_unsupported"

        # Valid mjlab scene-package task admits job and validates mjlab artifact runtime versions
        plan = client.post(
            "/api/v1/plans",
            json=training_plan(task["id"], budget={"gpuHours": 1}, hyperparameters={
                "num_envs": 4, "max_iterations": 2, "learning_rate": 0.001, "device": "cpu",
            }),
        ).json()
        client.post(f"/api/v1/approvals/{plan['approvalId']}/decision", json={"decision": "approve"})

        from packages.training.jobs import profile_key

        hb = {
            "instanceId": "worker-mjlab",
            "hardware": "hw-mjlab",
            "gpus": [],
            "cpuAvailable": True,
            "profiles": [
                {
                    "key": profile_key(plan, task["contentHash"], "hw-mjlab", "cpu"),
                    "iterationSeconds": 0.1,
                    "samples": 2,
                    "measuredAt": 10,
                    "evidence": "unit fixture",
                }
            ],
            "reports": [],
        }
        node_headers = {"X-Node-Token": token, "X-Node-Instance": "worker-mjlab"}
        assert client.post("/api/v1/nodes/local/heartbeat", headers=node_headers, json=hb).status_code == 200

        job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        assert job["leaseId"] is not None
        lease_headers = node_headers | {"X-Lease-Id": job["leaseId"]}

        def build_artifacts(versions_dict: dict[str, str]) -> bytes:
            import hashlib

            ckpt = b"fake-pt-weights"
            onnx_bytes = b"fake-onnx-graph"
            files = {
                "stdout.log": b"ok\n",
                "stderr.log": b"",
                "checkpoints/model_2.pt": ckpt,
                "policy.onnx": onnx_bytes,
                "tensorboard/events.out.tfevents.1": b"tfevent",
                "export.json": json.dumps(
                    {
                        "checkpoint": "checkpoints/model_2.pt",
                        "checkpointSha256": hashlib.sha256(ckpt).hexdigest(),
                        "policy": "policy.onnx",
                        "policySha256": hashlib.sha256(onnx_bytes).hexdigest(),
                        "iteration": 2,
                        "afterLearn": True,
                        "externalData": [],
                    }
                ).encode(),
                "versions.json": json.dumps(
                    {
                        "jobId": job["jobId"],
                        "task": task["id"],
                        "taskContentHash": task["contentHash"],
                        "scene": job["scene"],
                        "sceneContentHash": job["sceneContentHash"],
                        "demonstrationSets": [],
                        "seed": 42,
                        "hardware": {"device": "cpu", "gpu": None},
                        "versions": versions_dict,
                    }
                ).encode(),
            }
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                for rel, data in files.items():
                    info = tarfile.TarInfo(rel)
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
            return buf.getvalue()

        missing_mjwarp = client.post(
            f"/api/v1/jobs/{job['jobId']}/artifacts",
            headers=lease_headers | {"Content-Type": "application/x-tar"},
            content=build_artifacts(
                {"mjlab": "1.3.0", "mujoco": "3.5.0", "warp": "1.12.0", "torch": "2.9.1", "rsl_rl": "5.0.1"}
            ),
        )
        assert missing_mjwarp.status_code == 422

        accepted = client.post(
            f"/api/v1/jobs/{job['jobId']}/artifacts",
            headers=lease_headers | {"Content-Type": "application/x-tar"},
            content=build_artifacts(
                {
                    "mjlab": "1.3.0",
                    "mujoco": "3.5.0",
                    "mujoco_warp": "3.5.0",
                    "warp": "1.12.0",
                    "torch": "2.9.1",
                    "rsl_rl": "5.0.1",
                }
            ),
        )
        assert accepted.status_code == 200, accepted.text


def _write_test_articulation_usd(asset_path: Path, *, fixed_base: bool) -> Path:
    import math as _math

    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    asset_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(asset_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)

    root = UsdGeom.Xform.Define(stage, "/Asset").GetPrim()
    stage.SetDefaultPrim(root)
    UsdPhysics.ArticulationRootAPI.Apply(root)

    # Base link
    base = UsdGeom.Xform.Define(stage, "/Asset/base_link")
    base_prim = base.GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(base_prim)
    base_mass = UsdPhysics.MassAPI.Apply(base_prim)
    base_mass.CreateMassAttr(2.5)
    base_mass.CreateCenterOfMassAttr(Gf.Vec3f(0.01, -0.02, 0.05))
    base_mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0.02, 0.03, 0.04))
    half_s2 = _math.sqrt(0.5)
    base_mass.CreatePrincipalAxesAttr(Gf.Quatf(half_s2, Gf.Vec3f(0.0, 0.0, half_s2)))

    base_box = UsdGeom.Cube.Define(stage, "/Asset/base_link/base_collision")
    base_box.CreateSizeAttr(0.2)
    base_box.AddScaleOp().Set(Gf.Vec3f(1.0, 0.5, 0.25))
    UsdPhysics.CollisionAPI.Apply(base_box.GetPrim())

    base_vis = UsdGeom.Cylinder.Define(stage, "/Asset/base_link/base_visual")
    base_vis.CreateRadiusAttr(0.04)
    base_vis.CreateHeightAttr(0.12)
    base_vis.CreateAxisAttr(UsdGeom.Tokens.x)

    if fixed_base:
        world_weld = UsdPhysics.FixedJoint.Define(stage, "/Asset/base_fixed_joint")
        world_weld.CreateBody1Rel().SetTargets([Sdf.Path("/Asset/base_link")])
        world_weld.CreateLocalPos0Attr(Gf.Vec3f(0.0, 0.0, 0.1))
        world_weld.CreateLocalRot0Attr(Gf.Quatf(1.0, 0.0, 0.0, 0.0))
        world_weld.CreateLocalPos1Attr(Gf.Vec3f(0.0, 0.0, 0.0))
        world_weld.CreateLocalRot1Attr(Gf.Quatf(1.0, 0.0, 0.0, 0.0))

    # Upper link + revolute joint (authored in USD degrees & N*m/deg!)
    upper = UsdGeom.Xform.Define(stage, "/Asset/upper_link")
    upper.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.15))
    upper_prim = upper.GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(upper_prim)
    upper_mass = UsdPhysics.MassAPI.Apply(upper_prim)
    upper_mass.CreateMassAttr(1.2)
    upper_mass.CreateCenterOfMassAttr(Gf.Vec3f(0.0, 0.0, 0.12))
    upper_mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0.008, 0.009, 0.004))

    upper_cap = UsdGeom.Capsule.Define(stage, "/Asset/upper_link/link_capsule")
    upper_cap.CreateRadiusAttr(0.03)
    upper_cap.CreateHeightAttr(0.24)
    upper_cap.CreateAxisAttr(UsdGeom.Tokens.z)
    upper_cap.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.12))
    UsdPhysics.CollisionAPI.Apply(upper_cap.GetPrim())

    upper_mesh = UsdGeom.Mesh.Define(stage, "/Asset/upper_link/fin_mesh")
    upper_mesh.CreatePointsAttr(
        [
            Gf.Vec3f(0.0, 0.0, 0.0),
            Gf.Vec3f(0.06, 0.0, 0.0),
            Gf.Vec3f(0.0, 0.05, 0.0),
            Gf.Vec3f(0.0, 0.0, 0.07),
        ]
    )
    upper_mesh.CreateFaceVertexCountsAttr([3, 3, 3, 3])
    upper_mesh.CreateFaceVertexIndicesAttr([0, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3])
    UsdPhysics.CollisionAPI.Apply(upper_mesh.GetPrim())

    rev = UsdPhysics.RevoluteJoint.Define(stage, "/Asset/shoulder_pitch")
    rev.CreateAxisAttr(UsdGeom.Tokens.y)
    rev.CreateBody0Rel().SetTargets([Sdf.Path("/Asset/base_link")])
    rev.CreateBody1Rel().SetTargets([Sdf.Path("/Asset/upper_link")])
    rev.CreateLocalPos0Attr(Gf.Vec3f(0.0, 0.0, 0.15))
    rev.CreateLocalRot0Attr(Gf.Quatf(1.0, 0.0, 0.0, 0.0))
    rev.CreateLocalPos1Attr(Gf.Vec3f(0.0, 0.0, 0.0))
    rev.CreateLocalRot1Attr(Gf.Quatf(1.0, 0.0, 0.0, 0.0))
    rev.CreateLowerLimitAttr(-90.0)
    rev.CreateUpperLimitAttr(90.0)
    rev_drive = UsdPhysics.DriveAPI.Apply(rev.GetPrim(), "angular")
    rev_drive.CreateTypeAttr("force")
    rev_drive.CreateStiffnessAttr(2.0)  # 2.0 N*m/deg = 114.591559 N*m/rad
    rev_drive.CreateDampingAttr(0.1)    # 0.1 N*m*s/deg = 5.729578 N*m*s/rad
    rev_drive.CreateMaxForceAttr(18.0)  # 18.0 N*m
    rev_drive.CreateTargetPositionAttr(15.0)  # 15 deg = 0.261799 rad
    rev.GetPrim().CreateAttribute("mjc:armature", Sdf.ValueTypeNames.Float).Set(0.012)
    rev.GetPrim().CreateAttribute("mjc:frictionloss", Sdf.ValueTypeNames.Float).Set(0.05)

    # Welded child rigid body + site
    mount = UsdGeom.Xform.Define(stage, "/Asset/wrist_mount")
    mount.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.45))
    mount_prim = mount.GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(mount_prim)
    mount_mass = UsdPhysics.MassAPI.Apply(mount_prim)
    mount_mass.CreateMassAttr(0.35)
    mount_mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0.001, 0.001, 0.001))

    ee_site = UsdGeom.Xform.Define(stage, "/Asset/wrist_mount/ee_site")
    ee_site.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.05))

    weld = UsdPhysics.FixedJoint.Define(stage, "/Asset/tool_mount")
    weld.CreateBody0Rel().SetTargets([Sdf.Path("/Asset/upper_link")])
    weld.CreateBody1Rel().SetTargets([Sdf.Path("/Asset/wrist_mount")])
    weld.CreateLocalPos0Attr(Gf.Vec3f(0.0, 0.0, 0.30))
    weld.CreateLocalRot0Attr(Gf.Quatf(1.0, 0.0, 0.0, 0.0))
    weld.CreateLocalPos1Attr(Gf.Vec3f(0.0, 0.0, 0.0))
    weld.CreateLocalRot1Attr(Gf.Quatf(1.0, 0.0, 0.0, 0.0))

    # Prismatic joint + carriage link (SI meters & N/m)
    carriage = UsdGeom.Xform.Define(stage, "/Asset/carriage_link")
    carriage.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.45))
    carriage_prim = carriage.GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(carriage_prim)
    carriage_mass = UsdPhysics.MassAPI.Apply(carriage_prim)
    carriage_mass.CreateMassAttr(0.25)
    carriage_mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0.0005, 0.0005, 0.0005))

    ball = UsdGeom.Sphere.Define(stage, "/Asset/carriage_link/tip_sphere")
    ball.CreateRadiusAttr(0.025)
    UsdPhysics.CollisionAPI.Apply(ball.GetPrim())

    slide = UsdPhysics.PrismaticJoint.Define(stage, "/Asset/slider_joint")
    slide.CreateAxisAttr(UsdGeom.Tokens.z)
    slide.CreateBody0Rel().SetTargets([Sdf.Path("/Asset/wrist_mount")])
    slide.CreateBody1Rel().SetTargets([Sdf.Path("/Asset/carriage_link")])
    slide.CreateLocalPos0Attr(Gf.Vec3f(0.0, 0.0, 0.0))
    slide.CreateLocalRot0Attr(Gf.Quatf(1.0, 0.0, 0.0, 0.0))
    slide.CreateLocalPos1Attr(Gf.Vec3f(0.0, 0.0, 0.0))
    slide.CreateLocalRot1Attr(Gf.Quatf(1.0, 0.0, 0.0, 0.0))
    slide.CreateLowerLimitAttr(0.0)
    slide.CreateUpperLimitAttr(0.04)
    slide_drive = UsdPhysics.DriveAPI.Apply(slide.GetPrim(), "linear")
    slide_drive.CreateTypeAttr("force")
    slide_drive.CreateStiffnessAttr(400.0)
    slide_drive.CreateDampingAttr(25.0)
    slide_drive.CreateMaxForceAttr(35.0)
    slide_drive.CreateTargetPositionAttr(0.02)
    slide.GetPrim().CreateAttribute("mjc:armature", Sdf.ValueTypeNames.Float).Set(0.003)
    slide.GetPrim().CreateAttribute("mjc:frictionloss", Sdf.ValueTypeNames.Float).Set(0.1)

    stage.GetRootLayer().Save()
    return asset_path


def test_usd_mjcf_derives_articulation_hierarchy_inertias_geoms_drives_and_ground_plane(tmp_path: Path):
    import math
    import xml.etree.ElementTree as ET

    from apps.node.usd_mjcf import derive_usd_mjcf, verify_against_observation_spec

    floating_usd = _write_test_articulation_usd(tmp_path / "floating" / "asset.usda", fixed_base=False)
    floating = derive_usd_mjcf(floating_usd)
    assert floating.fixed_base is False
    assert floating.joint_names == ("shoulder_pitch", "slider_joint")
    assert "ee_site" in floating.sites

    j_rev, j_slide = floating.joints
    assert j_rev.kind == "hinge"
    assert j_rev.lower == pytest.approx(-math.pi / 2.0)
    assert j_rev.upper == pytest.approx(math.pi / 2.0)
    assert j_rev.default_position == pytest.approx(math.radians(15.0))
    assert j_rev.stiffness == pytest.approx(2.0 * 180.0 / math.pi)
    assert j_rev.damping == pytest.approx(0.1 * 180.0 / math.pi)
    assert j_rev.max_force == pytest.approx(18.0)
    assert j_rev.armature == pytest.approx(0.012)
    assert j_rev.frictionloss == pytest.approx(0.05)

    assert j_slide.kind == "slide"
    assert j_slide.lower == pytest.approx(0.0)
    assert j_slide.upper == pytest.approx(0.04)
    assert j_slide.default_position == pytest.approx(0.02)
    assert j_slide.stiffness == pytest.approx(400.0)
    assert j_slide.damping == pytest.approx(25.0)
    assert j_slide.max_force == pytest.approx(35.0)
    assert j_slide.armature == pytest.approx(0.003)
    assert j_slide.frictionloss == pytest.approx(0.1)

    xml_root = ET.fromstring(floating.mjcf_xml)
    assert xml_root.find(".//freejoint") is not None
    assert xml_root.find(".//asset/mesh") is not None
    assert len(xml_root.findall(".//actuator/position")) == 2

    # Scene stage referencing library:assets/... resolved via asset_packages + fixed base + ground plane
    fixed_asset_dir = tmp_path / "fixed_pkg"
    _write_test_articulation_usd(fixed_asset_dir / "asset.usda", fixed_base=True)
    scene_stage = tmp_path / "scene" / "stage.usda"
    scene_stage.parent.mkdir(parents=True, exist_ok=True)
    scene_stage.write_text(
        '#usda 1.0\n'
        '(defaultPrim = "World"\n metersPerUnit = 1\n upAxis = "Z")\n'
        'def Xform "World" {\n'
        '  def Xform "Lab" {\n'
        '    def Xform "TestArm" (references = @library:assets/robot/test-arm/1/asset.usda@</Asset>) {\n'
        '      double3 xformOp:translate = (0.4, -0.2, 0.0)\n'
        '      uniform token[] xformOpOrder = ["xformOp:translate"]\n'
        '    }\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )
    fixed = derive_usd_mjcf(
        scene_stage,
        prim_path="/World/Lab/TestArm",
        asset_packages={"robot/test-arm@1": fixed_asset_dir},
        include_ground_plane=True,
    )
    assert fixed.fixed_base is True
    assert fixed.root_pos == pytest.approx((0.4, -0.2, 0.1))
    fixed_xml = ET.fromstring(fixed.mjcf_xml)
    assert fixed_xml.find(".//freejoint") is None
    assert fixed_xml.find(".//worldbody/geom[@type='plane']") is not None

    # ObservationSpec (lw.obs-spec/1) verification helper
    from packages.policies.observations import ObservationSpec

    valid_obs_spec = {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "shoulder_pos",
                "source": "joint_position",
                "keys": ["/World/Lab/TestArm/shoulder_pitch"],
                "frame": "world",
                "unit": "rad",
                "shape": [1],
                "offset": [math.radians(15.0)],
                "scale": [1.0],
            },
            {
                "name": "slider_pos",
                "source": "joint_position",
                "keys": ["/World/Lab/TestArm/slider_joint"],
                "frame": "world",
                "unit": "m",
                "shape": [1],
                "offset": [0.02],
                "scale": [1.0],
            },
            {
                "name": "shoulder_vel",
                "source": "joint_velocity",
                "keys": ["/World/Lab/TestArm/shoulder_pitch"],
                "frame": "world",
                "unit": "rad/s",
                "shape": [1],
                "offset": [0.0],
                "scale": [1.0],
            },
            {
                "name": "slider_vel",
                "source": "joint_velocity",
                "keys": ["/World/Lab/TestArm/slider_joint"],
                "frame": "world",
                "unit": "m/s",
                "shape": [1],
                "offset": [0.0],
                "scale": [1.0],
            },
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": ["/World/Lab/TestArm/shoulder_pitch", "/World/Lab/TestArm/slider_joint"],
            "units": ["rad", "m"],
            "defaultPosition": [math.radians(15.0), 0.02],
            "scale": [1.0, 1.0],
            "lower": [-math.pi / 2.0, 0.0],
            "upper": [math.pi / 2.0, 0.04],
        },
    }
    parsed_spec = ObservationSpec.model_validate(valid_obs_spec)
    verify_against_observation_spec(
        fixed,
        parsed_spec,
        expected_kp={"shoulder_pitch": 2.0 * 180.0 / math.pi, "slider_joint": 400.0},
        expected_kd={"shoulder_pitch": 0.1 * 180.0 / math.pi, "slider_joint": 25.0},
        check_observation_offsets=True,
    )
    # Also verify standalone Asset derivation remapped to scene entity_key
    verify_against_observation_spec(
        floating,
        parsed_spec,
        entity_key="/World/Lab/TestArm",
    )

    swapped_spec = json.loads(json.dumps(valid_obs_spec))
    swapped_spec["action"]["jointKeys"] = [
        "/World/Lab/TestArm/slider_joint",
        "/World/Lab/TestArm/shoulder_pitch",
    ]
    # Swapping jointKeys without swapping units/defaultPosition/bounds fails closed
    with pytest.raises(ValueError, match="units"):
        verify_against_observation_spec(fixed, swapped_spec)
    # Swapping jointKeys WITH matching units/defaultPosition/bounds/gains succeeds (preserve_order=True)
    swapped_spec["action"]["units"] = ["m", "rad"]
    swapped_spec["action"]["defaultPosition"] = [0.02, math.radians(15.0)]
    swapped_spec["action"]["lower"] = [0.0, -math.pi / 2.0]
    swapped_spec["action"]["upper"] = [0.04, math.pi / 2.0]
    verify_against_observation_spec(
        fixed,
        swapped_spec,
        expected_kp=[400.0, 2.0 * 180.0 / math.pi],
        expected_kd=[25.0, 0.1 * 180.0 / math.pi],
    )

    drifted_default = json.loads(json.dumps(valid_obs_spec))
    drifted_default["action"]["defaultPosition"] = [0.0, 0.02]
    with pytest.raises(ValueError, match="defaultPosition"):
        verify_against_observation_spec(fixed, drifted_default)

    bad_limit = json.loads(json.dumps(valid_obs_spec))
    bad_limit["action"]["upper"] = [1.0, 0.04]
    with pytest.raises(ValueError, match="upper"):
        verify_against_observation_spec(fixed, bad_limit)

    with pytest.raises(ValueError, match="kp"):
        verify_against_observation_spec(
            fixed, valid_obs_spec, expected_kp={"shoulder_pitch": 10.0, "slider_joint": 400.0}
        )


def test_usd_mjcf_compiles_in_mujoco_and_matches_newton_and_fr3(tmp_path: Path):
    mujoco = pytest.importorskip("mujoco")
    import numpy as np

    from apps.node.usd_mjcf import derive_usd_mjcf

    usd_path = _write_test_articulation_usd(tmp_path / "fixed" / "asset.usda", fixed_base=True)
    derived = derive_usd_mjcf(usd_path, include_ground_plane=True)
    mj_model = derived.to_mjmodel()
    mj_data = mujoco.MjData(mj_model)
    for idx, j in enumerate(derived.joints):
        mj_data.qpos[idx] = j.default_position
        mj_data.ctrl[idx] = j.default_position
    mujoco.mj_forward(mj_model, mj_data)
    mujoco.mj_step(mj_model, mj_data)

    assert mj_model.nq == 2 and mj_model.nv == 2 and mj_model.nu == 2
    np.testing.assert_allclose(mj_model.dof_armature, [0.012, 0.003], atol=1e-6)
    np.testing.assert_allclose(mj_model.dof_frictionloss, [0.05, 0.1], atol=1e-6)
    np.testing.assert_allclose(
        mj_model.actuator_gainprm[:, 0],
        [derived.joints[0].stiffness, derived.joints[1].stiffness],
        rtol=1e-5,
    )
    np.testing.assert_allclose(
        mj_model.actuator_biasprm[:, 1],
        [-derived.joints[0].stiffness, -derived.joints[1].stiffness],
        rtol=1e-5,
    )
    np.testing.assert_allclose(
        mj_model.actuator_biasprm[:, 2],
        [-derived.joints[0].damping, -derived.joints[1].damping],
        rtol=1e-5,
    )
    np.testing.assert_allclose(
        mj_model.actuator_forcerange,
        [[-18.0, 18.0], [-35.0, 35.0]],
        rtol=1e-5,
    )

    # Verify real repository asset `fr3/1/asset.usdc` compiles cleanly and preserves all 9 joints
    fr3_path = Path(__file__).resolve().parents[2] / "library/assets/robot/fr3/1/asset.usdc"
    fr3_derived = derive_usd_mjcf(fr3_path)
    assert fr3_derived.fixed_base is True
    assert len(fr3_derived.joints) == 9
    fr3_model = fr3_derived.to_mjmodel()
    assert fr3_model.nq == 9 and fr3_model.nv == 9

    newton = pytest.importorskip("newton")
    import warp as wp
    from pxr import Usd

    with wp.ScopedDevice("cpu"):
        builder = newton.ModelBuilder()
        builder.add_usd(Usd.Stage.Open(str(usd_path)))
        newton_model = builder.finalize(device="cpu")
        solver = newton.solvers.SolverMuJoCo(newton_model, use_mujoco_cpu=True)
        ref_model = solver.mj_model
        np.testing.assert_allclose(mj_model.jnt_range, ref_model.jnt_range, atol=1e-5)
        np.testing.assert_allclose(mj_model.jnt_actfrcrange, ref_model.jnt_actfrcrange, rtol=1e-5)
        np.testing.assert_allclose(
            mj_model.actuator_gainprm[:, 0], ref_model.actuator_gainprm[:, 0], rtol=1e-5
        )
        np.testing.assert_allclose(
            mj_model.actuator_biasprm[:, 1:3], ref_model.actuator_biasprm[:, 1:3], rtol=1e-5
        )

        # Forward kinematics parity at non-zero joint configuration
        q_test = np.array([0.35, 0.025], dtype=np.float64)
        d1 = mujoco.MjData(mj_model)
        d2 = mujoco.MjData(ref_model)
        d1.qpos[:] = q_test
        d2.qpos[:] = q_test
        mujoco.mj_kinematics(mj_model, d1)
        mujoco.mj_kinematics(ref_model, d2)
        for body_name in ("base_link", "upper_link", "wrist_mount", "carriage_link"):
            b1 = mujoco.mj_name2id(mj_model, mujoco.mjtObj.mjOBJ_BODY, body_name)
            # SolverMuJoCo body names end with the prim name
            b2 = next(
                i for i in range(ref_model.nbody)
                if (mujoco.mj_id2name(ref_model, mujoco.mjtObj.mjOBJ_BODY, i) or "").endswith(body_name)
            )
            np.testing.assert_allclose(d1.xpos[b1], d2.xpos[b2], atol=1e-5)

        # Also verify body_mass/body_ipos/body_inertia parity when physics:mass has no diagonalInertia
        mass_no_diag = tmp_path / "mass_no_diag" / "asset.usda"
        mass_no_diag.parent.mkdir(parents=True, exist_ok=True)
        mass_no_diag.write_text(
            '#usda 1.0\n(defaultPrim = "Asset"\n metersPerUnit = 1\n upAxis = "Z")\n'
            'def Xform "Asset" (prepend apiSchemas = ["PhysicsArticulationRootAPI"]) {\n'
            '  def Xform "Carriage" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {\n'
            '    float physics:mass = 2.0\n'
            '    def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {\n'
            '      double size = 0.16\n'
            '    }\n'
            '  }\n'
            '  def Xform "Mast" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {\n'
            '    float physics:mass = 0.5\n'
            '    def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {\n'
            '      double size = 0.1\n'
            '      double3 xformOp:translate = (0, 0, 0.12)\n'
            '      uniform token[] xformOpOrder = ["xformOp:translate"]\n'
            '    }\n'
            '  }\n'
            '  def PhysicsRevoluteJoint "pitch" (prepend apiSchemas = ["PhysicsDriveAPI:angular"]) {\n'
            '    rel physics:body0 = </Asset/Carriage>\n'
            '    rel physics:body1 = </Asset/Mast>\n'
            '    uniform token physics:axis = "Y"\n'
            '    float physics:lowerLimit = -45\n'
            '    float physics:upperLimit = 45\n'
            '    float drive:angular:physics:stiffness = 1.5\n'
            '    float drive:angular:physics:damping = 0.3\n'
            '  }\n'
            '}\n',
            encoding="utf-8",
        )
        m1 = derive_usd_mjcf(mass_no_diag).to_mjmodel()
        b_nd = newton.ModelBuilder()
        b_nd.add_usd(Usd.Stage.Open(str(mass_no_diag)))
        m2 = newton.solvers.SolverMuJoCo(b_nd.finalize(device="cpu"), use_mujoco_cpu=True).mj_model
        for body_name in ("Carriage", "Mast"):
            i1 = mujoco.mj_name2id(m1, mujoco.mjtObj.mjOBJ_BODY, body_name)
            i2 = next(
                i for i in range(m2.nbody)
                if (mujoco.mj_id2name(m2, mujoco.mjtObj.mjOBJ_BODY, i) or "").endswith(body_name)
            )
            np.testing.assert_allclose(m1.body_mass[i1], m2.body_mass[i2], rtol=1e-5)
            np.testing.assert_allclose(m1.body_ipos[i1], m2.body_ipos[i2], atol=1e-5)
            np.testing.assert_allclose(
                np.sort(m1.body_inertia[i1]), np.sort(m2.body_inertia[i2]), rtol=1e-5
            )

        # Verify full 3x3 body-frame inertia tensor parity against Newton SolverMuJoCo when
        # physics:mass + physics:principalAxes = (1,0,0,0) (and (0,0,0,0)) are authored WITHOUT
        # diagonalInertia on a rotated capsule collider (Jacobi eigenpair must stay intact).
        rot_caps = tmp_path / "rot_caps_axes" / "asset.usda"
        rot_caps.parent.mkdir(parents=True, exist_ok=True)
        rot_caps.write_text(
            '#usda 1.0\n(defaultPrim = "Asset"\n metersPerUnit = 1\n upAxis = "Z")\n'
            'def Xform "Asset" (prepend apiSchemas = ["PhysicsArticulationRootAPI"]) {\n'
            '  def Xform "LinkA" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {\n'
            '    float physics:mass = 2.5\n'
            '    quatf physics:principalAxes = (1, 0, 0, 0)\n'
            '    def Capsule "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {\n'
            '      double radius = 0.04\n'
            '      double height = 0.30\n'
            '      quatf xformOp:orient = (0.8660254, 0.3535534, 0.3535534, 0)\n'
            '      uniform token[] xformOpOrder = ["xformOp:orient"]\n'
            '    }\n'
            '  }\n'
            '  def Xform "LinkB" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {\n'
            '    float physics:mass = 1.2\n'
            '    quatf physics:principalAxes = (0, 0, 0, 0)\n'
            '    def Capsule "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {\n'
            '      double radius = 0.03\n'
            '      double height = 0.22\n'
            '      quatf xformOp:orient = (0.7071068, 0.7071068, 0, 0)\n'
            '      uniform token[] xformOpOrder = ["xformOp:orient"]\n'
            '    }\n'
            '  }\n'
            '  def PhysicsRevoluteJoint "hinge" (prepend apiSchemas = ["PhysicsDriveAPI:angular"]) {\n'
            '    rel physics:body0 = </Asset/LinkA>\n'
            '    rel physics:body1 = </Asset/LinkB>\n'
            '    uniform token physics:axis = "Z"\n'
            '    float physics:lowerLimit = -90\n'
            '    float physics:upperLimit = 90\n'
            '    float drive:angular:physics:stiffness = 2.0\n'
            '    float drive:angular:physics:damping = 0.2\n'
            '  }\n'
            '}\n',
            encoding="utf-8",
        )

        def _body_inertia_tensor_3x3(model, body_idx: int) -> np.ndarray:
            mat9 = np.zeros(9, dtype=np.float64)
            mujoco.mju_quat2Mat(mat9, model.body_iquat[body_idx])
            r_mat = mat9.reshape(3, 3)
            return r_mat @ np.diag(model.body_inertia[body_idx]) @ r_mat.T

        rc_m1 = derive_usd_mjcf(rot_caps).to_mjmodel()
        rc_b = newton.ModelBuilder()
        rc_b.add_usd(Usd.Stage.Open(str(rot_caps)))
        rc_m2 = newton.solvers.SolverMuJoCo(rc_b.finalize(device="cpu"), use_mujoco_cpu=True).mj_model
        for body_name in ("LinkA", "LinkB"):
            i1 = mujoco.mj_name2id(rc_m1, mujoco.mjtObj.mjOBJ_BODY, body_name)
            i2 = next(
                i for i in range(rc_m2.nbody)
                if (mujoco.mj_id2name(rc_m2, mujoco.mjtObj.mjOBJ_BODY, i) or "").endswith(body_name)
            )
            np.testing.assert_allclose(rc_m1.body_mass[i1], rc_m2.body_mass[i2], rtol=1e-5)
            np.testing.assert_allclose(rc_m1.body_ipos[i1], rc_m2.body_ipos[i2], atol=1e-5)
            np.testing.assert_allclose(
                _body_inertia_tensor_3x3(rc_m1, i1),
                _body_inertia_tensor_3x3(rc_m2, i2),
                rtol=1e-5,
                atol=1e-6,
            )


def test_mjlab_trainer_arguments_maps_approved_hyperparameters_and_rejects_unknown_fields():
    from apps.node.mjlab_launcher import parse_mjlab_launcher_args
    from apps.node.runner import mjlab_trainer_arguments

    plan = training_plan(
        model={"actor": [128, 64], "critic": [256, 128], "activation": "elu", "obs_normalization": True},
        hyperparameters={
            "num_envs": 256,
            "max_iterations": 5,
            "device": "cuda",
            "learning_rate": 3e-4,
            "num_steps_per_env": 16,
            "num_learning_epochs": 4,
            "num_mini_batches": 2,
            "save_interval": 5,
        },
    )
    task = _mjlab_task_document("mjlab-args", physics_dt=0.005, decimation=4)
    args = mjlab_trainer_arguments(
        {
            "plan": plan,
            "task": task,
            "job": {"seed": 7, "jobId": "job-mjlab-1", "executionDevice": "cuda"},
        },
        "lw_training_bridge.install",
    )
    parsed = parse_mjlab_launcher_args(args)
    assert parsed.task == "Lab-Arm-Reach-Mjlab-v0"
    assert parsed.num_envs == 256
    assert parsed.max_iterations == 5
    assert parsed.seed == 7
    assert parsed.device == "cuda:0"
    assert parsed.logger == "tensorboard"
    assert parsed.experiment_name == "job-mjlab-1"
    assert parsed.external_callback == "lw_training_bridge.install"
    assert parsed.physics_dt == pytest.approx(0.005)
    assert parsed.decimation == 4
    assert parsed.learning_rate == pytest.approx(3e-4)
    assert json.loads(parsed.actor_hidden_dims) == [128, 64]
    assert json.loads(parsed.critic_hidden_dims) == [256, 128]
    assert parsed.activation == "elu"
    assert json.loads(parsed.obs_normalization) is True
    assert not hasattr(parsed, "presets")

    with pytest.raises(SystemExit):
        parse_mjlab_launcher_args([*args, "--presets", "flat"])

    presets_task = _mjlab_task_document("mjlab-presets-arg", presets=["flat"])
    with pytest.raises(ValueError, match="Unsupported"):
        mjlab_trainer_arguments(
            {
                "plan": plan,
                "task": presets_task,
                "job": {"seed": 7, "jobId": "job-mjlab-presets", "executionDevice": "cpu"},
            },
            "lw_training_bridge.install",
        )

    bad_plan = training_plan(hyperparameters={"num_envs": 4, "max_iterations": 1, "unknown_knob": 99})
    with pytest.raises(ValueError, match="Unsupported"):
        mjlab_trainer_arguments(
            {
                "plan": bad_plan,
                "task": task,
                "job": {"seed": 7, "jobId": "job-mjlab-2", "executionDevice": "cpu"},
            },
            "lw_training_bridge.install",
        )


def test_environment_observer_and_native_capture_support_mjlab_step_clock(tmp_path: Path, monkeypatch):
    import sys
    from types import SimpleNamespace

    import numpy as np

    from apps.node import environment_observer, native_observation_capture
    from apps.node.environment_observer import EnvironmentObserver
    from apps.node.native_observation_capture import NativeObservationCapture, source_identity
    from apps.node.observation_capture import code_hash

    class FakeTorchTensor:
        def __init__(self, arr, device="cpu"):
            self.arr = np.asarray(arr)
            self.device = device
            self.shape = self.arr.shape
            self.dtype = "torch.float32" if self.arr.dtype == np.float32 else str(self.arr.dtype)

        def __or__(self, other):
            return FakeTorchTensor(self.arr | other.arr, self.device)

        def __getitem__(self, idx):
            return FakeTorchTensor(self.arr[idx], self.device)

        def index_select(self, dim, index):
            return FakeTorchTensor(np.take(self.arr, index.arr, axis=dim), self.device)

        def detach(self):
            return self

        def to(self, **_):
            return self

        def tolist(self):
            return self.arr.tolist()

    fake_torch = SimpleNamespace(
        long="int64",
        tensor=lambda vals, dtype=None, device="cpu": FakeTorchTensor(
            np.asarray(vals, dtype=np.int64), device
        ),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    class MjlabStyleEnv:
        def __init__(self):
            self.num_envs = 2
            self.physics_dt = 0.005
            self._sim_step_counter = 0
            self.sim = SimpleNamespace()  # No get_physics_dt / get_physics_step_count on mjlab!
            self.q = 0.0

        def reset(self):
            self._sim_step_counter += 1
            return ({}, {})

        def step(self, action):
            self._sim_step_counter += 4
            self.q += 0.01
            done = FakeTorchTensor(np.array([False, False], dtype=bool))
            return ({}, None, done, done, {})

        def lw_policy_observation_state(self, indices):
            return [{"state": {"q": self.q + i}} for i in indices]

    class Normalizer:
        def __init__(self):
            self.hooks = []

        def register_forward_pre_hook(self, cb):
            self.hooks.append(cb)
            return SimpleNamespace(remove=lambda: self.hooks.remove(cb))

        def __call__(self, x):
            for cb in self.hooks:
                cb(self, (x,))
            return x

        def state_dict(self):
            return {"mean": FakeTorchTensor(np.zeros(2, dtype=np.float32))}

    class Policy:
        def __init__(self):
            self.obs_groups = ["actor"]
            self.obs_normalizer = Normalizer()

    class Alg:
        def __init__(self):
            self.policy = Policy()

        def get_policy(self):
            return self.policy

        def act(self, obs):
            self.policy.obs_normalizer(obs)
            return FakeTorchTensor(np.array([[0.1, -0.1], [0.2, -0.2]], dtype=np.float32))

    class Runner:
        def __init__(self, env):
            self.env = SimpleNamespace(unwrapped=env)
            self.alg = Alg()

        def export_policy_to_onnx(self, *_):
            pass

    mj_env = MjlabStyleEnv()
    runner = Runner(mj_env)
    pkg = tmp_path / "task"
    pkg.mkdir()
    (pkg / "obs_spec.yaml").write_text("schema: lab.policy_observation_spec/v1\n", encoding="utf-8")
    monkeypatch.setenv("LW_TASK_PACKAGE", str(pkg))

    profile = {
        "id": "mjlab-clock-fixture",
        "evidenceMode": "controlled",
        "collectorSha256": code_hash(native_observation_capture.__file__),
        "observerSha256": code_hash(environment_observer.__file__),
        "runner": source_identity(runner),
        "actor": source_identity(runner.alg.policy),
        "act": source_identity(runner.alg.act),
        "exporter": source_identity(runner.export_policy_to_onnx),
        "stateProvider": source_identity(mj_env.lw_policy_observation_state),
        "observationGroups": ["actor"],
        "environments": [0, 1],
        "boundary": "rsl-mlp-pre-normalizer",
        "normalization": "inside-exported-graph",
        "previousAction": "raw-native-algorithm-output",
        "previousActionReset": "zero-on-environment-epoch",
        "inputWidth": 2,
        "actionWidth": 2,
    }
    inputs = {
        "job": {
            "jobId": "job-mjlab-cap",
            "leaseId": "lease-mjlab-cap",
            "task": "task/mjlab-probe@1",
            "taskContentHash": "a" * 64,
            "scene": "robot-and-tubes@2",
            "sceneContentHash": "b" * 64,
            "seed": 7,
        },
        "task": {"observation": "obs_spec.yaml"},
    }
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with EnvironmentObserver(runner) as observer:
        assert observer.disabled is False
        assert observer.dt == pytest.approx(0.005)
        with NativeObservationCapture(runner, observer, profile, inputs) as capture:
            runner.alg.act(FakeTorchTensor(np.zeros((2, 2), dtype=np.float32)))
            for step_idx in range(140):
                runner.env.unwrapped.step(None)
                obs_vec = FakeTorchTensor(
                    np.array([[step_idx * 0.1, 1.0], [step_idx * 0.1 + 0.5, -1.0]], dtype=np.float32)
                )
                runner.alg.act(obs_vec)
            receipt = capture.publish(
                out_dir,
                {"checkpointSha256": "c" * 64, "policySha256": "d" * 64, "afterLearn": True, "iteration": 5},
                {"mjlab": "1.3.0"},
            )
            assert receipt is not None
            assert receipt["sampleCount"] == 256
            assert observer.sim_time > 0.0




class _CommandNativeEnvironment:
    def __init__(self):
        self.state = {"joints": {"/World/Joint": {"q": 0.0, "qd": 0.0}}}
        self.command: object = [0.4, -0.8]

    def lw_policy_observation_state(self, indices):
        return [{"state": self.state, "command": self.command} for _ in indices]


def test_native_capture_with_command_source_passes_evidence_verifier_and_qualification(
    tmp_path: Path, monkeypatch
):
    from types import SimpleNamespace

    from apps.node import native_observation_capture as native_mod
    from apps.node.observation_capture import code_hash
    from packages.library.archive import extract_package
    from packages.policies.native_evidence import NativeProfile
    from packages.policies.qualification import NativeProfiles
    from packages.training.inputs import pack_package
    from tests.fixtures.policy import artifact_archive, model_bytes, observation_spec, policy_task
    from tests.gateway.test_training_jobs import NODE, TOKEN
    from tests.unit import test_native_observation_capture as controlled

    registry = NativeProfiles([], allow_controlled=True)
    app = create_app(Settings(node_token=TOKEN), observation_profiles=registry)
    spec = observation_spec()
    spec["observations"][0]["keys"] = spec["action"]["jointKeys"] = ["/World/Joint"]
    spec["observations"].append(
        {
            "name": "velocity_command",
            "source": "command",
            "frame": "body",
            "unit": "m/s",
            "shape": [2],
            "offset": [0.0, 0.0],
            "scale": [1.0, 0.5],
            "lower": [-1.0, -2.0],
            "upper": [1.0, 2.0],
        }
    )

    task_doc = task_document("command-native-probe")
    task_doc["episode"]["commandEnvelope"] = [[-1.0, 1.0], [-2.0, 2.0]]
    task_pkg_dir = tmp_path / "task_pkg"
    task_pkg_dir.mkdir()
    policy_task(task_pkg_dir, spec=spec, task=task_doc)
    (task_pkg_dir / "native_provider.py").write_bytes(Path(__file__).read_bytes())
    monkeypatch.setenv("LW_TASK_PACKAGE", str(task_pkg_dir))

    # First verify malformed command payloads (bool, NaN, non-list) are rejected fail-closed
    bad_runner = controlled.Runner()
    bad_env = _CommandNativeEnvironment()
    bad_obs = SimpleNamespace(env=bad_env, selected=[0], epochs=[0], physics_count=0, disabled=False)
    bad_env.sim = SimpleNamespace(get_physics_step_count=lambda: bad_obs.physics_count)
    bad_profile = {
        "id": "bad-cmd-fixture",
        "evidenceMode": "controlled",
        "collectorSha256": code_hash(native_mod.__file__),
        "observerSha256": code_hash(native_mod.environment_observer.__file__),
        "runner": native_mod.source_identity(bad_runner),
        "actor": native_mod.source_identity(bad_runner.alg.actor),
        "act": native_mod.source_identity(bad_runner.alg.act),
        "exporter": native_mod.source_identity(bad_runner.export_policy_to_onnx),
        "stateProvider": native_mod.source_identity(bad_env.lw_policy_observation_state),
        "observationGroups": ["policy"],
        "environments": [0],
        "boundary": "rsl-mlp-pre-normalizer",
        "normalization": "inside-exported-graph",
        "previousAction": "raw-native-algorithm-output",
        "previousActionReset": "zero-on-environment-epoch",
        "inputWidth": 3,
        "actionWidth": 1,
    }
    bad_inputs = {
        "job": {
            "jobId": "job-bad-cmd",
            "leaseId": "lease-bad-cmd",
            "task": "task/command-native-probe@1",
            "taskContentHash": "a" * 64,
            "scene": "robot-and-tubes@2",
            "sceneContentHash": "b" * 64,
            "seed": 1,
        },
        "task": {"observation": "obs_spec.yaml"},
    }
    bad_env.command = [True, 0.1]
    with native_mod.NativeObservationCapture(bad_runner, bad_obs, bad_profile, bad_inputs) as bad_cap:
        bad_runner.alg.act(controlled.Tensor([[0.0, 0.0, 0.0]]))
        bad_obs.physics_count = 1
        bad_runner.alg.act(controlled.Tensor([[0.0, 0.0, 0.0]]))
    assert bad_cap.error is not None and not bad_cap.samples

    with TestClient(app) as client:
        pushed_task = client.post(
            "/api/v1/tasks",
            content=pack_package(task_pkg_dir),
            headers={"Content-Type": "application/x-tar"},
        )
        assert pushed_task.status_code == 201, pushed_task.text
        task_record = pushed_task.json()

        def make_artifacts(job):
            out = tmp_path / "cmd_output"
            out.mkdir()
            extract_package(io.BytesIO(artifact_archive(job, model=model_bytes(size=3))), out)
            versions = json.loads((out / "versions.json").read_bytes())
            versions["versions"]["rsl_rl"] = "5.4.1"
            (out / "versions.json").write_text(json.dumps(versions), encoding="utf-8")
            export = json.loads((out / "export.json").read_bytes())
            runner = controlled.Runner()
            env = _CommandNativeEnvironment()
            observer = SimpleNamespace(env=env, selected=[0], epochs=[0], physics_count=0, disabled=False)
            env.sim = SimpleNamespace(get_physics_step_count=lambda: observer.physics_count)
            prof = NativeProfile(
                id="command-native-input",
                evidenceMode="controlled",
                taskContentHash=job["taskContentHash"],
                stateProviderFile="native_provider.py",
                versions=versions["versions"],
                inputWidth=3,
                inputName="observations",
                actionWidth=1,
                collectorSha256=code_hash(native_mod.__file__),
                observerSha256=code_hash(native_mod.environment_observer.__file__),
                runner=native_mod.source_identity(runner),
                actor=native_mod.source_identity(runner.alg.actor),
                act=native_mod.source_identity(runner.alg.act),
                exporter=native_mod.source_identity(runner.export_policy_to_onnx),
                stateProvider=native_mod.source_identity(env.lw_policy_observation_state),
                observationGroups=["policy"],
                environments=[0],
                boundary="rsl-mlp-pre-normalizer",
                normalization="inside-exported-graph",
                previousAction="raw-native-algorithm-output",
                previousActionReset="zero-on-environment-epoch",
            )
            registry.profiles[prof.id] = prof
            with native_mod.NativeObservationCapture(
                runner,
                observer,
                prof.model_dump(mode="json"),
                {"job": job, "task": {"observation": "obs_spec.yaml"}},
            ) as cap:
                runner.alg.act(controlled.Tensor([[0.0, 0.0, 0.0]]))
                for idx in range(1, 257):
                    observer.physics_count = idx
                    q = idx / 25600.0
                    cx = (idx % 32) / 64.0
                    cy = -((idx % 16) / 16.0)
                    env.state["joints"]["/World/Joint"]["q"] = q
                    env.command = [cx, cy]
                    # scale=[2.0] on q, scale=[1.0, 0.5] on command
                    runner.alg.act(controlled.Tensor([[q * 2.0, cx * 1.0, cy * 0.5]]))
            assert cap.error is None
            receipt = cap.publish(out, export, versions)
            assert receipt is not None
            return pack_package(out)

        from tests.gateway.test_training_jobs import heartbeat

        plan_resp = client.post(
            "/api/v1/plans",
            json=training_plan(task_record["id"], budget={"gpuHours": 1}),
        )
        assert plan_resp.status_code == 201, plan_resp.text
        plan_rec = plan_resp.json()
        client.post(f"/api/v1/approvals/{plan_rec['approvalId']}/decision", json={"decision": "approve"})
        assert (
            client.post(
                "/api/v1/nodes/local/heartbeat",
                headers=NODE,
                json=heartbeat(plan_rec, task_record),
            ).status_code
            == 200
        )
        job = client.post("/api/v1/jobs", json={"planId": plan_rec["id"], "seed": 42}).json()
        tar_bytes = make_artifacts(job)
        up = client.post(
            f"/api/v1/jobs/{job['jobId']}/artifacts",
            content=tar_bytes,
            headers=NODE | {"X-Lease-Id": job["leaseId"], "Content-Type": "application/x-tar"},
        )
        assert up.status_code == 200, up.text
        done_hb = heartbeat(
            plan_rec,
            task_record,
            reports=[
                {
                    "jobId": job["jobId"],
                    "leaseId": job["leaseId"],
                    "status": "succeeded",
                    "elapsedSeconds": 1.0,
                    "logAfter": 0,
                    "logs": [],
                }
            ],
        )
        assert client.post("/api/v1/nodes/local/heartbeat", headers=NODE, json=done_hb).status_code == 200
        pol = client.post(
            "/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoints/model_0.pt"}
        )
        assert pol.status_code == 201, pol.text
        policy = pol.json()
        route = "/api/v1/policies/command-native-probe/1/observation-checks"
        chk = client.post(route, json={"artifact": "observations/native-samples.json"})
        assert chk.status_code == 201, chk.text
        check_id = chk.json()["id"]
        assert chk.json()["result"]["numericalPass"] is True

        ver = client.post(
            f"{route}/{check_id}/verify-source",
            json={"artifact": "observations/native-capture.json"},
        )
        assert ver.status_code == 200, ver.text
        assert ver.json()["sourceVerification"]["status"] == "native_input_verified"
        assert ver.json()["sourceVerification"]["qualified"] is True

        qual = app.state.policy_evidence.qualification(policy["id"], check_id)
        assert qual["qualified"] is True
        assert qual["samples"] == 256


def deepcopy_json(obj):
    return json.loads(json.dumps(obj))


def test_verify_against_observation_spec_supports_passive_joints_subsets_and_custom_order(tmp_path: Path):
    import math

    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    from apps.node.usd_mjcf import derive_usd_mjcf, verify_against_observation_spec

    usd_file = tmp_path / "passive_and_subset.usda"
    stage = Usd.Stage.CreateNew(str(usd_file))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/Asset").GetPrim()
    stage.SetDefaultPrim(root)
    for name in ("base", "link1", "passive_flap", "carriage"):
        prim = UsdGeom.Xform.Define(stage, f"/Asset/{name}").GetPrim()
        UsdPhysics.RigidBodyAPI.Apply(prim)
        m = UsdPhysics.MassAPI.Apply(prim)
        m.CreateMassAttr(1.0)
        m.CreateDiagonalInertiaAttr(Gf.Vec3f(0.01, 0.01, 0.01))

    j1 = UsdPhysics.RevoluteJoint.Define(stage, "/Asset/j_shoulder")
    j1.CreateAxisAttr(UsdGeom.Tokens.y)
    j1.CreateBody0Rel().SetTargets([Sdf.Path("/Asset/base")])
    j1.CreateBody1Rel().SetTargets([Sdf.Path("/Asset/link1")])
    j1.CreateLowerLimitAttr(-60.0)
    j1.CreateUpperLimitAttr(60.0)
    d1 = UsdPhysics.DriveAPI.Apply(j1.GetPrim(), "angular")
    d1.CreateTypeAttr("force")
    d1.CreateStiffnessAttr(1.0)
    d1.CreateDampingAttr(0.1)
    d1.CreateTargetPositionAttr(10.0)

    # Passive / unactuated auxiliary joint (no DriveAPI)
    jp = UsdPhysics.RevoluteJoint.Define(stage, "/Asset/j_passive")
    jp.CreateAxisAttr(UsdGeom.Tokens.x)
    jp.CreateBody0Rel().SetTargets([Sdf.Path("/Asset/link1")])
    jp.CreateBody1Rel().SetTargets([Sdf.Path("/Asset/passive_flap")])
    jp.CreateLowerLimitAttr(-30.0)
    jp.CreateUpperLimitAttr(30.0)

    j2 = UsdPhysics.PrismaticJoint.Define(stage, "/Asset/j_slider")
    j2.CreateAxisAttr(UsdGeom.Tokens.z)
    j2.CreateBody0Rel().SetTargets([Sdf.Path("/Asset/link1")])
    j2.CreateBody1Rel().SetTargets([Sdf.Path("/Asset/carriage")])
    j2.CreateLowerLimitAttr(-0.05)
    j2.CreateUpperLimitAttr(0.05)
    d2 = UsdPhysics.DriveAPI.Apply(j2.GetPrim(), "linear")
    d2.CreateTypeAttr("force")
    d2.CreateStiffnessAttr(250.0)
    d2.CreateDampingAttr(15.0)
    d2.CreateTargetPositionAttr(0.01)
    stage.GetRootLayer().Save()

    derived = derive_usd_mjcf(usd_file)
    assert derived.joint_names == ("j_shoulder", "j_passive", "j_slider")
    assert [j.actuated for j in derived.joints] == [True, False, True]

    # 1) Custom non-USD order for actuated joints + passive joint in observations
    custom_order_spec = {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "hinge_q_reversed",
                "source": "joint_position",
                "keys": ["/World/Lab/Bot/j_passive", "/World/Lab/Bot/j_shoulder"],
                "frame": "world",
                "unit": "rad",
                "shape": [2],
                "offset": [0.0, math.radians(10.0)],
                "scale": [1.0, 1.0],
            },
            {
                "name": "slide_qd",
                "source": "joint_velocity",
                "keys": ["/World/Lab/Bot/j_slider"],
                "frame": "world",
                "unit": "m/s",
                "shape": [1],
                "offset": [0.0],
                "scale": [1.0],
            },
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": ["/World/Lab/Bot/j_slider", "/World/Lab/Bot/j_shoulder"],
            "units": ["m", "rad"],
            "defaultPosition": [0.01, math.radians(10.0)],
            "scale": [1.0, 1.0],
            "lower": [-0.05, math.radians(-60.0)],
            "upper": [0.05, math.radians(60.0)],
        },
    }
    verify_against_observation_spec(
        derived,
        custom_order_spec,
        entity_key="/World/Lab/Bot",
        expected_kp={"j_shoulder": 180.0 / math.pi, "j_slider": 250.0},
        expected_kd={"j_shoulder": 0.1 * 180.0 / math.pi, "j_slider": 15.0},
        check_observation_offsets=True,
    )

    # 2) Controlled subset (only j_shoulder actuated by policy)
    subset_spec = deepcopy_json(custom_order_spec)
    subset_spec["action"] = {
        "space": "joint_position_offset",
        "jointKeys": ["/World/Lab/Bot/j_shoulder"],
        "units": ["rad"],
        "defaultPosition": [math.radians(10.0)],
        "scale": [1.0],
        "lower": [math.radians(-60.0)],
        "upper": [math.radians(60.0)],
    }
    verify_against_observation_spec(
        derived,
        subset_spec,
        entity_key="/World/Lab/Bot",
        expected_kp=[180.0 / math.pi],
        expected_kd=[0.1 * 180.0 / math.pi],
    )

    # 3) Passive joint in action.jointKeys is rejected
    passive_act_spec = deepcopy_json(subset_spec)
    passive_act_spec["action"]["jointKeys"] = ["/World/Lab/Bot/j_passive"]
    passive_act_spec["action"]["defaultPosition"] = [0.0]
    passive_act_spec["action"]["lower"] = [math.radians(-30.0)]
    passive_act_spec["action"]["upper"] = [math.radians(30.0)]
    with pytest.raises(ValueError, match="unactuated"):
        verify_against_observation_spec(derived, passive_act_spec, entity_key="/World/Lab/Bot")

    # 4) Duplicate keys in action or observations are rejected
    dup_obs_spec = deepcopy_json(subset_spec)
    dup_obs_spec["observations"][0]["keys"] = ["/World/Lab/Bot/j_shoulder", "/World/Lab/Bot/j_shoulder"]
    with pytest.raises(ValueError, match="duplicate"):
        verify_against_observation_spec(derived, dup_obs_spec, entity_key="/World/Lab/Bot")


def test_open_usd_stage_confines_paths_rewrites_nested_layers_and_fails_closed(
    tmp_path: Path, monkeypatch
):
    from apps.node.usd_mjcf import derive_usd_mjcf

    # Build a nested multi-layer asset:
    # scene/stage.usda -> library:assets/robot/nested-bot/1/asset.usda
    #                  -> ./parts/arm.usda -> library:assets/part/tip/1/asset.usda
    tip_pkg = tmp_path / "tip_pkg"
    tip_pkg.mkdir()
    (tip_pkg / "asset.usda").write_text(
        '#usda 1.0\n(defaultPrim = "Asset"\n metersPerUnit = 1\n upAxis = "Z")\n'
        'def Xform "Asset" {\n'
        '  def Xform "tip_site" {\n'
        '    double3 xformOp:translate = (0, 0, 0.08)\n'
        '    uniform token[] xformOpOrder = ["xformOp:translate"]\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )

    bot_pkg = tmp_path / "bot_pkg"
    (bot_pkg / "parts").mkdir(parents=True)
    arm_usda = (
        '#usda 1.0\n(defaultPrim = "Arm"\n metersPerUnit = 1\n upAxis = "Z")\n'
        'def Xform "Arm" {\n'
        '  def Xform "upper" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {\n'
        '    float physics:mass = 1.0\n'
        '    float3 physics:diagonalInertia = (0.01, 0.01, 0.01)\n'
        '    def Xform "tip_ref" (references = @library:assets/part/tip/1/asset.usda@</Asset>) {}\n'
        '  }\n'
        '  def PhysicsRevoluteJoint "elbow" (prepend apiSchemas = ["PhysicsDriveAPI:angular"]) {\n'
        '    rel physics:body0 = </Arm/base>\n'
        '    rel physics:body1 = </Arm/upper>\n'
        '    uniform token physics:axis = "Y"\n'
        '    float physics:lowerLimit = -90\n'
        '    float physics:upperLimit = 90\n'
        '    float drive:angular:physics:stiffness = 1.0\n'
        '    float drive:angular:physics:damping = 0.1\n'
        '  }\n'
        '}\n'
    )
    (bot_pkg / "parts" / "arm.usda").write_text(arm_usda, encoding="utf-8")
    (bot_pkg / "asset.usda").write_text(
        '#usda 1.0\n(defaultPrim = "Asset"\n metersPerUnit = 1\n upAxis = "Z")\n'
        'def Xform "Asset" (references = @./parts/arm.usda@</Arm>) {\n'
        '  def Xform "base" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {\n'
        '    float physics:mass = 2.0\n'
        '    float3 physics:diagonalInertia = (0.02, 0.02, 0.02)\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )
    scene_dir = tmp_path / "scene_nested"
    scene_dir.mkdir()
    scene_file = scene_dir / "stage.usda"
    scene_file.write_text(
        '#usda 1.0\n(defaultPrim = "World"\n metersPerUnit = 1\n upAxis = "Z")\n'
        'def Xform "World" {\n'
        '  def Xform "Lab" {\n'
        '    def Xform "Bot" (references = @library:assets/robot/nested-bot/1/asset.usda@</Asset>) {}\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )
    pkgs = {"robot/nested-bot@1": bot_pkg, "part/tip@1": tip_pkg}
    nested = derive_usd_mjcf(scene_file, prim_path="/World/Lab/Bot", asset_packages=pkgs)
    assert nested.joint_names == ("elbow",)
    assert "tip_site" in nested.sites

    # Path traversal, external host paths, and symlinks fail closed
    outside = tmp_path / "outside.usda"
    _write_test_articulation_usd(outside, fixed_base=True)

    for bad_ref in (
        "../outside.usda",
        str(outside.resolve()),
        "library:assets/robot/nested-bot/1/../../outside.usda",
        r"C:\outside.usda",
    ):
        bad_stage = tmp_path / f"bad_{ abs(hash(bad_ref)) }" / "stage.usda"
        bad_stage.parent.mkdir(parents=True, exist_ok=True)
        bad_stage.write_text(
            '#usda 1.0\n(defaultPrim = "Asset"\n metersPerUnit = 1\n upAxis = "Z")\n'
            f'def Xform "Asset" (references = @{bad_ref}@</Asset>) {{}}\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            derive_usd_mjcf(bad_stage, asset_packages=pkgs)

    symlink_pkg = tmp_path / "symlink_pkg"
    symlink_pkg.mkdir()
    (symlink_pkg / "evil.usda").symlink_to(outside)
    sym_scene = tmp_path / "sym_scene" / "stage.usda"
    sym_scene.parent.mkdir()
    sym_scene.write_text(
        '#usda 1.0\n(defaultPrim = "Asset"\n metersPerUnit = 1\n upAxis = "Z")\n'
        'def Xform "Asset" (references = @library:assets/robot/sym/1/evil.usda@</Asset>) {}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="symlink"):
        derive_usd_mjcf(sym_scene, asset_packages={"robot/sym@1": symlink_pkg})

    # Broken composition (missing file OR missing target prim) fails closed
    broken_prim_stage = tmp_path / "broken_prim" / "stage.usda"
    broken_prim_stage.parent.mkdir()
    broken_prim_stage.write_text(
        '#usda 1.0\n(defaultPrim = "World"\n metersPerUnit = 1\n upAxis = "Z")\n'
        'def Xform "World" {\n'
        '  def Xform "Bot" (\n'
        '    references = @library:assets/robot/nested-bot/1/asset.usda@</MissingPrim>\n'
        '  ) {\n'
        '    def Xform "Fallback" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {\n'
        '      float physics:mass = 1.0\n'
        '      float3 physics:diagonalInertia = (0.01, 0.01, 0.01)\n'
        '    }\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="composition"):
        derive_usd_mjcf(broken_prim_stage, prim_path="/World/Bot", asset_packages=pkgs)

    # Symlinked LW_SCENE_PACKAGE fails closed immediately
    sym_scene_root = tmp_path / "sym_scene_root"
    sym_scene_root.symlink_to(scene_dir)
    monkeypatch.setenv("LW_SCENE_PACKAGE", str(sym_scene_root))
    with pytest.raises(ValueError, match="LW_SCENE_PACKAGE"):
        derive_usd_mjcf(scene_file, prim_path="/World/Lab/Bot", asset_packages=pkgs)

    # Intermediate directory symlink inside LW_SCENE_PACKAGE or asset package fails closed
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    _write_test_articulation_usd(outside_dir / "escaped.usda", fixed_base=True)
    escape_scene_pkg = tmp_path / "escape_scene_pkg"
    escape_scene_pkg.mkdir()
    (escape_scene_pkg / "sub").symlink_to(outside_dir)
    monkeypatch.setenv("LW_SCENE_PACKAGE", str(escape_scene_pkg))
    with pytest.raises(ValueError, match="symlink"):
        derive_usd_mjcf(escape_scene_pkg / "sub" / "escaped.usda", asset_packages=pkgs)

    monkeypatch.setenv("LW_SCENE_PACKAGE", str(scene_dir))
    escape_asset_pkg = tmp_path / "escape_asset_pkg"
    escape_asset_pkg.mkdir()
    (escape_asset_pkg / "sub").symlink_to(outside_dir)
    with pytest.raises(ValueError, match="symlink"):
        derive_usd_mjcf(
            escape_asset_pkg / "sub" / "escaped.usda",
            asset_packages={"robot/esc@1": escape_asset_pkg},
        )

    # Coexistence of LW_SCENE_PACKAGE + LW_ASSET_PACKAGES on a subdirectory asset stage
    sub_asset_pkg = tmp_path / "sub_asset_pkg"
    (sub_asset_pkg / "usd").mkdir(parents=True)
    (sub_asset_pkg / "parts").mkdir(parents=True)
    (sub_asset_pkg / "parts" / "child.usda").write_text(
        '#usda 1.0\n(defaultPrim = "Child"\n metersPerUnit = 1\n upAxis = "Z")\n'
        'def Xform "Child" {\n'
        '  def Xform "ee_site" {}\n'
        '}\n',
        encoding="utf-8",
    )
    (sub_asset_pkg / "usd" / "bot.usda").write_text(
        '#usda 1.0\n(defaultPrim = "Asset"\n metersPerUnit = 1\n upAxis = "Z")\n'
        'def Xform "Asset" (prepend apiSchemas = ["PhysicsArticulationRootAPI"]) {\n'
        '  def Xform "base" (\n'
        '    prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]\n'
        '    references = @../parts/child.usda@</Child>\n'
        '  ) {\n'
        '    float physics:mass = 1.0\n'
        '    float3 physics:diagonalInertia = (0.01, 0.01, 0.01)\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LW_SCENE_PACKAGE", str(scene_dir))
    monkeypatch.setenv(
        "LW_ASSET_PACKAGES",
        json.dumps({"robot/sub-bot@1": str(sub_asset_pkg)}),
    )
    coexist = derive_usd_mjcf(sub_asset_pkg / "usd" / "bot.usda")
    assert "ee_site" in coexist.sites
    monkeypatch.delenv("LW_SCENE_PACKAGE", raising=False)
    monkeypatch.delenv("LW_ASSET_PACKAGES", raising=False)


def test_usd_mjcf_physics_fidelity_mass_units_drives_multiroot_reflection_and_sites(tmp_path: Path):
    import xml.etree.ElementTree as ET

    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    from apps.node.usd_mjcf import derive_usd_mjcf

    # 1) Authored physics:mass WITHOUT diagonalInertia on offset & multi-shape colliders
    mass_only_usda = (
        '#usda 1.0\n(defaultPrim = "Asset"\n metersPerUnit = 1\n upAxis = "Z")\n'
        'def Xform "Asset" (prepend apiSchemas = ["PhysicsArticulationRootAPI"]) {\n'
        '  def Xform "Carriage" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {\n'
        '    float physics:mass = 2.0\n'
        '    def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {\n'
        '      double size = 0.16\n'
        '    }\n'
        '  }\n'
        '  def Xform "Mast" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {\n'
        '    float physics:mass = 3.0\n'
        '    def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {\n'
        '      double size = 0.2\n'
        '      double3 xformOp:translate = (0, 0, 0.15)\n'
        '      uniform token[] xformOpOrder = ["xformOp:translate"]\n'
        '    }\n'
        '  }\n'
        '  def PhysicsRevoluteJoint "pitch" (prepend apiSchemas = ["PhysicsDriveAPI:angular"]) {\n'
        '    rel physics:body0 = </Asset/Carriage>\n'
        '    rel physics:body1 = </Asset/Mast>\n'
        '    uniform token physics:axis = "Y"\n'
        '    float physics:lowerLimit = -45\n'
        '    float physics:upperLimit = 45\n'
        '    float drive:angular:physics:stiffness = 1.5\n'
        '    float drive:angular:physics:damping = 0.3\n'
        '  }\n'
        '}\n'
    )
    mass_only_file = tmp_path / "mass_only.usda"
    mass_only_file.write_text(mass_only_usda, encoding="utf-8")
    derived_mass = derive_usd_mjcf(mass_only_file)
    xml_mass = ET.fromstring(derived_mass.mjcf_xml)
    carriage_inertial = xml_mass.find(".//body[@name='Carriage']/inertial")
    mast_inertial = xml_mass.find(".//body[@name='Mast']/inertial")
    assert carriage_inertial is not None and mast_inertial is not None
    assert float(carriage_inertial.attrib["mass"]) == pytest.approx(2.0)
    assert [float(x) for x in carriage_inertial.attrib["diaginertia"].split()] == pytest.approx(
        [0.008533333, 0.008533333, 0.008533333], rel=1e-5
    )
    assert float(mast_inertial.attrib["mass"]) == pytest.approx(3.0)
    assert [float(x) for x in mast_inertial.attrib["pos"].split()] == pytest.approx([0.0, 0.0, 0.15])
    assert [float(x) for x in mast_inertial.attrib["diaginertia"].split()] == pytest.approx(
        [0.02, 0.02, 0.02], rel=1e-5
    )

    # 1b) Rotated capsule with authored principalAxes=(1,0,0,0) and NO diagonalInertia keeps
    # Jacobi eigenbasis (quat != identity), while zero-norm principalAxes=(0,0,0,0) WITH
    # diagonalInertia falls back to identity.
    axes_edge_file = tmp_path / "axes_edge.usda"
    axes_edge_file.write_text(
        '#usda 1.0\n(defaultPrim = "Asset"\n metersPerUnit = 1\n upAxis = "Z")\n'
        'def Xform "Asset" (prepend apiSchemas = ["PhysicsArticulationRootAPI"]) {\n'
        '  def Xform "RotBody" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {\n'
        '    float physics:mass = 2.5\n'
        '    quatf physics:principalAxes = (1, 0, 0, 0)\n'
        '    def Capsule "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {\n'
        '      double radius = 0.04\n'
        '      double height = 0.30\n'
        '      quatf xformOp:orient = (0.8660254, 0.3535534, 0.3535534, 0)\n'
        '      uniform token[] xformOpOrder = ["xformOp:orient"]\n'
        '    }\n'
        '  }\n'
        '  def Xform "ZeroQuatBody" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {\n'
        '    float physics:mass = 1.0\n'
        '    float3 physics:diagonalInertia = (0.01, 0.02, 0.03)\n'
        '    quatf physics:principalAxes = (0, 0, 0, 0)\n'
        '  }\n'
        '  def PhysicsRevoluteJoint "j" {\n'
        '    rel physics:body0 = </Asset/RotBody>\n'
        '    rel physics:body1 = </Asset/ZeroQuatBody>\n'
        '    uniform token physics:axis = "Z"\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )
    xml_axes = ET.fromstring(derive_usd_mjcf(axes_edge_file).mjcf_xml)
    rot_in = xml_axes.find(".//body[@name='RotBody']/inertial")
    zero_in = xml_axes.find(".//body[@name='ZeroQuatBody']/inertial")
    assert rot_in is not None and zero_in is not None
    rot_q = [float(x) for x in rot_in.attrib["quat"].split()]
    assert rot_q != pytest.approx([1.0, 0.0, 0.0, 0.0], abs=1e-2)
    assert [float(x) for x in zero_in.attrib["quat"].split()] == pytest.approx([1.0, 0.0, 0.0, 0.0])

    # 2) Reject non-canonical metersPerUnit, kilogramsPerUnit, and acceleration DriveAPI
    bad_mpu = tmp_path / "bad_mpu.usda"
    bad_mpu.write_text(
        mass_only_usda.replace("metersPerUnit = 1", "metersPerUnit = 0.01"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="metersPerUnit"):
        derive_usd_mjcf(bad_mpu)

    bad_kpu = tmp_path / "bad_kpu.usda"
    bad_kpu.write_text(
        mass_only_usda.replace("metersPerUnit = 1", "metersPerUnit = 1\n kilogramsPerUnit = 0.001"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="kilogramsPerUnit"):
        derive_usd_mjcf(bad_kpu)

    bad_drive = tmp_path / "bad_drive.usda"
    bad_drive.write_text(
        mass_only_usda.replace(
            "float drive:angular:physics:stiffness = 1.5",
            'uniform token drive:angular:physics:type = "acceleration"\n'
            "    float drive:angular:physics:stiffness = 1.5",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="force"):
        derive_usd_mjcf(bad_drive)

    # 3) Reject multiple disconnected articulation roots under scope
    multi_root = tmp_path / "multi_root.usda"
    multi_root.write_text(
        '#usda 1.0\n(defaultPrim = "Asset"\n metersPerUnit = 1\n upAxis = "Z")\n'
        'def Xform "Asset" {\n'
        '  def Xform "BodyA" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {}\n'
        '  def Xform "BodyB" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {}\n'
        '}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Multiple"):
        derive_usd_mjcf(multi_root)

    # 4) Negative-determinant mirrored mesh & guide/non-leaf Xform sites
    mirror_file = tmp_path / "mirrored_and_sites.usda"
    m_stage = Usd.Stage.CreateNew(str(mirror_file))
    UsdGeom.SetStageUpAxis(m_stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(m_stage, 1.0)
    m_root = UsdGeom.Xform.Define(m_stage, "/Asset").GetPrim()
    m_stage.SetDefaultPrim(m_root)
    link = UsdGeom.Xform.Define(m_stage, "/Asset/link").GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(link)
    lm = UsdPhysics.MassAPI.Apply(link)
    lm.CreateMassAttr(1.0)
    lm.CreateDiagonalInertiaAttr(Gf.Vec3f(0.01, 0.01, 0.01))

    mirrored_mesh = UsdGeom.Mesh.Define(m_stage, "/Asset/link/left_finger_mesh")
    mirrored_mesh.AddScaleOp().Set(Gf.Vec3f(-1.0, 2.0, 1.0))
    # Right-handed tetrahedron before reflection
    mirrored_mesh.CreatePointsAttr(
        [
            Gf.Vec3f(0.0, 0.0, 0.0),
            Gf.Vec3f(0.05, 0.0, 0.0),
            Gf.Vec3f(0.0, 0.04, 0.0),
            Gf.Vec3f(0.0, 0.0, 0.06),
        ]
    )
    mirrored_mesh.CreateFaceVertexCountsAttr([3, 3, 3, 3])
    mirrored_mesh.CreateFaceVertexIndicesAttr([0, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3])
    UsdPhysics.CollisionAPI.Apply(mirrored_mesh.GetPrim())

    guide_sphere = UsdGeom.Sphere.Define(m_stage, "/Asset/link/grasp_guide")
    guide_sphere.CreatePurposeAttr(UsdGeom.Tokens.guide)
    guide_sphere.AddTranslateOp().Set(Gf.Vec3d(0.1, 0.0, 0.0))

    cam_mount = UsdGeom.Xform.Define(m_stage, "/Asset/link/camera_mount")
    cam_mount.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.2))
    UsdGeom.Camera.Define(m_stage, "/Asset/link/camera_mount/rgb_cam")
    m_stage.GetRootLayer().Save()

    derived_mirror = derive_usd_mjcf(mirror_file)
    assert "grasp_guide" in derived_mirror.sites
    assert "camera_mount" in derived_mirror.sites
    m_xml = ET.fromstring(derived_mirror.mjcf_xml)
    mesh_el = m_xml.find(".//asset/mesh")
    assert mesh_el is not None
    nums = [float(x) for x in mesh_el.attrib["vertex"].split()]
    verts = [(nums[i], nums[i + 1], nums[i + 2]) for i in range(0, len(nums), 3)]
    f_nums = [int(x) for x in mesh_el.attrib["face"].split()]
    faces = [(f_nums[i], f_nums[i + 1], f_nums[i + 2]) for i in range(0, len(f_nums), 3)]
    # Signed volume of reflected + rewound tetrahedron MUST be positive (> 0)
    signed_vol6 = 0.0
    for i0, i1, i2 in faces:
        a, b, c = verts[i0], verts[i1], verts[i2]
        signed_vol6 += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        )
    assert signed_vol6 > 0.0


def test_bridge_callback_enforces_static_onnx_and_subclass_learn_interception(
    tmp_path: Path, monkeypatch
):
    import sys
    from collections import deque
    from types import ModuleType, SimpleNamespace

    import numpy as np
    from onnx import TensorProto, helper, numpy_helper

    from apps.node import bridge_callback, mjlab_launcher

    out_dir = tmp_path / "output"
    work_dir = tmp_path / "work"
    (work_dir / "logs" / "exp1").mkdir(parents=True)
    monkeypatch.chdir(work_dir)

    inputs_path = tmp_path / "inputs.json"
    inputs_payload = {
        "job": {
            "jobId": "job-bridge-unit",
            "task": "task/mjlab-unit@1",
            "taskContentHash": "a" * 64,
            "scene": "scene-a@1",
            "sceneContentHash": "b" * 64,
            "seed": 11,
            "demonstrationSets": [],
        },
        "task": {"mjlab": {"task_id": "Lab-Unit-v0"}},
        "plan": {"algorithm": {"library": "rsl_rl", "name": "PPO", "version": "5.0.1"}},
        "hardware": {"device": "cpu", "gpu": None},
    }
    inputs_path.write_text(json.dumps(inputs_payload), encoding="utf-8")
    monkeypatch.setenv("LW_NODE_OUTPUT", str(out_dir))
    monkeypatch.setenv("LW_NODE_INPUTS", str(inputs_path))
    monkeypatch.delenv("LW_NODE_VIEW_IPC", raising=False)

    fake_versions = {
        "mjlab": "1.3.0",
        "mujoco": "4.0.1",
        "mujoco-warp": "3.8.1",
        "warp-lang": "1.12.0",
        "torch": "2.9.1",
        "rsl-rl-lib": "5.0.1",
    }
    monkeypatch.setattr(
        bridge_callback.importlib.metadata,
        "version",
        lambda dist: fake_versions[dist],
    )

    onnx_export_calls: list[dict] = []

    def fake_torch_onnx_export(*args, **kwargs):
        onnx_export_calls.append(dict(kwargs))
        # Write a single-file static ONNX graph at the target path (args[2] or kwargs["f"])
        f_target = kwargs.get("f", args[2] if len(args) > 2 else out_dir / "policy.onnx")
        w = numpy_helper.from_array(np.ones((2, 1), dtype=np.float32), "weight")
        m = helper.make_model(
            helper.make_graph(
                [helper.make_node("MatMul", ["obs", "weight"], ["actions"])],
                "static-onnx",
                [helper.make_tensor_value_info("obs", TensorProto.FLOAT, [1, 2])],
                [helper.make_tensor_value_info("actions", TensorProto.FLOAT, [1, 1])],
                [w],
            ),
            opset_imports=[helper.make_opsetid("", 18)],
        )
        m.ir_version = 10
        Path(f_target).write_bytes(m.SerializeToString())

    fake_torch_mod = ModuleType("torch")
    fake_torch_onnx_mod = ModuleType("torch.onnx")
    fake_torch_onnx_mod.export = fake_torch_onnx_export
    fake_torch_mod.onnx = fake_torch_onnx_mod
    seed_events: list[tuple[str, int]] = []
    fake_torch_mod.manual_seed = lambda s: seed_events.append(("torch", s))
    fake_torch_mod.cuda = SimpleNamespace(is_available=lambda: False, manual_seed_all=lambda s: None)
    monkeypatch.setitem(sys.modules, "torch", fake_torch_mod)
    monkeypatch.setitem(sys.modules, "torch.onnx", fake_torch_onnx_mod)

    class FakeOnPolicyRunner:
        def __init__(self, log_dir: Path):
            self.current_learning_iteration = 0
            self.env = None
            self.logger = SimpleNamespace(
                log_dir=str(log_dir),
                rewbuffer=deque([1.5, 2.5]),
                log=lambda *a, **kw: None,
            )

        def learn(self, num_learning_iterations=1, **_):
            for it in range(num_learning_iterations):
                self.current_learning_iteration = it
                self.logger.log(it=it)
            (Path(self.logger.log_dir) / f"model_{self.current_learning_iteration}.pt").write_bytes(
                b"pt-ckpt"
            )

        def export_policy_to_onnx(self, path: str, filename: str = "policy.onnx"):
            # Deliberately pass dynamo=True and dynamic_axes to prove bridge_callback overrides both!
            fake_torch_onnx_mod.export(
                None,
                None,
                str(Path(path) / filename),
                dynamo=True,
                dynamic_axes={"obs": {0: "batch"}, "actions": {0: "batch"}},
            )

    rsl_pkg = ModuleType("rsl_rl")
    rsl_runners = ModuleType("rsl_rl.runners")
    rsl_runners.OnPolicyRunner = FakeOnPolicyRunner
    rsl_pkg.runners = rsl_runners
    monkeypatch.setitem(sys.modules, "rsl_rl", rsl_pkg)
    monkeypatch.setitem(sys.modules, "rsl_rl.runners", rsl_runners)

    bridge_callback.install()
    versions_doc = json.loads((out_dir / "versions.json").read_text(encoding="utf-8"))
    assert versions_doc["versions"] == {
        "mjlab": "1.3.0",
        "mujoco": "4.0.1",
        "mujoco_warp": "3.8.1",
        "warp": "1.12.0",
        "torch": "2.9.1",
        "rsl_rl": "5.0.1",
    }

    # Subclass overriding learn WITHOUT calling super().learn is still intercepted once bridged
    class CustomOverrideRunner(FakeOnPolicyRunner):
        def learn(self, num_learning_iterations=1, **_):
            self.current_learning_iteration = num_learning_iterations - 1
            self.logger.log(it=self.current_learning_iteration)
            (Path(self.logger.log_dir) / f"model_{self.current_learning_iteration}.pt").write_bytes(
                b"custom-ckpt"
            )
            return "custom-done"

    bridge_callback.ensure_runner_bridged(CustomOverrideRunner)
    custom = CustomOverrideRunner(work_dir / "logs" / "exp1")
    assert custom.learn(num_learning_iterations=2) == "custom-done"
    assert len(onnx_export_calls) == 1
    assert onnx_export_calls[0]["dynamo"] is False
    assert onnx_export_calls[0]["dynamic_axes"] == {}
    export_doc = json.loads((out_dir / "export.json").read_text(encoding="utf-8"))
    assert export_doc["externalData"] == []
    assert export_doc["afterLearn"] is True
    assert export_doc["iteration"] == 1
    metrics_lines = (out_dir / "metrics.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(metrics_lines) == 1

    # Also verify RNG seeding order in mjlab_launcher._seed_everything
    mjlab_launcher._seed_everything(99, "cpu")
    assert ("torch", 99) in seed_events
