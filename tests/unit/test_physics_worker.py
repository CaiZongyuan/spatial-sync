"""Availability and owned-worker failures remain GPU/SDK-free in ordinary CI."""

import subprocess
import sys
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from lw.client import GatewayClient

from packages.library import physics
from packages.library.assets import AssetLibrary
from packages.library.common import LibraryError, content_hash
from packages.library.references import ReferenceInspection
from packages.library.reports import ReportStore, merge_physics
from packages.library.scenes import SceneLibrary
from packages.library.validation import StructuralValidator


def test_missing_distributions_are_observations_without_importing_the_engine(monkeypatch):
    before = set(sys.modules)

    def installed(name):
        if name in ("newton", "warp-lang"):
            raise physics.PackageNotFoundError(name)
        return "26.8"

    monkeypatch.setattr(physics, "version", installed)
    result = physics.validate({"kind": "asset", "metadata": {}})
    assert result["physics"] == {
        "status": "skipped", "reason": "Physics tools are not installed: newton, warp-lang",
        "missingTools": ["newton", "warp-lang"],
    }
    assert not any(row["status"] == "pass" for row in result["checks"])
    assert not {"newton", "warp"} & (set(sys.modules) - before)
    assert "toolVersions" not in result["physics"]


def test_synthetic_and_invalid_structure_skip_without_querying_or_importing_sdk(monkeypatch):
    monkeypatch.setattr(physics, "version", lambda name: pytest.fail("SDK lookup is unnecessary"))
    synthetic = physics.validate({"kind": "scene", "metadata": {"sources": ["synthetic"]}})
    assert synthetic["physics"]["status"] == "skipped"
    invalid = physics.validate({"structure": {"checks": [{"status": "fail"}]}})
    assert "structural validation failed" in invalid["physics"]["reason"]


def test_p6_without_taught_config_has_no_geometry_export_or_engine_dependency():
    before = set(sys.modules)
    result = physics.taught_checks(object(), {"structure": {"effectiveBindings": {"newton": []}},
                                              "programs": {}})
    assert result[0]["id"] == "P6" and result[0]["notApplicable"]
    assert not {"newton", "warp"} & (set(sys.modules) - before)


@pytest.mark.parametrize("imported_site,articulation,count", [
    (False, 0, 1), (True, 0, 1), (False, -1, 1), (False, 0, 0),
])
def test_p6_measures_visual_and_imported_usd_sites(monkeypatch, imported_site, articulation, count):
    """Exercise the real identity adapters; SDK geometry/FK are stubbed, not measured."""
    site_flag = 8
    tcp, target, joint = "/World/Lab/TCP", "/World/Lab/Target", "/World/Lab/Joint"

    class Array:
        def __init__(self, values):
            self.values = np.array(values)

        def numpy(self):
            return self.values

        def assign(self, values):
            self.values = values

    pose = [0, 0, 1, 0, 0, 0, 1]
    builder = SimpleNamespace(shape_flags=[site_flag if imported_site else 1] * 2,
                              shape_label=[tcp, target], shape_body=[-1, -1],
                              shape_transform=[pose.copy(), pose.copy()])
    original_flags = builder.shape_flags.copy()

    def add_site(body, *, xform, label):
        builder.shape_flags.append(site_flag)
        builder.shape_label.append(label)
        builder.shape_body.append(body)
        builder.shape_transform.append(xform)

    builder.add_site = add_site
    world = SimpleNamespace(ExtractTranslation=lambda: pose[:3], ExtractRotationQuat=lambda:
                            SimpleNamespace(GetImaginary=lambda: pose[3:6], GetReal=lambda: pose[6]))
    stage = SimpleNamespace(GetPrimAtPath=lambda key: SimpleNamespace(IsA=lambda kind: True))
    monkeypatch.setitem(sys.modules, "pxr", SimpleNamespace(UsdGeom=SimpleNamespace(
        Xformable=object(), XformCache=lambda: SimpleNamespace(GetLocalToWorldTransform=lambda prim: world))))
    monkeypatch.setitem(sys.modules, "warp", SimpleNamespace(
        transform=lambda p, q: [*p, *q], quat=lambda *q: q))
    monkeypatch.setitem(sys.modules, "newton", SimpleNamespace(
        ShapeFlags=SimpleNamespace(SITE=site_flag), eval_fk=lambda *args: None))
    payload = {"programs": {"fixture": {"fields": {
        "joints": {"posture": True}, "tcp": {"spatialReference": "site"},
        "locations": {"documentSchema": "locations"},
    }}}, "structure": {"effectiveBindings": {"newton": [{
        "key": "/World/Lab", "program": "fixture", "config": {
            "joints": [joint], "tcp": tcp,
            "locations": {"locations": {"station": {"site": target, "grasp": [0.]}}},
        },
    }]}}}
    physics.add_taught_sites(builder, stage, {
        "path_shape_map": {tcp: 0, target: 1}, "path_body_map": {},
    }, payload)
    model = SimpleNamespace(
        **{name: Array(getattr(builder, name)) for name in
           ("shape_flags", "shape_body", "shape_transform")},
        shape_label=builder.shape_label, joint_label=[joint],
        articulation_count=count, joint_articulation=Array([articulation]),
        joint_q_start=Array([0, 1]), joint_qd_start=Array([0, 1]),
        joint_limit_lower=Array([-1.]), joint_limit_upper=Array([1.]),
        state=lambda: SimpleNamespace(joint_q=Array([0.]), joint_qd=Array([0.])),
    )
    result = physics.taught_checks(SimpleNamespace(model=model), payload)
    if 0 <= articulation < count:
        assert result[0]["status"] == "pass" and result[0]["measurements"]["errorMm"] == 0
    else:
        assert result[0]["status"] == "fail" and "measurements" not in result[0]
        assert "outside Newton forward kinematics" in result[0]["message"]
    site_labels = [label for label, flags in zip(builder.shape_label, builder.shape_flags, strict=True)
                   if flags & site_flag]
    assert sorted(site_labels) == [tcp, target]
    assert builder.shape_flags[:2] == original_flags
    assert builder.shape_transform[:2] == [pose, pose]


@pytest.mark.parametrize("height,status", [(0.05, "pass"), (0.045, "fail")])
def test_contact_transfer_uses_builtin_numbers_for_real_float32_body_frame_data(monkeypatch, height, status):
    def array(values, dtype=np.float32):
        return SimpleNamespace(numpy=lambda: np.array(values, dtype=dtype))

    monkeypatch.setitem(sys.modules, "newton", SimpleNamespace())
    contacts = SimpleNamespace(
        rigid_contact_count=array([1], np.int32), rigid_contact_max=1,
        contact_counters=array([1, 0, 0], np.int32),
        rigid_contact_shape0=array([0], np.int32), rigid_contact_shape1=array([1], np.int32),
        rigid_contact_point0=array([[0, 0, -0.05]]), rigid_contact_point1=array([[0, 0, 0]]),
        rigid_contact_normal=array([[0, 0, -1]]), rigid_contact_margin0=array([0]),
        rigid_contact_margin1=array([0]),
    )
    pipeline = SimpleNamespace(
        contacts=lambda: contacts, collide=lambda state, output: None,
        include_static_kinematic_pairs=True,
        broad_phase_pair_count=array([1], np.int32), broad_phase_shape_pairs=np.zeros((1, 2)),
        narrow_phase=SimpleNamespace(split_gjk_mpr=False, global_contact_reducer=None),
    )
    sim = SimpleNamespace(
        collision_pipeline=pipeline, state=SimpleNamespace(body_q=array([[0, 0, height, 0, 0, 0, 1]])),
        model=SimpleNamespace(shape_count=2, shape_body=array([0, -1], np.int32),
                              shape_label=["Sphere", "Ground"]),
    )
    observations = physics.contact_observations(sim)
    assert type(observations[0][2]) is float
    assert physics.penetration(observations)["status"] == status


def test_physics_timeout_preserves_published_failure_and_latest_pointer(tmp_path, monkeypatch):
    package = tmp_path / "package"
    package.mkdir()
    (package / "scene.yaml").write_text("immutable fixture", encoding="utf-8")
    structural = {"schema": "lw.validation/1", "subject": "scene:timeout-fixture@1", "launchable": True,
                  "checks": [{"id": "S8", "tier": "structural", "status": "pass", "message": "Valid"}]}
    physical = {"physics": {"status": "completed", "device": "cpu", "ranAt": "2026-10-20T00:00:00Z",
                            "toolVersions": {"newton": "1.7.0"}},
                "checks": [{"id": "P6", "tier": "physics", "status": "fail", "message": "38 mm"}]}
    store = ReportStore(tmp_path / "state")
    digest = content_hash(package)
    previous = store.save(merge_physics(structural, physical), digest)
    selected = SimpleNamespace(id="timeout-fixture@1", path=package, content_hash=digest, validation=None,
                               reports=store, metadata={"stage": "python:falling-boxes"})
    directory = store.directory(structural["subject"], digest)
    before = {file.name: file.read_bytes() for file in directory.iterdir()}
    validator = StructuralValidator(tmp_path / "state")

    def worker(module, payload, timeout):
        if module == "structural":
            assert timeout == 20
            return structural
        assert timeout == 180
        raise LibraryError("validation_unavailable", "Physics validation timed out", 503)

    monkeypatch.setattr(validator, "worker", worker)
    monkeypatch.setattr("packages.library.validation.physics_availability", lambda payload: (None, {}))
    with pytest.raises(LibraryError) as failure:
        validator.revalidate(selected, "scene", SimpleNamespace(versions={}))
    assert failure.value.status == 503
    assert store.latest(structural["subject"], digest) == previous
    assert {file.name: file.read_bytes() for file in directory.iterdir()} == before


def test_reference_structure_entrypoint_never_starts_physics_worker(tmp_path, monkeypatch):
    report = {"schema": "lw.validation/1", "subject": "scene:references-fixture@1", "launchable": True,
              "checks": [{"id": "S7", "tier": "structural", "status": "pass", "message": "Pinned"}]}
    validator = StructuralValidator(tmp_path / "state")
    calls = []

    def worker(module, payload, timeout):
        calls.append(module)
        assert module == "structural"
        return report

    monkeypatch.setattr(validator, "worker", worker)
    result = validator.run_structure(tmp_path, "scene", report["subject"], {}, SimpleNamespace(versions={}))
    assert result == report
    assert calls == ["structural"]


def test_missing_tools_return_real_skip_without_starting_a_physics_child(tmp_path, monkeypatch):
    report = {"schema": "lw.validation/1", "subject": "scene:missing-tools@1", "launchable": True,
              "checks": [{"id": "S8", "tier": "structural", "status": "pass", "message": "Valid"}]}
    validator = StructuralValidator(tmp_path / "state")
    calls = []

    def worker(module, payload, timeout):
        calls.append(module)
        assert module == "structural"
        return report

    def missing(name):
        if name in ("newton", "warp-lang"):
            raise physics.PackageNotFoundError(name)
        return "26.8"

    monkeypatch.setattr(validator, "worker", worker)
    monkeypatch.setattr(physics, "version", missing)
    result = validator.run(tmp_path, "scene", report["subject"], {"sources": ["newton"]},
                           SimpleNamespace(versions={}))
    assert calls == ["structural"]
    assert result["physics"]["status"] == "skipped"
    assert result["physics"]["missingTools"] == ["newton", "warp-lang"]


def test_actual_reference_scan_fills_missing_s7_with_only_structure(tmp_path, monkeypatch):
    root = tmp_path / "user-library"
    target = root / "assets/labware/target/1"
    target.mkdir(parents=True)
    target.joinpath("asset.yaml").write_text(
        "id: labware/target\nkind: labware\ntitle: Target\nstage: asset.usda\n", encoding="utf-8"
    )
    target.joinpath("asset.usda").write_text('#usda 1.0\ndef Xform "Asset" {}\n', encoding="utf-8")
    owner = root / "scenes/reference-owner/1"
    owner.mkdir(parents=True)
    owner.joinpath("scene.yaml").write_text(
        "name: reference-owner\ntitle: Owner\nsources: [newton]\nstage: scene.usda\n", encoding="utf-8"
    )
    owner.joinpath("devices.yaml").write_text("scene: reference-owner\nentities: []\n", encoding="utf-8")
    owner.joinpath("locations.yaml").write_text("locations: {}\n", encoding="utf-8")
    owner.joinpath("scene.usda").write_text('#usda 1.0\ndef Xform "World" {}\n', encoding="utf-8")
    assets = AssetLibrary(tmp_path / "state", builtin_root=tmp_path / "builtin", user_root=root)
    assets.startup()
    scenes = SceneLibrary(builtin_root=tmp_path / "builtin", user_root=root, state_root=tmp_path / "state")
    validator = StructuralValidator(tmp_path / "state")
    calls = []

    def worker(module, payload, timeout):
        calls.append(module)
        assert module == "structural"
        return {"schema": "lw.validation/1", "subject": payload["subject"], "launchable": True,
                "checks": [{"id": "S7", "tier": "structural", "status": "pass", "message": "Inspected"}],
                "referencedAssets": []}

    monkeypatch.setattr(validator, "worker", worker)
    monkeypatch.setattr("packages.library.validation.physics_availability",
                        lambda payload: pytest.fail("Reference inspection queried physics availability"))
    inspection = ReferenceInspection(assets, lambda: scenes, SimpleNamespace(root=tmp_path / "recordings"),
                                     lambda: [], validator)
    selected = assets.resolve("labware/target@1")
    result = inspection.scan(selected, "asset")
    assert result["complete"] and result["references"] == []
    assert inspection.scan(selected, "asset")["complete"]
    assert calls == ["structural"]


def test_unreaped_child_has_only_bounded_waits_and_current_handle_cleanup(tmp_path, monkeypatch):
    waits = []

    class Stuck:
        returncode = None
        killed = False

        def wait(self, *, timeout):
            waits.append(timeout)
            raise subprocess.TimeoutExpired("owned fixture", timeout)

        def poll(self):
            return None

        def kill(self):
            self.killed = True

    child = Stuck()
    monkeypatch.setattr("packages.library.validation.subprocess.Popen", lambda *args, **kwargs: child)
    validator = StructuralValidator(tmp_path)
    with pytest.raises(LibraryError, match="cleanup could not be verified"):
        validator.worker("physics", {}, 180)
    assert child.killed and waits == [180, 5]


async def test_cli_and_mcp_shared_mutations_wait_for_the_bounded_workers(tmp_path):
    observed = []

    def gateway(request):
        observed.append((request.url.path, request.extensions["timeout"]))
        return httpx.Response(200, json={"received": True})

    (tmp_path / "asset.yaml").write_text("fixture", encoding="utf-8")
    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), base_url="http://test") as http:
        client = GatewayClient(http=http)
        assert await client.library_validate("robot/fr3@1") == {"received": True}
        assert await client.library_import(tmp_path) == {"received": True}
    assert [path for path, _ in observed] == ["/api/v1/library/assets/robot/fr3/1/validate",
                                             "/api/v1/library/imports"]
    assert all(set(timeout.values()) == {240} for _, timeout in observed)
