"""Pure template tests use real Config reference metadata and immutable local bytes."""

import copy

import pytest

from packages.library.common import content_hash
from packages.library.templates import TemplateError, config_fields, instantiate, resolve
from packages.programs.centrifuge.newton import CentrifugeProgram as NativeCentrifuge
from packages.programs.centrifuge.virtual import CentrifugeProgram as VirtualCentrifuge
from packages.programs.robot_arm.newton import RobotArmProgram


@pytest.fixture
def asset(tmp_path):
    (tmp_path / "input.txt").write_text("immutable authored reference", encoding="utf-8")
    return {
        "id": "device/reference@1",
        "path": str(tmp_path),
        "contentHash": content_hash(tmp_path),
        "metadata": {
            "implements": [
                {
                    "type": "centrifuge/v1",
                    "programs": {"newton": "centrifuge.newton", "synthetic": "centrifuge.virtual"},
                    "config": {"slotSites": ["Rotor/Slot*"], "visualRpmCap": 55},
                }
            ]
        },
    }


@pytest.fixture
def resources():
    roots = ["/World/Lab/First", "/World/Lab/Second"]
    return {
        "entities": roots,
        "bodies": [],
        "joints": [],
        "sites": [f"{root}/Rotor/Slot{i}" for root in roots for i in range(1, 5)],
    }


PROGRAMS = {
    program.id: {"fields": config_fields(program)}
    for program in (NativeCentrifuge, VirtualCentrifuge, RobotArmProgram)
}


def test_two_instances_rebase_independently_override_top_level_and_preserve_all_inputs(asset, resources):
    document = {
        "scene": "fixture",
        "entities": [
            {"match": "/World/Lab/*", "template": asset["id"], "config": {"visualRpmCap": 61, "tubes": []}}
        ],
    }
    original = copy.deepcopy((document, asset))
    resolved = resolve(document, "synthetic", {asset["id"]: asset})
    effective = instantiate(resolved, resources, PROGRAMS)["entities"]
    assert len(effective) == 2
    for binding in effective:
        assert binding["program"] == "centrifuge.virtual"
        assert binding["config"]["slotSites"] == [f"{binding['key']}/Rotor/Slot{i}" for i in range(1, 5)]
        assert binding["config"]["visualRpmCap"] == 61
    assert (document, asset) == original
    assert instantiate(resolved, resources, PROGRAMS)["entities"] == effective


@pytest.mark.parametrize(
    "path",
    ["../outside", "Rotor/../../outside", "C:/host", "\\\\server\\file", "https://host/file", "/World/Other"],
)
def test_relative_template_paths_cannot_escape_asset_root(asset, resources, path):
    asset["metadata"]["implements"][0]["config"]["slotSites"] = [path]
    document = {"scene": "fixture", "entities": [{"key": "/World/Lab/First", "template": asset["id"]}]}
    with pytest.raises(TemplateError, match="relative to /Asset"):
        instantiate(resolve(document, "synthetic", {asset["id"]: asset}), resources, PROGRAMS)


@pytest.mark.parametrize(
    "reference",
    ["device/reference", "device/reference@0", "device/reference@01", "../reference@1", "C:/host@1"],
)
def test_templates_require_exact_portable_version_ids(reference):
    document = {"scene": "fixture", "entities": [{"key": "/World/Lab/First", "template": reference}]}
    with pytest.raises(TemplateError, match="exact asset"):
        resolve(document, "synthetic", {})


def test_program_selection_uses_source_and_rejects_conflicting_explicit_program(asset):
    binding = {"key": "/World/Lab/First", "template": asset["id"]}
    document = {"scene": "fixture", "entities": [binding]}
    assert (
        resolve(document, "newton", {asset["id"]: asset}).bootstrap()["entities"][0]["program"]
        == "centrifuge.newton"
    )
    assert (
        resolve(document, "synthetic", {asset["id"]: asset}).bootstrap()["entities"][0]["program"]
        == "centrifuge.virtual"
    )
    binding["program"] = "centrifuge.newton"
    with pytest.raises(TemplateError, match="conflicts"):
        resolve(document, "synthetic", {asset["id"]: asset})
    binding.pop("program")
    asset["metadata"]["implements"][0]["programs"].pop("synthetic")
    with pytest.raises(TemplateError, match="found 0"):
        resolve(document, "synthetic", {asset["id"]: asset})


def test_multiple_implements_need_an_unambiguous_existing_type_selector(asset):
    asset["metadata"]["implements"].append(
        {"type": "other/v1", "programs": {"synthetic": "other.virtual"}, "config": {}}
    )
    document = {"scene": "fixture", "entities": [{"key": "/World/Lab/First", "template": asset["id"]}]}
    with pytest.raises(TemplateError, match="found 2"):
        resolve(document, "synthetic", {asset["id"]: asset})
    document["entities"][0]["type"] = "centrifuge/v1"
    assert (
        resolve(document, "synthetic", {asset["id"]: asset}).bootstrap()["entities"][0]["type"]
        == "centrifuge/v1"
    )
    asset["metadata"]["implements"].append(copy.deepcopy(asset["metadata"]["implements"][0]))
    with pytest.raises(TemplateError, match="found 2"):
        resolve(document, "synthetic", {asset["id"]: asset})


def test_explicit_legacy_paths_replace_rebased_defaults_without_guessing_an_extra_prefix(asset):
    asset["metadata"]["implements"] = [
        {
            "type": "robot-arm/v1",
            "programs": {"newton": "robot_arm.newton"},
            "config": {"jointKeys": ["Joint1"], "graspMode": "weld"},
        }
    ]
    resources = {
        "entities": ["/World/Lab/RobotA"],
        "bodies": [],
        "sites": [],
        "joints": ["/World/Lab/RobotA/fr3/Joint1"],
    }
    document = {
        "scene": "fixture",
        "entities": [
            {
                "key": "/World/Lab/RobotA",
                "template": asset["id"],
                "config": {"jointKeys": ["/World/Lab/RobotA/fr3/Joint1"], "locations": "locations.yaml"},
            }
        ],
    }
    actual = instantiate(resolve(document, "newton", {asset["id"]: asset}), resources, PROGRAMS)["entities"][
        0
    ]
    assert actual["config"] == {
        "jointKeys": ["/World/Lab/RobotA/fr3/Joint1"],
        "graspMode": "weld",
        "locations": "locations.yaml",
    }


def test_unknown_reference_duplicate_binding_and_zero_glob_are_explicit_errors(asset, resources):
    document = {"scene": "fixture", "entities": [{"key": "/World/Lab/First", "template": "device/missing@1"}]}
    with pytest.raises(TemplateError, match="Unknown asset"):
        resolve(document, "synthetic", {asset["id"]: asset})
    document["entities"] = [{"match": "/World/Absent/*", "template": asset["id"]}]
    with pytest.raises(TemplateError, match="matched no"):
        instantiate(resolve(document, "synthetic", {asset["id"]: asset}), resources, PROGRAMS)
    document["entities"] = [
        {"match": "/World/Lab/*", "template": asset["id"]},
        {"key": "/World/Lab/First", "type": "centrifuge/v1", "program": "centrifuge.virtual"},
    ]
    with pytest.raises(TemplateError, match="Duplicate binding"):
        instantiate(resolve(document, "synthetic", {asset["id"]: asset}), resources, PROGRAMS)


def test_changed_asset_bytes_cannot_be_read_under_the_same_snapshot(asset):
    document = {"scene": "fixture", "entities": [{"key": "/World/Lab/First", "template": asset["id"]}]}
    from pathlib import Path

    (Path(asset["path"]) / "input.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(TemplateError, match="immutable asset changed"):
        resolve(document, "synthetic", {asset["id"]: asset})
