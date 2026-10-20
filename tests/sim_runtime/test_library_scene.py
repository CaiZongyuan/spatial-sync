"""Actual CPU model/Manifest consumers for authored USD library packages."""

import shutil
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
import yaml

newton = pytest.importorskip("newton")
pytest.importorskip("pxr.Usd")

from apps.api.app.library_validation import program_schemas
from packages.library.assets import AssetLibrary
from packages.library.common import TYPE_ROOT, document
from packages.library.physics import initial_joint_checks, taught_checks
from packages.library.physics import simulation as physics_simulation
from packages.library.physics import validate as validate_physics
from packages.library.scenes import SceneLibrary
from packages.library.structural import validate
from packages.library.validation import StructuralValidator
from packages.protocol.internal import SessionOptions
from packages.sim_runtime.info_host import NewtonInfoHost
from packages.sim_runtime.library_scene import prepare_scene_config
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.simulation import NewtonSimulation

pytestmark = pytest.mark.newton
FIXTURE = Path(__file__).resolve().parents[2] / "examples/usd-library"


def authored_scope(tmp_path):
    root = tmp_path / "library"
    for source in (FIXTURE / "assets").iterdir():
        metadata = document(source / "asset.yaml")
        shutil.copytree(source, root / "assets" / metadata["id"] / "1")
    shutil.copytree(FIXTURE / "scene", root / "scenes/usd-library-fixture/1")
    assets = AssetLibrary(tmp_path / "state", user_root=root)
    assets.startup()
    selected = SceneLibrary(user_root=root, state_root=tmp_path / "state").resolve("usd-library-fixture@1")
    return scoped_payload(selected, assets), selected


def scoped_payload(selected, assets):
    scope = StructuralValidator.scope
    payload = {
        "kind": "scene", "subject": "scene:" + selected.id, "metadata": selected.metadata,
        "bindings": selected.bindings, "locations": selected.locations,
        "package": scope(selected.path), "assets": [
            scope(version.path, identifier=version.id, metadata=version.metadata, digest=version.content_hash)
            | {"root": version.root} for version in assets.versions.values()
        ], "programs": program_schemas(),
        "types": {value["type"]: value for file in TYPE_ROOT.glob("*.yaml") if (value := document(file))},
    }
    payload["structure"] = validate(payload)
    assert not any(row["status"] == "fail" for row in payload["structure"]["checks"])
    return payload


def test_physics_and_runner_share_real_lab_model_and_keep_pbr_dressing_out_of_physics(tmp_path):
    payload, selected = authored_scope(tmp_path)
    config = prepare_scene_config(payload, include_visuals=True)
    with physics_simulation(payload) as physical, NewtonSimulation(
        selected.name, device="cpu", config=config
    ) as rendered:
        assert physical.model.device.is_cpu and rendered.model.device.is_cpu
        assert physical.model.body_label == rendered.model.body_label
        assert physical.model.shape_label == rendered.model.shape_label
        for name in ("body_mass", "shape_body", "shape_flags", "shape_transform",
                     "joint_type", "joint_q_start"):
            np.testing.assert_array_equal(getattr(physical.model, name).numpy(),
                                          getattr(rendered.model, name).numpy())
        assert set(rendered.model.body_label) == {
            "/World/Lab/Table", "/World/Lab/Tubes/Tube1", "/World/Lab/Tubes/Tube2",
        }
        assert not any("/Dressing" in key for key in [*rendered.model.body_label,
                       *rendered.model.shape_label, *rendered.model.joint_label])
        masses = dict(zip(rendered.model.body_label, rendered.model.body_mass.numpy(), strict=True))
        assert masses == pytest.approx({"/World/Lab/Table": 20., "/World/Lab/Tubes/Tube1": .1,
                                        "/World/Lab/Tubes/Tube2": .1})
        manifest = export_manifest(rendered, session_id="library-sdk", scene_revision="rev",
                                   revision_numeric_id=1, strict_labels=True)
        nodes = {node.entityKey: node for node in manifest.nodes}
        room = nodes["/World/Dressing/Room"]
        assert room.kind == "static" and room.visuals[0].kind == "glb" and not room.colliders
        assert room.initialPose.p == (0., 0., 0.)
        assert room.visuals[0].url.endswith("environment/usd-fixture-room/1/visual.glb")
        for key in rendered.model.body_label:
            node = nodes[key]
            assert node.kind == "body" and node.visuals[0].kind == "glb"
            if key == "/World/Lab/Table":
                assert node.visuals[0].url.endswith("environment/usd-fixture-table/1/visual.glb")
                assert node.colliders[0].kind == "mesh"
                continue
            assert node.visuals[0].url.endswith("labware/usd-fixture-tube/1/visual.glb")
            assert len(node.colliders) == 1 and node.colliders[0].kind == "primitive"
            assert node.colliders[0].geometry.type == "cylinder"
        assert nodes["/World/Lab/Table"].visuals[0].kind == "glb"
        assert len(nodes["/World/Lab/Stations"].visuals) == 2
        assert {site.entityKey for site in manifest.sites} >= {
            "/World/Lab/Stations/SlotA", "/World/Lab/Stations/SlotB",
        }
        flags = rendered.model.shape_flags.numpy()
        for site, original in rendered.builder.spatial_site_visuals.items():
            assert flags[site] & int(newton.ShapeFlags.SITE)
            assert not flags[original] & int(newton.ShapeFlags.SITE | newton.ShapeFlags.COLLIDE_SHAPES)
            np.testing.assert_allclose(rendered.model.shape_transform.numpy()[site],
                                       rendered.model.shape_transform.numpy()[original], atol=1e-6)
        host = NewtonInfoHost(rendered, manifest, SessionOptions(device="cpu"), lambda events: None,
                              scene_package=selected)
        assert host.port is not None and len(host.catalog.entities) == 2
        assert host.port.welds.counts() == (0, 0)
        np.testing.assert_allclose(host.port.site_pose("/World/Lab/Stations/SlotA")[0], [-.14, 0., .11],
                                   atol=1e-6)
        np.testing.assert_allclose(host.port.site_pose("/World/Lab/Stations/SlotB")[0], [.14, 0., .11],
                                   atol=1e-6)


def test_shared_usd_builder_preserves_actual_hinge_fk_and_ordinary_config_sites(tmp_path):
    root = tmp_path / "library"
    package = root / "scenes/usd-sites-fk/1"
    package.mkdir(parents=True)
    shutil.copyfile(FIXTURE.parents[1] / "tests/fixtures/usd-config-sites.usda", package / "stage.usda")
    documents = {
        "scene.yaml": {"name": "usd-sites-fk", "title": "USD Site FK regression",
                       "sources": ["newton"], "stage": "stage.usda"},
        "devices.yaml": {"scene": "usd-sites-fk", "entities": [{
            "key": "/World/Lab/Robot", "type": "robot-arm/v1", "program": "robot_arm.newton",
            "config": {"locations": "locations.yaml", "tubes": [], "fingerKeys": [],
                       "jointKeys": ["/World/Lab/Robot/Joint"], "handBody": "/World/Lab/Robot/Link",
                       "tcpSite": "/World/Lab/Robot/Link/TCP"},
        }]},
        "locations.yaml": {"locations": {
            "visual-good": {"site": "/World/Lab/Target", "approach": [0.], "grasp": [0.]},
            "visual-bad": {"site": "/World/Lab/Target", "approach": [0.], "grasp": [.2]},
            "explicit-good": {"site": "/World/Lab/Existing", "approach": [0.], "grasp": [0.]},
        }},
    }
    for name, value in documents.items():
        (package / name).write_text(yaml.safe_dump(value), encoding="utf-8")
    assets = AssetLibrary(tmp_path / "state", user_root=root)
    assets.startup()
    selected = SceneLibrary(user_root=root, state_root=tmp_path / "state").resolve("usd-sites-fk@1")
    payload = scoped_payload(selected, assets)
    with physics_simulation(payload) as sim:
        assert sim.model.device.is_cpu and sim.model.articulation_count > 0
        assert all(row["status"] == "pass" for row in initial_joint_checks(sim))
        before = sim.state.joint_q.numpy().copy()
        rows = {row["location"]: row for row in taught_checks(sim, payload)}
        assert rows["visual-good"]["status"] == rows["explicit-good"]["status"] == "pass"
        assert rows["visual-bad"]["status"] == "fail"
        assert rows["visual-good"]["measurements"]["errorMm"] == pytest.approx(0., abs=1e-5)
        assert rows["visual-bad"]["measurements"]["errorMm"] == pytest.approx(199.666833, abs=.001)
        np.testing.assert_array_equal(sim.state.joint_q.numpy(), before)
        assert sim.sim_time == 0.
        manifest = export_manifest(sim, session_id="sites-sdk", scene_revision="rev", revision_numeric_id=1,
                                   strict_labels=True)
        sites = [site.entityKey for site in manifest.sites]
        assert len(sites) == len(set(sites))
        assert {"/World/Lab/Robot/Link/TCP", "/World/Lab/Target", "/World/Lab/Existing"} <= set(sites)


def test_dressing_asset_with_no_rigid_bodies_completes_real_physics_checks(tmp_path):
    package = FIXTURE / "assets/usd-fixture-room"
    payload = {
        "kind": "asset", "subject": "asset:environment/usd-fixture-room@1",
        "metadata": document(package / "asset.yaml"), "package": StructuralValidator.scope(package),
        "assets": [], "programs": program_schemas(),
        "types": {value["type"]: value for file in TYPE_ROOT.glob("*.yaml") if (value := document(file))},
    }
    payload["structure"] = validate(payload)
    assert not any(row["status"] == "fail" for row in payload["structure"]["checks"])
    result = validate_physics(payload)
    assert result["physics"]["status"] == "completed" and result["physics"]["device"] == "cpu"
    assert result["physics"]["simulationSeconds"] == pytest.approx(2.)
    assert result["physics"]["freeBodyCount"] == 0
    checks = {row["id"]: row for row in result["checks"]}
    assert checks["P1"]["status"] == "pass"
    assert checks["P2"]["notApplicable"] and checks["P4"]["notApplicable"]
    assert checks["P3"]["measurements"]["simTime"] == pytest.approx(2.)
    assert not any(row["status"] == "fail" for row in result["checks"])
    # A real dressing-only scene must also reach the Runner's zero-body frame.
    from packages.library.common import content_hash
    from packages.library.reports import merge_physics
    from packages.sim_runtime.runner import _Runner
    from packages.transport import create_transport

    scene_payload, selected = authored_scope(tmp_path)
    scene = selected.path
    (scene / "stage.usda").write_text('''#usda 1.0
(metersPerUnit = 1
 upAxis = "Z")
def Xform "World" {
 def Xform "Lab" {}
 def Xform "Dressing" {
  def Xform "Room" (references = @library:assets/environment/usd-fixture-room/1/asset.usda@</Asset>) {}
 }
}
''', encoding="utf-8")
    (scene / "devices.yaml").write_text("scene: usd-library-fixture\nentities: []\n", encoding="utf-8")
    selected = SceneLibrary(user_root=scene.parents[2], state_root=tmp_path / "state").resolve(selected.id)
    assets = AssetLibrary(tmp_path / "state", user_root=scene.parents[2])
    assets.startup()
    scene_payload = scoped_payload(selected, assets)
    physical = validate_physics(scene_payload)
    assert not any(row["status"] == "fail" for row in physical["checks"]), physical
    selected.reports.save(merge_physics(scene_payload["structure"], physical), content_hash(scene))
    selected = SceneLibrary(user_root=scene.parents[2], state_root=tmp_path / "state").resolve(selected.id,
                                                                                             launch=True)
    parent, child = create_transport()
    runner = _Runner.__new__(_Runner)
    try:
        runner.__init__(child, "empty-sdk", selected.builder, SessionOptions(device="cpu"), selected)
        assert runner.sim.model.body_count == runner.sim.model.shape_count == 0
        frame = runner._frame("stream.snapshot", reliable=True)
        assert frame.poses == [] and runner.extractor.last_metrics["readbackBytes"] == 0
        assert any(node.entityKey == "/World/Dressing/Room" for node in runner.manifest.nodes)
    finally:
        if hasattr(runner, "extractor"):
            runner.extractor.close()
            assert not runner.extractor.thread.is_alive()
        for name in ("sim", "frames", "control"):
            if resource := getattr(runner, name, None):
                resource.close()
        child.close()
        parent.close()


@pytest.mark.parametrize("glob", [False, True])
def test_ancestor_config_site_keeps_child_body_identity_and_existing_node_parents(tmp_path, glob):
    payload, _ = authored_scope(tmp_path)
    stage = Path(payload["package"]["files"]["stage.usda"])
    stage.write_text(stage.read_text(encoding="utf-8").replace('def Xform "Tubes"', 'def Xform "Frame"'),
                     encoding="utf-8")
    binding = payload["bindings"]["entities"][0]
    binding["match"] = "/World/Lab/Frame/*"
    binding["config"]["locationSites"] = ["/World/Lab/Frame"]
    if glob:
        del binding["template"]
        binding.update(type="tube/v1", program="tube.newton")
        binding["config"].update(robots=[], locationSites=["/World/Lab/Frame", "/World/Lab/Stations/Slot*"])
    payload["structure"] = validate(payload)
    assert next(row for row in payload["structure"]["checks"] if row["id"] == "S5")["status"] == "pass"
    with NewtonSimulation("ancestor-site", device="cpu",
                          config=prepare_scene_config(payload, include_visuals=True)) as sim:
        manifest = export_manifest(sim, session_id="ancestor-site", scene_revision="rev",
                                   revision_numeric_id=1)
    nodes = {node.entityKey: node for node in manifest.nodes}
    assert "/World/Lab/Frame" not in nodes
    expected = {"/World/Lab/Frame"}
    if glob:
        expected.update(["/World/Lab/Stations/SlotA", "/World/Lab/Stations/SlotB"])
    assert {site.entityKey for site in manifest.sites} == expected
    assert {node.entityKey for node in manifest.nodes if node.kind == "body"} == {
        "/World/Lab/Table", "/World/Lab/Frame/Tube1", "/World/Lab/Frame/Tube2",
    }
    assert all(node.parentEntityKey is None or node.parentEntityKey in nodes for node in manifest.nodes)


def test_dressing_config_site_is_rejected_by_structure_and_shared_builder(tmp_path):
    payload, _ = authored_scope(tmp_path)
    stage = Path(payload["package"]["files"]["stage.usda"])
    room_layer = ('def Xform "Room" (references = '
                  '@library:assets/environment/usd-fixture-room/1/asset.usda@</Asset>)')
    stage.write_text(stage.read_text(encoding="utf-8").replace(
        room_layer + ' {}', room_layer + ' {\n def Xform "Marker" {}\n }'), encoding="utf-8")
    invalid = "/World/Dressing/Room/Marker"
    for binding in payload["structure"]["effectiveBindings"]["newton"]:
        binding["config"]["locationSites"] = [invalid]
    # Even an untrusted/stale effective report must not add Dressing to Newton.
    with pytest.raises(ValueError, match="outside.*World/Lab"):
        prepare_scene_config(payload).builder()
    authored = deepcopy(payload)
    authored["bindings"]["entities"][0]["config"]["locationSites"] = [invalid]
    report = validate(authored)
    failure = next(row for row in report["checks"] if row["id"] == "S5")
    assert failure["status"] == "fail" and invalid in failure["message"]
