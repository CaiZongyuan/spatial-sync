from types import SimpleNamespace

import pytest

newton = pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from newton.solvers import SolverXPBD

from packages.protocol.math import compose_pose
from packages.sim_runtime.labels import labels_for_model
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.simulation import NewtonSimulation


def test_manifest_preserves_newton_geometry_and_child_site_anchor():
    with NewtonSimulation() as sim:
        manifest = export_manifest(sim, session_id="test", scene_revision="rev", revision_numeric_id=1)
        box = next(n for n in manifest.nodes if n.entityKey == "/World/Box")
        assert box.visuals[0].geometry.halfExtents == pytest.approx((0.15, 0.2, 0.15))
        floor = next(n for n in manifest.nodes if n.entityKey == "/World/Floor")
        assert floor.kind == "static"
        assert not hasattr(floor, "streamId")
        assert floor.initialPose.p == (0, 0, 0)
        assert len([n for n in manifest.nodes if n.kind == "body"]) == 2
        site = next(s for s in manifest.sites if s.role == "joint-anchor")
        assert site.localPose.p == pytest.approx((0, 0, 0.35))
        p = sim.body_poses()[1]
        world = compose_pose({"p": p[:3], "q": p[3:]}, site.localPose.model_dump())
        assert world["p"] == pytest.approx((1, 0, 1.5), abs=1e-6)
        assert p[:3] != pytest.approx(world["p"], abs=1e-3)
        free = next(j for j in manifest.joints if j.type == "free")
        assert free.anchorSiteKey is None


def test_omitted_newton_label_provenance_is_distinct_from_explicit_body_0():
    builder = newton.ModelBuilder()
    builder.add_body()  # Known omission at this scene-construction boundary.
    model = builder.finalize(device="cpu")
    labels = labels_for_model(model, missing_labels={("body", 0), ("joint", 0)})
    assert labels.bodies[0].entity_key == "/__unlabeled/body_0"
    assert not labels.bodies[0].stable
    with pytest.raises(ValueError, match="Missing label"):
        labels_for_model(model, missing_labels={("body", 0)}, strict=True)
    explicit = newton.ModelBuilder()
    explicit.add_body(label="body_0")
    explicit_labels = labels_for_model(explicit.finalize(device="cpu"))
    assert explicit_labels.bodies[0].entity_key == "/body_0"
    assert explicit_labels.bodies[0].stable


def test_exporter_forwards_known_construction_omission_provenance():
    builder = newton.ModelBuilder()
    builder.add_body()
    model = builder.finalize(device="cpu")
    scene = SimpleNamespace(
        model=model,
        anchor_sites={},
        epoch=0,
        scene_id="known-omission",
        physics_dt=1 / 240,
        solver=SolverXPBD(model),
        missing_labels={("body", 0), ("joint", 0)},
    )
    manifest = export_manifest(scene, session_id="test", scene_revision="rev", revision_numeric_id=1)
    body = next(n for n in manifest.nodes if n.kind == "body")
    assert body.entityKey == "/__unlabeled/body_0" and not body.stableKey
    assert [w.code for w in manifest.warnings] == ["unlabeled_entity", "unlabeled_entity"]
    with pytest.raises(ValueError, match="Missing label"):
        export_manifest(
            scene,
            session_id="test",
            scene_revision="rev",
            revision_numeric_id=1,
            strict_labels=True,
        )
