"""Scene ground Waypoint contract coexisting backward-compatibly with arm Teach Locations."""

import math
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from packages.info.binding_validation import validate_binding_semantics
from packages.library.description_views import description_view
from packages.library.metadata import Locations, scene_metadata
from packages.library.scenes import SceneLibrary
from packages.library.structural import config_locations
from packages.library.templates import TemplateError, require_spatial_domain


def test_locations_accepts_legacy_teach_locations_waypoints_only_and_combined():
    legacy = Locations.model_validate(
        {
            "robot": "fr3",
            "locations": {
                "rack_a1": {
                    "site": "/World/Lab/Rack/SlotA1",
                    "zone": "rack",
                    "approach": [0.0] * 7,
                    "grasp": [0.1] * 7,
                }
            },
        }
    )
    assert "rack_a1" in legacy.locations
    assert legacy.waypoints == {}

    waypoints_only = Locations.model_validate(
        {
            "waypoints": {
                "origin": {"p": [0, 0, 0]},
                "north_station": {
                    "p": [1.25, 0.5, 0.0],
                    "yaw": 1.5708,
                    "xyTolerance": 0.08,
                    "yawTolerance": 0.2,
                },
            }
        }
    )
    assert waypoints_only.locations == {}
    assert waypoints_only.waypoints["origin"].p == [0.0, 0.0, 0.0]
    assert waypoints_only.waypoints["origin"].yaw == 0.0
    assert waypoints_only.waypoints["origin"].site is None
    assert waypoints_only.waypoints["origin"].xyTolerance is None
    assert waypoints_only.waypoints["origin"].yawTolerance is None
    assert waypoints_only.waypoints["north_station"].xyTolerance == pytest.approx(0.08)
    assert waypoints_only.waypoints["north_station"].yawTolerance == pytest.approx(0.2)

    combined = Locations.model_validate(
        {
            "locations": {"bench": {"site": "/World/Lab/Bench", "zone": "bench"}},
            "waypoints": {
                "bench_front": {
                    "p": [0.6, -0.4, 0.0],
                    "yaw": 0,
                    "site": "/World/Lab/FloorBenchFront",
                }
            },
        }
    )
    assert "bench" in combined.locations
    assert combined.waypoints["bench_front"].site == "/World/Lab/FloorBenchFront"


@pytest.mark.parametrize(
    "bad_waypoint",
    [
        {"p": [0.0, 0.0]},
        {"p": [0.0, 0.0, 0.0, 0.0]},
        {"p": [True, 0.0, 0.0]},
        {"p": [math.nan, 0.0, 0.0]},
        {"p": [math.inf, 0.0, 0.0]},
        {"p": [0.0, 0.0, 0.0], "yaw": True},
        {"p": [0.0, 0.0, 0.0], "yaw": math.nan},
        {"p": [0.0, 0.0, 0.0], "xyTolerance": 0.0},
        {"p": [0.0, 0.0, 0.0], "xyTolerance": -0.1},
        {"p": [0.0, 0.0, 0.0], "yawTolerance": 0.0},
        {"p": [0.0, 0.0, 0.0], "yawTolerance": -0.2},
        {"p": [0.0, 0.0, 0.0], "site": ""},
        {"p": [0.0, 0.0, 0.0], "extraField": 1},
    ],
)
def test_waypoint_rejects_invalid_coordinates_tolerances_and_unknown_fields(bad_waypoint):
    with pytest.raises(ValidationError):
        Locations.model_validate({"waypoints": {"wp": bad_waypoint}})


def test_waypoint_rejects_empty_name():
    with pytest.raises(ValidationError):
        Locations.model_validate({"waypoints": {"": {"p": [0.0, 0.0, 0.0]}}})


def test_spatial_domain_and_binding_validation_handle_optional_waypoint_sites(tmp_path):
    loc_file = tmp_path / "locations.yaml"
    loc_file.write_text(
        yaml.safe_dump(
            {
                "waypoints": {
                    "free_floor": {"p": [1.0, 0.0, 0.0], "yaw": 0.5},
                    "marked_floor": {
                        "p": [0.0, 1.0, 0.0],
                        "yaw": 1.57,
                        "site": "/World/Lab/Waypoints/North",
                        "requires": [{"property": "fallen", "equals": False}],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    package = {"path": str(tmp_path), "files": {"locations.yaml": str(loc_file)}}
    normalized = config_locations("locations.yaml", package)
    assert "site" not in normalized["waypoints"]["free_floor"]
    assert normalized["waypoints"]["marked_floor"]["site"] == "/World/Lab/Waypoints/North"

    fields = {"locations": {"documentSchema": "locations"}}
    require_spatial_domain({"locations": normalized}, fields, "/World/Lab")

    outside = {
        "waypoints": {
            "escaped": {"p": [0.0, 0.0, 0.0], "yaw": 0.0, "site": "/Outside/Marker"},
        }
    }
    with pytest.raises(TemplateError, match="outside /World/Lab"):
        require_spatial_domain({"locations": outside}, fields, "/World/Lab")

    definition = {
        "properties": {"fallen": {"schema": {"type": "boolean"}}},
        "actions": {},
    }

    def prop_schema(_entity, prop):
        return definition["properties"][prop]["schema"]

    validate_binding_semantics(
        "/World/Lab/Biped",
        {"config": {"locations": normalized}},
        definition,
        prop_schema,
        sites={"/World/Lab/Waypoints/North"},
    )

    with pytest.raises(ValueError, match="unknown Site"):
        validate_binding_semantics(
            "/World/Lab/Biped",
            {"config": {"locations": normalized}},
            definition,
            prop_schema,
            sites=set(),
        )


def test_scene_library_describe_and_summary_expose_waypoints_without_breaking_legacy_scenes(tmp_path):
    user_root = tmp_path / "user-lib"
    state_root = tmp_path / "state"
    scene_dir = user_root / "scenes/waypoint-only/1"
    scene_dir.mkdir(parents=True)
    (scene_dir / "scene.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "waypoint-only",
                "title": "Waypoint Only Scene",
                "sources": ["newton"],
                "stage": "scene.usda",
            }
        ),
        encoding="utf-8",
    )
    (scene_dir / "scene.usda").write_text('#usda 1.0\ndef Xform "World" {}\n', encoding="utf-8")
    (scene_dir / "devices.yaml").write_text(
        yaml.safe_dump({"scene": "waypoint-only", "entities": []}),
        encoding="utf-8",
    )
    (scene_dir / "locations.yaml").write_text(
        yaml.safe_dump(
            {
                "waypoints": {
                    "dock": {"p": [0.0, 0.0, 0.0], "yaw": 0.0},
                    "checkpoint_a": {"p": [1.5, -0.5, 0.0], "yaw": 0.78, "xyTolerance": 0.1},
                }
            }
        ),
        encoding="utf-8",
    )

    data, bindings, locations = scene_metadata(scene_dir)
    assert data["name"] == "waypoint-only"
    assert "waypoints" in locations

    scenes = SceneLibrary(user_root=user_root, state_root=state_root)
    detail = scenes.describe("waypoint-only@1")
    assert detail["locations"] == {}
    assert set(detail["waypoints"]) == {"dock", "checkpoint_a"}
    assert detail["waypoints"]["checkpoint_a"]["p"] == [1.5, -0.5, 0.0]

    summary = description_view(detail, "scene", "waypoint-only@1", view="summary")
    assert summary["counts"]["locations"] == 0
    assert summary["counts"]["waypoints"] == 2
    assert summary["waypointsPreview"] == ["checkpoint_a", "dock"]

    legacy_detail = scenes.describe("lab-demo@3")
    assert legacy_detail["waypoints"] == {}
    assert len(legacy_detail["locations"]) > 0


def test_p6_arm_taught_checks_ignore_waypoints_only_locations_document():
    from packages.library.physics import taught_checks

    sim = SimpleNamespace(model=SimpleNamespace(shape_count=0))
    payload = {
        "structure": {
            "effectiveBindings": {
                "newton": [
                    {
                        "key": "/World/Lab/Biped",
                        "type": "biped/v1",
                        "program": "biped.policy",
                        "config": {
                            "locations": {
                                "waypoints": {"patrol_1": {"p": [1.0, 0.0, 0.0], "yaw": 0.0}}
                            }
                        },
                    }
                ]
            }
        },
        "programs": {
            "biped.policy": {
                "fields": {"locations": {"documentSchema": "locations"}},
                "configSchema": {},
            }
        },
    }
    rows = taught_checks(sim, payload)
    assert len(rows) == 1
    assert rows[0]["id"] == "P6" and rows[0]["notApplicable"] is True
