import pytest

from packages.sim_runtime.labels import resolve_labels


def test_duplicate_labels_across_entity_kinds_fail_with_conflicting_entities():
    with pytest.raises(ValueError, match=r"/World/Same.*body\[0\].*shape\[0\]"):
        resolve_labels(["World/Same"], ["/World/Same"], [])


def test_missing_names_use_explicit_provenance_and_do_not_guess_from_spelling():
    labels = resolve_labels(["body_0", "body_1"], [None], ["World/hinge"], missing_labels={("body", 1)})
    assert labels.bodies[0].entity_key == "/body_0"
    assert labels.bodies[0].stable
    assert labels.bodies[1].entity_key == "/__unlabeled/body_1"
    assert not labels.bodies[1].stable
    assert labels.shapes[0].entity_key == "/__unlabeled/shape_0"
    assert [w["code"] for w in labels.warnings] == ["unlabeled_entity", "unlabeled_entity"]
    with pytest.raises(ValueError, match="Missing label"):
        resolve_labels([None], [], [], strict=True)


def test_sites_share_shape_labels_but_have_site_fallback_kind():
    labels = resolve_labels([], [None], [], site_indices={0})
    assert labels.shapes[0].entity_key == "/__unlabeled/site_0"


def test_same_usd_prim_can_own_its_body_and_collision_shape_without_renaming():
    key = "/World/Lab/Box"
    labels = resolve_labels([key], [key], [], shape_bodies=[0])
    assert labels.bodies[0].entity_key == labels.shapes[0].entity_key == key
    assert labels.bodies[0].stable and labels.shapes[0].stable
    with pytest.raises(ValueError, match="Duplicate label"):
        resolve_labels([key, "/World/Lab/Other"], [key], [], shape_bodies=[1])
    with pytest.raises(ValueError, match="Duplicate label"):
        resolve_labels([key], [key, key], [], shape_bodies=[0, 0])


def test_generated_site_alias_preserves_its_original_nonphysical_visual_identity():
    key = "/World/Lab/Rack/Slot"
    labels = resolve_labels([], [key, key], [], shape_bodies=[-1, -1],
                            site_indices={1}, site_aliases={1: 0})
    assert [identity.kind for identity in labels.shapes] == ["shape", "site"]
    assert all(identity.entity_key == key for identity in labels.shapes)
    with pytest.raises(ValueError, match="Duplicate label"):
        resolve_labels([], [key, key], [], shape_bodies=[-1, -1], site_indices={1})
    with pytest.raises(ValueError, match="Duplicate label"):
        resolve_labels(["/World/Lab/Body"], [key, key], [], shape_bodies=[0, -1],
                       site_indices={1}, site_aliases={1: 0})
