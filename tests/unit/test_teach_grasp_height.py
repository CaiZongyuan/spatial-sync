"""Teaching configuration and output guards; no IK or Source execution."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from tools.teach import Teacher, TeachingScene, select_scene, teach_selected, write_locations


def test_teacher_fk_uses_selected_base_and_pinned_source_without_changing_legacy():
    pytest.importorskip("scipy", reason="Offline FK teaching requires the simulation extra")
    legacy = Teacher()
    base = np.eye(4)
    base[:3, 3] = [.2, .7, .95]
    selected = Teacher(base=base)
    q = [0, -.4, 0, -2, 0, 1.6, .7]
    old = legacy.fk(q)
    actual = selected.fk(q)
    np.testing.assert_allclose(actual[:3, 3] - old[:3, 3], [.7, .7, .2], atol=1e-12)
    np.testing.assert_array_equal(actual[:3, :3], old[:3, :3])


def test_selected_teaching_reports_old_bottom_fk_as_fifty_mm_error_without_erasing_locations():
    calls = []

    def solve(point, initial):
        calls.append(point.tolist())
        return [0.] * 7

    fk = np.eye(4)
    fk[:3, 3] = [1, 2, 3]
    locations = {"robot": "R", "locations": {name: {"site": name, "grasp": [0.] * 7,
                   "approach": [0.] * 7, "requires": []} for name in ("rack.A1", "centrifuge.slot1")}}
    before = deepcopy(locations)
    selection = TeachingScene(SimpleNamespace(solve=solve, fk=lambda q: fk),
                             {"locations": locations, "graspHeightM": .050},
                             {name: [1, 2, 3] for name in locations["locations"]},
                             {"sceneVersion": "fr3-test@2", "sceneContentHash": "a" * 64}, ())
    result, evidence = teach_selected(selection)
    assert len(result["locations"]) == len(evidence) == 2
    assert locations == before
    assert calls[0] == [1, 2, 3.05] and calls[1] == pytest.approx([1, 2, 3.20])
    for row in evidence:
        assert row["bottomSiteWorld"] == row["physicalTcpWorld"] == [1, 2, 3]
        assert row["graspTargetWorld"] == [1, 2, 3.05]
        assert row["tcpErrorM"] == pytest.approx(.05)
        assert row["sceneVersion"] == "fr3-test@2"


def test_teaching_refuses_implicit_latest_and_writing_published_scene_packages(tmp_path):
    with pytest.raises(ValueError, match="exact scene version"):
        select_scene("fr3-test")
    library = tmp_path / "library"
    published = library / "scenes/fr3-test/2/locations.yaml"
    published.parent.mkdir(parents=True)
    published.write_text("original", encoding="utf-8")
    with pytest.raises(ValueError, match="immutable"):
        write_locations(published, {"locations": {}}, (library,))
    assert published.read_text() == "original"
    output = tmp_path / "factory/locations.yaml"
    write_locations(output, {"locations": {}}, (library,))
    assert output.read_text(encoding="utf-8") == "locations: {}\n"
