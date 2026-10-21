"""Teaching configuration and output guards; no IK or Source execution."""

from copy import deepcopy
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
import yaml

from tools import teach as teaching
from tools.teach import Teacher, TeachingScene, measured_grasp, select_scene, teach_selected, write_locations


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

    def solve(point, initial, *, orientation):
        calls.append(point.tolist())
        np.testing.assert_array_equal(orientation, fk[:3, :3])
        return [0.] * 7

    fk = np.eye(4)
    fk[:3, :3] = [[0, 0, 1], [0, 1, 0], [-1, 0, 0]]  # Retain a horizontal selected TCP.
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


@pytest.fixture
def measured_scene():
    """Literal model poses distinguish a real TCP from a zero-error bottom frame."""
    hand, tcp, bottom = "/Robot/Hand", "/Robot/Hand/TCP", "/Rack/A1"
    calibration = {"assetId": "robot/fr3@1", "assetContentHash": "a" * 64,
                   "geometryDigest": "b" * 64, "handBody": hand, "tcpSite": tcp,
                   "siteOwner": hand, "handToTcpPose": [0, 0, .1034, 0, 0, 0, 1]}
    offline = np.eye(4)
    offline[:3, 3] = [1, 2, 3.08]
    selection = TeachingScene(SimpleNamespace(fk=lambda q: offline), {"graspHeightM": .080},
                              {}, {"physicalTcpSite": tcp}, (), calibration)
    # The TCP's actual model local pose is float32; it is not an arbitrary tolerance.
    local_z = float(np.float32(.1034))
    manifest = {"nodes": [{"entityKey": hand, "streamId": 1, "kind": "body"}],
                "sites": [{"entityKey": tcp, "parentBodyKey": hand,
                           "localPose": {"p": [0, 0, local_z], "q": [0, 0, 0, 1]}},
                          {"entityKey": bottom, "localPose": {"p": [1, 2, 3], "q": [0, 0, 0, 1]}}]}
    snapshot = {"poses": [[1, 1, 2, 3.08 - local_z, 0, 0, 0, 1]]}
    return selection, {"site": bottom}, [0.] * 7, manifest, snapshot


def test_measured_teaching_uses_actual_tcp_and_preserves_original_bottom(measured_scene):
    row = measured_grasp(*measured_scene)
    assert row["bottomSiteWorld"] == [1, 2, 3]
    assert row["graspTargetWorld"] == row["physicalTcpWorld"] == [1, 2, 3.08]
    assert row["teacherModelErrorM"] == row["tcpErrorM"] == 0


def test_measured_teaching_rejects_wrong_offline_base_instead_of_trusting_its_fk(measured_scene):
    selection, *_ = measured_scene
    selection.teacher.fk([0.] * 7)[0, 3] += .01
    with pytest.raises(ValueError, match="differs from actual model TCP"):
        measured_grasp(*measured_scene)


@pytest.mark.parametrize("extra_error", [0., .000003])
def test_measured_teaching_accepts_legally_quantized_rotations_but_requires_control_beyond_bound(
    measured_scene, extra_error,
):
    selection, _, _, _, snapshot = measured_scene
    # Independently specified Y30deg rotation: TCP offset is (.1034/2, 0,
    # .1034*sqrt(3)/2). The actual body position rounds down .4um on each axis.
    offline = selection.teacher.fk([0.] * 7)
    offline[:3, 3] = [1.1111114 + .0517 + extra_error, 2.2222224,
                       3.3333334 + .08954702675131755]
    snapshot["poses"][0] = [1, 1.111111, 2.222222, 3.333333, 0, .258819, 0, .9659258]
    if extra_error:
        with pytest.raises(ValueError, match="actual model/FK control required"):
            measured_grasp(*measured_scene)
    else:
        row = measured_grasp(*measured_scene)
        assert 0 < row["teacherModelErrorM"] <= row["snapshotRepresentationBoundM"] < .000001
        assert row["teacherModelComparison"] == "within-snapshot-representation"


@pytest.mark.parametrize("change", ["owner", "virtual-tcp"])
def test_measured_teaching_rejects_relabelled_or_moved_model_tcp(measured_scene, change):
    _, _, _, manifest, _ = measured_scene
    site = manifest["sites"][0]
    if change == "owner":
        site["parentBodyKey"] = None
    else:
        site["localPose"]["p"][2] += .08
    with pytest.raises(ValueError, match="calibration"):
        measured_grasp(*measured_scene)


@pytest.mark.parametrize("changed_epoch", [False, True])
def test_http_capture_compares_same_paused_sample_before_writing(
    measured_scene, tmp_path, monkeypatch, changed_epoch,
):
    selection, location, joints, manifest, snapshot = measured_scene
    selection.identity.update(robot="/Robot", sceneVersion="teaching@2", sceneContentHash="c" * 64)
    selection.config["locations"] = {"locations": {"rack.A1": location}}
    summary = {"source": "newton", "sceneVersion": "teaching@2", "sceneContentHash": "c" * 64,
               "state": "PAUSED", "sceneRevision": "rev-1", "epoch": 0, "simTime": 4.0}
    manifest["sceneRevision"] = "rev-1"
    snapshot.update(sceneRevision="rev-1", epoch=0, simTime=4.0)
    reads = []

    def respond(request):
        reads.append((request.method, request.url.path))
        suffix = request.url.path.removeprefix("/api/v1/sessions/session-1")
        if suffix == "/state":
            value = {"entities": {"/Robot": {"values": {"jointPositions": joints}, "rev": 1}}}
        elif suffix == "/scene":
            value = manifest
        elif suffix == "/snapshot":
            value = snapshot
        else:
            value = summary | ({"epoch": 1} if changed_epoch and len(reads) > 1 else {})
        return httpx.Response(200, json=value)

    original_client = httpx.Client
    monkeypatch.setattr(teaching.httpx, "Client", lambda **kwargs: original_client(
        base_url="http://test", transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(teaching, "select_scene", lambda *args: selection)
    output = tmp_path / "factory/locations.yaml"
    args = SimpleNamespace(scene_version="teaching@2", robot="/Robot", library_root=tmp_path,
                           session="session-1", url="http://test", output=output,
                           input=tmp_path / "unused.yaml", capture="rack.A1", pose="grasp")
    if changed_epoch:
        with pytest.raises(ValueError, match="sample changed"):
            teaching.capture(args)
        assert not output.exists()
    else:
        result = teaching.capture(args)
        assert result["physicalTcpWorld"] == [1, 2, 3.08]
        assert result["bottomSiteWorld"] == [1, 2, 3]
        assert result["tcpErrorM"] == 0
        assert yaml.safe_load(output.read_text())["locations"]["rack.A1"]["grasp"] == joints
    assert len(reads) == 5 and all(method == "GET" for method, _ in reads)


@pytest.mark.parametrize("version,source_kind,scene_id,accepted", [
    pytest.param(2, "newton", "robot-and-tubes", True, id="old-v2"),
    pytest.param(1, "newton", "robot-and-tubes", True, id="old-v1"),
    pytest.param(2, "synthetic", "robot-and-tubes", False, id="wrong-source"),
    pytest.param(2, "newton", "other-scene", False, id="wrong-scene"),
])
def test_legacy_http_capture_keeps_zero_height_supported_versions(
    tmp_path, monkeypatch, version, source_kind, scene_id, accepted,
):
    """Existing Python-builder scene versions still use the legacy h0 capture path."""
    robot, site = "/World/Lab/RobotA", "/World/Lab/Rack/A1"
    joints = [0., -.4, 0., -2., 0., 1.6, .7]
    original = {"locations": {"rack.A1": {"site": site, "grasp": [0.] * 7, "approach": [1.] * 7}}}
    input_path, output = tmp_path / "input.yaml", tmp_path / "factory/locations.yaml"
    input_path.write_text(yaml.safe_dump(original), encoding="utf-8")
    documents = {"": {"source": source_kind, "sceneId": scene_id, "sceneVersion": f"{scene_id}@{version}",
                       "state": "RUNNING"},
                 "/state": {"entities": {robot: {"values": {"jointPositions": joints}, "rev": 3}}},
                 "/scene": {"nodes": [], "sites": [{"entityKey": site,
                            "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]}}]},
                 "/snapshot": {"poses": []}}

    def respond(request):
        return httpx.Response(200, json=documents[request.url.path.removeprefix("/api/v1/sessions/old")])

    client_class = httpx.Client
    monkeypatch.setattr(teaching.httpx, "Client", lambda **kwargs: client_class(
        base_url="http://test", transport=httpx.MockTransport(respond)))
    args = SimpleNamespace(scene_version=None, robot=None, library_root=None, session="old", url="http://test",
                           input=input_path, output=output, capture="rack.A1", pose="grasp")
    if not accepted:
        with pytest.raises(ValueError, match="matching Newton scene"):
            teaching.capture(args)
        assert not output.exists()
        assert input_path.read_text() == yaml.safe_dump(original)
        return
    result = teaching.capture(args)
    written = yaml.safe_load(output.read_text())
    assert written["locations"]["rack.A1"]["grasp"] == joints
    assert written["locations"]["rack.A1"]["approach"] == [1.] * 7
    assert input_path.read_text() == yaml.safe_dump(original)
    assert result["inputIdentity"] == {"sceneVersion": f"robot-and-tubes@{version}"}
    assert result["graspHeightM"] == 0.0
