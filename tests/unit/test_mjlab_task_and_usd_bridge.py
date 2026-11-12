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
    assert dumped_mjlab["mjlab"]["presets"] == []

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
    with pytest.raises(ValueError, match="jointKeys"):
        verify_against_observation_spec(fixed, swapped_spec)

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
    task = _mjlab_task_document("mjlab-args", physics_dt=0.005, decimation=4, presets=["flat"])
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


