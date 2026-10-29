"""Demonstration contact allowance changes both qualification and active guards."""

from types import SimpleNamespace

import yaml

from packages.library.metadata import scene_metadata
from packages.programs.labware.contacts import contacts_for_bodies
from packages.programs.labware.grasp import physical_grasp
from packages.programs.labware.motion import LabwareMotionProgram
from packages.programs.robot_arm.taught import posture_problems
from tests.unit.test_labware_occupancy import configured_context


def test_demo_allows_1_5mm_but_still_requires_bilateral_intended_pad_contacts():
    contacts = [SimpleNamespace(bodies=(13, finger), shapes=("cap/Handle", f"finger{finger}/Pad"),
                                distance_m=-.0015) for finger in (11, 12)]
    params = dict(body=13, fingers={11, 12}, pad_shapes={11: "finger11/Pad", 12: "finger12/Pad"},
                  shape_prefix="cap/Handle")
    args = ([-.035, 0, -.022, 0, 0, 0, 1], [0, 0, 0, 0, 0, 0, 1], [.035, 0, .022], .017, .017)
    assert not physical_grasp(*args, contacts, **params)
    assert physical_grasp(*args, contacts, contact_allowance_m=.002, **params)
    assert not physical_grasp(*args, contacts[:1], contact_allowance_m=.002, **params)
    port = SimpleNamespace(collision_contacts=lambda **kwargs: contacts)
    assert contacts_for_bodies(port, {13}) == contacts
    assert contacts_for_bodies(port, {13}, limit_m=.002) == []
    contacts[0].distance_m = -.0021
    assert not physical_grasp(*args, contacts, contact_allowance_m=.002, **params)
    assert contacts_for_bodies(port, {13}, limit_m=.002) == contacts[:1]


def test_place_executes_taught_pre_insertion_before_grasp_and_release():
    ctx, _ = configured_context()
    previous_read = ctx.read
    ctx.read = lambda entity, prop: (
        "elsewhere" if entity == "occupant" and prop == "location" else previous_read(entity, prop))
    pre = [.1] * 7
    ctx.config.locations["locations"]["target"]["preInsertion"] = pre
    program = LabwareMotionProgram()
    program.init(ctx)
    program.start(SimpleNamespace(action="place", params={
        "location": "target", "owner": "auto", "epoch": 0, "operationId": "demo-place"}), ctx)
    moves = [values for kind, values, _ in program.plan if kind == "move"]
    assert moves == [[0.] * 7, pre, [0.] * 7, [0.] * 7]
    assert posture_problems({"locations": {"target": {
        "approach": [0.] * 7, "grasp": [0.] * 7, "preInsertion": [2.] * 7}}}, [(-1, 1)] * 7)


def test_scene_import_accepts_and_retains_optional_pre_insertion(tmp_path):
    location = {"site": "/World/Lab/Sites/target", "approach": [0.] * 7,
                "grasp": [0.] * 7, "preInsertion": [.1] * 7}
    documents = {
        "scene.yaml": {"name": "demo", "title": "Demo", "sources": ["newton"], "stage": "stage.usda"},
        "devices.yaml": {"scene": "demo", "entities": []},
        "locations.yaml": {"locations": {"target": location}},
    }
    (tmp_path / "stage.usda").write_text("#usda 1.0\n")
    for name, value in documents.items():
        (tmp_path / name).write_text(yaml.safe_dump(value))
    assert scene_metadata(tmp_path)[2]["locations"]["target"]["preInsertion"] == [.1] * 7
