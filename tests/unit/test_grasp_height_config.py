"""The same strict optional Config scalar in both existing arm programs."""

import pytest
from pydantic import ValidationError

from apps.api.app.library_validation import program_schemas
from packages.library.templates import config_description
from packages.programs.robot_arm.newton import RobotArmProgram as NewtonArm
from packages.programs.robot_arm.virtual import RobotArmProgram as VirtualArm


def inputs(program):
    values = {"locations": {"locations": {}}, "tubes": ["/World/Lab/Tubes/T1"]}
    if program is NewtonArm:
        values.update(jointKeys=[f"/World/Lab/R/j{i}" for i in range(7)],
                      fingerKeys=["/World/Lab/R/f1", "/World/Lab/R/f2"],
                      handBody="/World/Lab/R/hand", tcpSite="/World/Lab/R/hand/tcp")
    return values


@pytest.mark.parametrize("program", [NewtonArm, VirtualArm])
def test_old_config_defaults_to_zero_and_schema_exposes_only_one_optional_height(program):
    assert program.Config.model_validate(inputs(program)).graspHeightM == 0.0
    schema = program.Config.model_json_schema()
    height = schema["properties"]["graspHeightM"]
    assert {key: height[key] for key in ("type", "minimum", "default")} == {
        "type": "number", "minimum": 0, "default": 0.0}
    assert "graspHeightM" not in schema.get("required", [])


@pytest.mark.parametrize("program", [NewtonArm, VirtualArm])
@pytest.mark.parametrize("height", [0, 0.0, 0.080])
def test_supported_numeric_height_reaches_the_existing_config(program, height):
    assert program.Config.model_validate(inputs(program) | {"graspHeightM": height}).graspHeightM == height


@pytest.mark.parametrize("program", [NewtonArm, VirtualArm])
@pytest.mark.parametrize("height", [-0.001, float("nan"), float("inf"), True, False, "0.08"])
def test_invalid_height_is_a_config_error_with_field_path(program, height):
    with pytest.raises(ValidationError) as caught:
        program.Config.model_validate(inputs(program) | {"graspHeightM": height})
    assert caught.value.errors(include_url=False)[0]["loc"] == ("graspHeightM",)


def test_private_registry_snapshot_preserves_declared_scalar_and_real_endpoint_role():
    description = config_description(NewtonArm)
    assert description["fields"]["tcpSite"] == {
        "spatialReference": "site",
        "postureEndpoint": {"offsetField": "graspHeightM", "offsetAxis": "worldZ"},
    }
    assert description["configSchema"]["properties"]["graspHeightM"]["type"] == "number"
    registered = program_schemas()[NewtonArm.id]
    assert registered["fields"] == description["fields"]
    assert registered["configSchema"] == description["configSchema"]
