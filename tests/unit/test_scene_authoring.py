"""Pure Scene Factory lane: plans, canonical packages, layout and repair receipts."""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from pxr import Sdf, Usd, UsdGeom, UsdPhysics, UsdUtils

from factory.authoring import (
    BUILTIN_LIBRARY,
    FactoryError,
    PlanError,
    _mesh_sha256,
    authoring_request,
    build,
    load_plan,
    plan_digest,
    request_digest,
)
from factory.procedural import package_digest, rack_geometry, rack_usd
from packages.library.metadata import asset_metadata, scene_metadata

REPO_ROOT = Path(__file__).resolve().parents[2]
PLANS = REPO_ROOT / "factory/plans"
FIXTURE_TABLE = REPO_ROOT / "examples/usd-library/assets/usd-fixture-table"


@pytest.mark.parametrize("collision", ["solid", "holes"])
def test_usd_collision_boxes_keep_their_design_centers_and_sizes(collision):
    params = plan_document("tube-rack-demo-v1.yaml")["procedural"][0]["rack"]
    params["collision"] = collision
    stage = Usd.Stage.CreateInMemory()
    stage.GetRootLayer().ImportFromString(rack_usd(params))
    for box in rack_geometry(params).collision:
        prim = stage.GetPrimAtPath("/Asset/" + box.name)
        transform = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
        assert tuple(transform.ExtractTranslation()) == pytest.approx(box.center)
        bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default"]).ComputeWorldBound(prim)
        assert tuple(bounds.ComputeAlignedRange().GetSize()) == pytest.approx(box.size)


def library_with_box(tmp_path):
    root = tmp_path / "library"
    destination = root / "assets/environment/cardboard-box/1"
    shutil.copytree(FIXTURE_TABLE, destination)
    metadata = yaml.safe_load((destination / "asset.yaml").read_text(encoding="utf-8"))
    metadata.update(
        id="environment/cardboard-box",
        kind="environment",
        title="Fixture cardboard box",
        description="Test-only trusted prop; the real evidence uses the SF2 acquisition.",
    )
    (destination / "asset.yaml").write_text(
        yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8"
    )
    return root


def plan_document(name):
    return yaml.safe_load((PLANS / name).read_text(encoding="utf-8"))


def write_plan(tmp_path, document, name="plan.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def build_plan(tmp_path, name, out, library, layout=None):
    plan = load_plan(PLANS / name)
    return build(plan, out, library_roots=[library], layout=layout)


def stage_of(scene_path, roots=()):
    layer = Sdf.Layer.FindOrOpen(str(Path(scene_path) / "stage.usda"))
    clone = Sdf.Layer.CreateAnonymous(".usda")
    clone.TransferContent(layer)

    def resolve(reference):
        if not reference.startswith("library:"):
            return reference
        relative = Path(reference.removeprefix("library:"))
        for root in roots:
            candidate = Path(root) / relative
            if candidate.is_file():
                return str(candidate)
        return reference

    UsdUtils.ModifyAssetPaths(clone, resolve)
    return Usd.Stage.Open(clone)


def digest_dir(path):
    root = Path(path)
    return package_digest(
        {
            entry.relative_to(root).as_posix(): entry.read_bytes()
            for entry in root.rglob("*")
            if entry.is_file()
        }
    )


def test_plan_rejects_guessed_versions_and_wrong_units(tmp_path):
    document = plan_document("tube-rack-demo-v1.yaml")
    del document["assets"][0]["version"]
    with pytest.raises(PlanError, match="version"):
        load_plan(write_plan(tmp_path, document, "missing.yaml"))

    document = plan_document("tube-rack-demo-v1.yaml")
    document["instances"][0]["asset"] = "environment/cardboard-box@latest"
    with pytest.raises(PlanError, match="exact"):
        load_plan(write_plan(tmp_path, document, "latest.yaml"))

    document = plan_document("tube-rack-demo-v1.yaml")
    document["units"]["length"] = "cm"
    with pytest.raises(PlanError, match="length"):
        load_plan(write_plan(tmp_path, document, "units.yaml"))

    library = library_with_box(tmp_path)
    document = plan_document("tube-rack-demo-v1.yaml")
    document["assets"][0]["id"] = "environment/not-installed"
    document["instances"][0]["asset"] = "environment/not-installed@1"
    plan = load_plan(write_plan(tmp_path, document, "unknown.yaml"))
    with pytest.raises(PlanError, match="not installed"):
        build(plan, tmp_path / "unknown", library_roots=[library])


def test_canonical_packages_are_stable_and_preserve_references(tmp_path):
    library = library_with_box(tmp_path)
    out = tmp_path / "v1"
    result = build_plan(tmp_path, "tube-rack-demo-v1.yaml", out, library)
    scene = out / "scenes/tube-rack-demo"
    for name in ("scene.yaml", "stage.usda", "devices.yaml", "locations.yaml", "factory.json"):
        assert (scene / name).is_file()
    assert (out / "manifest.json").is_file()
    scene_metadata(scene)
    for package in sorted((out / "assets").glob("*/*/*")):
        asset_metadata(package)

    stage = stage_of(scene, [out, library])
    assert UsdGeom.GetStageUpAxis(stage) == "Z"
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1
    box = stage.GetPrimAtPath("/World/Lab/Box")
    assert box.GetAttribute("xformOp:translate").Get() == (0.65, 0.0, 0.75)
    assert box.GetAttribute("physics:kinematicEnabled").Get() is True
    assert "library:assets/environment/cardboard-box/1/asset.usda" in (
        scene / "stage.usda"
    ).read_text(encoding="utf-8")
    rack = stage.GetPrimAtPath("/World/Lab/Rack")
    assert rack and [child.GetPath().name for child in rack.GetChildren()].count("HoleA1") == 1
    bench = stage.GetPrimAtPath("/World/Dressing/Bench")
    assert bench.IsValid()
    assert tuple(UsdGeom.XformCache().GetLocalToWorldTransform(bench).ExtractTranslation()) == (
        0.15, 0.0, 0.725
    )
    assert not list(out.rglob("*.usdc"))
    assert list(out.rglob("visual.glb"))

    devices = yaml.safe_load((scene / "devices.yaml").read_text(encoding="utf-8"))
    assert [entity["key"] for entity in devices["entities"]] == ["/World/Lab/Tubes/Tube01"]
    recipe = json.loads((scene / "factory.json").read_text(encoding="utf-8"))
    assert recipe["planSha256"] == plan_digest(load_plan(PLANS / "tube-rack-demo-v1.yaml"))
    assert {item["id"] for item in result["assets"]} == {
        "environment/cardboard-box@1",
        "environment/six-hole-tube-rack@1",
        "labware/sf3-test-tube@1",
    }

    before = (scene / "stage.usda").read_bytes()
    build_plan(tmp_path, "tube-rack-demo-v1.yaml", out, library)
    assert (scene / "stage.usda").read_bytes() == before


def valid_layout(plan, request):
    return {
        "schema": "lw.scene-layout/1",
        "scene": plan.scene.name,
        "planSha256": plan_digest(plan),
        "requestSha256": request_digest(request),
        "mode": "human-edited",
        "blender": {"version": "4.5.9", "blendSha256": "a" * 64},
        "instances": [
            {
                "instanceId": item["instanceId"],
                "entityKey": item["entityKey"],
                "positionM": item["positionM"],
                "rotationDeg": item["rotationDeg"],
            }
            for item in request["instances"]
        ],
        "procedural": [
            {
                "instanceId": item["instanceId"],
                "type": item["type"],
                "dimensionsM": item["dimensionsM"],
                "localBoundsM": item["localBoundsM"],
                "meshSha256": item["meshSha256"],
                "holeCount": item.get("holeCount"),
                "collision": item.get("collision"),
            }
            for item in request["instances"]
            if item["kind"] == "procedural"
        ],
    }


def test_layout_manifest_drives_pose_and_rejects_drift(tmp_path):
    library = library_with_box(tmp_path)
    plan = load_plan(PLANS / "tube-rack-demo-v1.yaml")
    request = authoring_request(plan, library_roots=[library])
    layout = valid_layout(plan, request)
    moved = json.loads(json.dumps(layout))
    moved["instances"][0]["positionM"] = [0.7, 0.0, 0.75]
    out = tmp_path / "moved"
    result = build(plan, out, library_roots=[library], layout=moved)
    stage = stage_of(out / "scenes/tube-rack-demo", [out, library])
    assert stage.GetPrimAtPath("/World/Lab/Box").GetAttribute("xformOp:translate").Get() == (
        0.7,
        0.0,
        0.75,
    )
    assert result["sceneContentHash"]

    cases = []
    missing = json.loads(json.dumps(layout))
    missing["instances"] = missing["instances"][:-1]
    cases.append(("missing", missing, "missing"))
    renamed = json.loads(json.dumps(layout))
    renamed["instances"][0]["entityKey"] = "/World/Lab/Other"
    cases.append(("renamed", renamed, "entityKey"))
    stale = json.loads(json.dumps(layout))
    stale["planSha256"] = "0" * 64
    cases.append(("stale", stale, "another plan"))
    no_request = json.loads(json.dumps(layout))
    del no_request["requestSha256"]
    cases.append(("no-request", no_request, "requestSha256"))
    wrong_request = json.loads(json.dumps(layout))
    wrong_request["requestSha256"] = "0" * 64
    cases.append(("wrong-request", wrong_request, "different Blender authoring request"))
    no_provenance = json.loads(json.dumps(layout))
    del no_provenance["blender"]
    cases.append(("no-provenance", no_provenance, "provenance"))
    wrong_version = json.loads(json.dumps(layout))
    wrong_version["blender"]["version"] = "4.4.0"
    cases.append(("wrong-version", wrong_version, "Blender 4.5.9"))
    no_mode = json.loads(json.dumps(layout))
    del no_mode["mode"]
    cases.append(("no-mode", no_mode, "mode"))
    no_rotation = json.loads(json.dumps(layout))
    del no_rotation["instances"][0]["rotationDeg"]
    cases.append(("no-rotation", no_rotation, "rotationDeg"))
    no_mesh = json.loads(json.dumps(layout))
    del no_mesh["procedural"][0]["meshSha256"]
    cases.append(("no-mesh", no_mesh, "mesh differs"))
    changed_mesh = json.loads(json.dumps(layout))
    changed_mesh["procedural"][0]["meshSha256"] = "0" * 64
    cases.append(("changed-mesh", changed_mesh, "mesh differs"))
    wrong_dimensions = json.loads(json.dumps(layout))
    wrong_dimensions["procedural"][0]["dimensionsM"][0] += 0.01
    cases.append(("dimensions", wrong_dimensions, "dimensions"))
    wrong_holes = json.loads(json.dumps(layout))
    wrong_holes["procedural"][0]["holeCount"] = 5
    cases.append(("holes", wrong_holes, "hole count"))
    wrong_collision = json.loads(json.dumps(layout))
    wrong_collision["procedural"][0]["collision"] = "holes"
    cases.append(("collision", wrong_collision, "collision mode"))
    for name, document, message in cases:
        try:
            build(plan, tmp_path / name, library_roots=[library], layout=document)
        except PlanError as error:
            assert message in str(error), (name, str(error))
            continue
        raise AssertionError(f"{name} did not raise PlanError")


def test_procedural_mesh_digest_binds_vertex_order():
    mesh = {
        "vertices": [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.0, 0.1, 0.0]],
        "faces": [[0, 1, 2]],
    }
    swapped = json.loads(json.dumps(mesh))
    swapped["vertices"][0], swapped["vertices"][1] = (
        swapped["vertices"][1],
        swapped["vertices"][0],
    )
    assert _mesh_sha256(mesh) != _mesh_sha256(swapped)


def test_scene_package_is_not_replaced_by_another_plan(tmp_path):
    library = library_with_box(tmp_path)
    out = tmp_path / "shared"
    build_plan(tmp_path, "tube-rack-demo-v1.yaml", out, library)
    scene = out / "scenes/tube-rack-demo"
    before = {path.name: path.read_bytes() for path in scene.iterdir()}
    with pytest.raises(FactoryError, match="immutable factory package"):
        build_plan(tmp_path, "tube-rack-demo-v2.yaml", out, library)
    assert {path.name: path.read_bytes() for path in scene.iterdir()} == before
    assert not (out / "assets/environment/six-hole-tube-rack/2").exists()


def test_trusted_stage_must_stay_inside_its_package(tmp_path):
    library = library_with_box(tmp_path)
    package = library / "assets/environment/cardboard-box/1"
    metadata = yaml.safe_load((package / "asset.yaml").read_text(encoding="utf-8"))
    metadata["stage"] = "../escape.usda"
    (package / "asset.yaml").write_text(
        yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8"
    )
    (package.parent / "escape.usda").write_text("#usda 1.0\n", encoding="utf-8")
    with pytest.raises(PlanError, match="package-local|outside its package"):
        build_plan(tmp_path, "tube-rack-demo-v1.yaml", tmp_path / "escape", library)


def test_rack_repair_changes_collision_and_keeps_visual(tmp_path):
    library = library_with_box(tmp_path)
    v1 = tmp_path / "v1"
    v2 = tmp_path / "v2"
    build_plan(tmp_path, "tube-rack-demo-v1.yaml", v1, library)
    build_plan(tmp_path, "tube-rack-demo-v2.yaml", v2, library)
    solid = v1 / "assets/environment/six-hole-tube-rack/1"
    holes = v2 / "assets/environment/six-hole-tube-rack/2"
    assert (solid / "visual.glb").read_bytes() == (holes / "visual.glb").read_bytes()
    assert (solid / "asset.usda").read_bytes() != (holes / "asset.usda").read_bytes()
    assert package_digest(
        {path.relative_to(solid).as_posix(): path.read_bytes() for path in solid.rglob("*") if path.is_file()}
    ) != package_digest(
        {path.relative_to(holes).as_posix(): path.read_bytes() for path in holes.rglob("*") if path.is_file()}
    )
    solid_stage = Usd.Stage.Open(str(solid / "asset.usda"))
    solid_colliders = [
        prim for prim in solid_stage.Traverse() if prim.HasAPI(UsdPhysics.CollisionAPI)
    ]
    assert [prim.GetName() for prim in solid_colliders] == ["Collision"]
    scale = solid_stage.GetPrimAtPath("/Asset/Collision").GetAttribute("xformOp:scale").Get()
    assert tuple(scale) == pytest.approx((0.13, 0.09, 0.042))
    hole_stage = Usd.Stage.Open(str(holes / "asset.usda"))
    hole_colliders = [prim for prim in hole_stage.Traverse() if prim.HasAPI(UsdPhysics.CollisionAPI)]
    assert len(hole_colliders) > 1
    assert "Collision" not in {prim.GetName() for prim in hole_colliders}
    assert len([prim for prim in hole_stage.Traverse() if prim.GetName().startswith("Hole")]) == 6
    geometry = rack_geometry(load_plan(PLANS / "tube-rack-demo-v2.yaml").procedural[0].rack.model_dump())
    assert len(geometry.sites) == 6


def test_second_scene_reuses_the_repaired_rack_version(tmp_path):
    library = library_with_box(tmp_path)
    v2 = tmp_path / "v2"
    reuse = tmp_path / "reuse"
    repaired = build_plan(tmp_path, "tube-rack-demo-v2.yaml", v2, library)
    reused = build_plan(tmp_path, "tube-rack-reuse.yaml", reuse, library)
    stage = stage_of(reuse / "scenes/tube-rack-reuse", [reuse])
    assert stage.GetPrimAtPath("/World/Lab/Rack")
    text = (reuse / "scenes/tube-rack-reuse/stage.usda").read_text(encoding="utf-8")
    assert "library:assets/environment/six-hole-tube-rack/2/asset.usda" in text
    rack_hash = next(
        item["contentHash"] for item in repaired["assets"] if item["id"].endswith("tube-rack@2")
    )
    assert next(
        item["contentHash"] for item in reused["assets"] if item["id"].endswith("tube-rack@2")
    ) == rack_hash
    assert not any(item["id"] == "environment/cardboard-box@1" for item in reused["assets"])
    metadata = yaml.safe_load(
        (reuse / "scenes/tube-rack-reuse/scene.yaml").read_text(encoding="utf-8")
    )
    assert metadata["name"] == "tube-rack-reuse"


def test_robot_is_referenced_with_scene_initial_joint_state(tmp_path):
    document = plan_document("tube-rack-demo-v1.yaml")
    document["scene"] = {
        "name": "robot-reference",
        "title": "Robot reference composition",
        "description": "Trusted FR3 retained by package reference; no physics claim.",
        "sources": ["newton"],
        "simulation": {"solver": "xpbd", "warmupSteps": 1},
        "locations": {"robot": "/World/Lab/RobotA", "locations": {}},
        "dressing": [],
    }
    document["assets"] = [{"id": "robot/fr3", "version": 1}]
    document["procedural"] = []
    document["instances"] = [
        {
            "instanceId": "robot",
            "entityKey": "/World/Lab/RobotA",
            "asset": "robot/fr3@1",
            "transform": {"positionM": [-0.45, 0.0, 0.75], "rotationDeg": [0.0, 0.0, 0.0]},
            "initialJointPositions": {
                "fr3_joint4": -1.5707963267948966,
                "fr3_joint6": 1.5707963267948966,
            },
            "binding": {
                "template": "robot/fr3@1",
                "config": {"locations": "locations.yaml", "tubes": []},
            },
        }
    ]
    plan = load_plan(write_plan(tmp_path, document, "robot.yaml"))
    out = tmp_path / "robot"
    result = build(plan, out, library_roots=[BUILTIN_LIBRARY])
    stage = stage_of(out / "scenes/robot-reference", [out, BUILTIN_LIBRARY])
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    position = UsdGeom.XformCache().GetLocalToWorldTransform(
        stage.GetPrimAtPath("/World/Lab/RobotA")
    ).ExtractTranslation()
    assert tuple(position) == pytest.approx((-0.45, 0.0, 0.75))
    assert "library:assets/robot/fr3/1/asset.usdc" in (
        out / "scenes/robot-reference/stage.usda"
    ).read_text(encoding="utf-8")
    assert not list(out.rglob("*.usdc"))
    assert stage.GetPrimAtPath("/World/Lab/RobotA/fr3_joint4").GetAttribute(
        "newton:angular:position"
    ).Get() == pytest.approx(-90.0)
    assert stage.GetPrimAtPath("/World/Lab/RobotA/fr3_joint6").GetAttribute(
        "newton:angular:position"
    ).Get() == pytest.approx(90.0)
    link = stage.GetPrimAtPath("/World/Lab/RobotA/fr3_link0")
    assert link.GetAttribute("physics:mass").Get() == pytest.approx(2.3966000080108643)
    assert link.GetAttribute("physics:diagonalInertia").Get() == pytest.approx(
        (0.0067344354, 0.010765565, 0.0115)
    )
    assert stage.GetPrimAtPath("/World/Lab/RobotA/fr3_base_joint").IsA(
        UsdPhysics.FixedJoint
    )
    assert stage.GetPrimAtPath("/World/Lab/RobotA/fr3_joint1").IsA(
        UsdPhysics.RevoluteJoint
    )
    robot = next(item for item in result["assets"] if item["id"] == "robot/fr3@1")
    assert robot["source"] == "library"
    assert robot["contentHash"] == digest_dir(Path(BUILTIN_LIBRARY) / "assets/robot/fr3/1")


def test_reference_closure_rejects_bad_seats_and_unknown_sites(tmp_path):
    document = plan_document("tube-rack-demo-v1.yaml")
    document["instances"][2]["transform"]["positionM"] = [0.2, -0.0175, 0.756]
    with pytest.raises(PlanError, match="seated instance"):
        load_plan(write_plan(tmp_path, document, "seat.yaml"))

    document = plan_document("tube-rack-demo-v1.yaml")
    document["instances"][2]["binding"]["config"]["locationSites"].append(
        "/World/Lab/Rack/HoleZ9"
    )
    with pytest.raises(PlanError, match="unknown Sites"):
        load_plan(write_plan(tmp_path, document, "site.yaml"))

    document = plan_document("tube-rack-demo-v1.yaml")
    document["instances"][1]["entityKey"] = document["instances"][2]["entityKey"]
    with pytest.raises(PlanError, match="duplicate entityKey"):
        load_plan(write_plan(tmp_path, document, "duplicate.yaml"))

    document = plan_document("tube-rack-demo-v1.yaml")
    document["instances"][1]["entityKey"] = "/World/Lab/Tubes"
    with pytest.raises(PlanError, match="nested"):
        load_plan(write_plan(tmp_path, document, "nested.yaml"))


def test_authoring_request_and_immutable_versions(tmp_path):
    library = library_with_box(tmp_path)
    plan = load_plan(PLANS / "tube-rack-demo-v1.yaml")
    request = authoring_request(plan, library_roots=[library])
    assert request_digest(request) == request_digest(json.loads(json.dumps(request)))
    box = next(item for item in request["instances"] if item["instanceId"] == "box")
    assert box["kind"] == "trusted"
    assert Path(box["stagePath"]).as_posix().endswith("environment/cardboard-box/1/asset.usda")
    rack = next(item for item in request["instances"] if item["instanceId"] == "rack")
    assert rack["kind"] == "procedural"
    assert rack["holeCount"] == 6
    assert rack["visualMeshSha256"]
    assert len(rack["visualMesh"]["faces"]) > 0
    assert request["dressing"][0]["name"] == "Bench"
    assert request["libraryRoots"] == [str(library.resolve())]

    out = tmp_path / "immutable"
    build(plan, out, library_roots=[library])
    document = plan_document("tube-rack-demo-v1.yaml")
    document["procedural"][0]["rack"]["holeDiameterM"] = 0.021
    changed = load_plan(write_plan(tmp_path, document, "changed.yaml"))
    with pytest.raises(FactoryError, match="immutable"):
        build(changed, out, library_roots=[library])


def test_manifest_with_platform_line_endings_does_not_block_a_rebuild(tmp_path):
    library = library_with_box(tmp_path)
    plan = load_plan(PLANS / "tube-rack-demo-v1.yaml")
    out = tmp_path / "line-endings"
    build(plan, out, library_roots=[library])

    manifest = out / "manifest.json"
    translated = manifest.read_bytes().replace(b"\n", b"\r\n")
    manifest.write_bytes(translated)

    build(plan, out, library_roots=[library])

    assert manifest.read_bytes() == translated


def test_manifest_differences_beyond_line_endings_still_conflict(tmp_path):
    library = library_with_box(tmp_path)
    plan = load_plan(PLANS / "tube-rack-demo-v1.yaml")
    out = tmp_path / "manifest-drift"
    build(plan, out, library_roots=[library])

    manifest = out / "manifest.json"
    canonical = manifest.read_bytes()
    document = json.loads(canonical)
    drifted = dict(document, gaps=["unexpected"])
    for variant in (
        json.dumps(document, indent=4, sort_keys=True) + "\n",
        json.dumps(drifted, indent=2, sort_keys=True) + "\n",
        canonical.decode("utf-8") + "\n",
        canonical[:-1].decode("utf-8"),
    ):
        manifest.write_bytes(variant.encode("utf-8"))
        with pytest.raises(FactoryError, match="immutable factory manifest"):
            build(plan, out, library_roots=[library])


def test_authoring_request_names_every_library_root(tmp_path):
    library = library_with_box(tmp_path)
    plan = load_plan(PLANS / "tube-rack-demo-v1.yaml")
    request = authoring_request(plan, library_roots=[library, BUILTIN_LIBRARY])
    assert request["libraryRoots"] == [
        str(library.resolve()), str(BUILTIN_LIBRARY.resolve()),
    ]
    assert request["libraryRoot"] == str(library.resolve())
