"""Pure authoring checks; no Host, Source, broker or client is started."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from packages.live.bindings import LiveBindings, live_catalog, load_bindings

EXAMPLE = Path(__file__).parents[2] / "devices/live/lab.yaml"


def document():
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def test_example_compiles_three_live_entities_and_preserves_catalog_shape():
    bindings = load_bindings(EXAMPLE)
    catalog = live_catalog(bindings, "live-test")
    assert set(catalog.entities) == {
        "/Real/Incubator2", "/Real/BiosafetyCabinet1", "/Real/Environment1",
    }
    assert catalog.action("/Real/Incubator2", "setSetpoint")["risk"] == "high"
    assert catalog.action("/Real/BiosafetyCabinet1", "setUv")["risk"] == "high"
    assert all("placeAt" not in binding for binding in catalog.entities.values())


@pytest.mark.parametrize("mutate", [
    lambda doc: doc["entities"][0].update(key="/World/Incubator2"),
    lambda doc: doc["entities"].append(deepcopy(doc["entities"][0])),
    lambda doc: doc["entities"][0].update(placeAt="/World/Lab/Incubator2"),
    lambda doc: doc.update(layout="fr3-lab"),
    lambda doc: doc["broker"].update(url="http://localhost:1883"),
    lambda doc: doc["broker"].update(url="mqtt://localhost:0"),
])
def test_live_binding_rejects_identity_layout_and_broker_errors(mutate):
    value = document()
    mutate(value)
    with pytest.raises(ValidationError):
        LiveBindings.model_validate(value)


@pytest.mark.parametrize("mutate", [
    lambda entity: entity.update(program="incubator.virtual"),
    lambda entity: entity["config"]["telemetry"]["map"].update(unknown="$.temp"),
    lambda entity: entity["config"]["commands"].update(unknown={"topic": "cmd", "ack": "ack"}),
    lambda entity: entity["config"]["telemetry"].update(topic="lab/+/telemetry"),
    lambda entity: entity["config"].update(statusTopic=entity["config"]["telemetry"]["topic"]),
])
def test_compilation_rejects_unknown_program_property_action_and_ambiguous_topics(mutate):
    value = document()
    mutate(value["entities"][0])
    with pytest.raises(ValueError):
        live_catalog(LiveBindings.model_validate(value), "live-test")


def test_layout_placement_is_readonly_metadata_and_does_not_change_catalog_contract():
    value = document()
    value["layout"] = "fr3-lab@2"
    value["entities"][0]["placeAt"] = "/World/Lab/Incubator"
    bindings = LiveBindings.model_validate(value)
    assert bindings.entities[0].placeAt == "/World/Lab/Incubator"
    assert "placeAt" not in live_catalog(bindings, "live-test").entities["/Real/Incubator2"]


@pytest.mark.parametrize("other_type", ["incubator", "cabinet"])
def test_different_devices_cannot_share_an_unaddressed_command_topic(other_type):
    value = document()
    first = value["entities"][0]
    topic = first["config"]["commands"]["setSetpoint"]["topic"]
    if other_type == "incubator":
        other = deepcopy(first)
        other["key"] = "/Real/Incubator3"
        other["config"]["telemetry"]["topic"] = "lab/incubator3/telemetry"
        other["config"]["statusTopic"] = "lab/incubator3/status"
        other["config"]["commands"]["setSetpoint"]["ack"] = "lab/incubator3/ack"
        value["entities"].append(other)
    else:
        other = value["entities"][1]
        other["config"]["commands"]["setUv"]["topic"] = topic
    with pytest.raises(ValueError) as error:
        live_catalog(LiveBindings.model_validate(value), "live-test")
    assert all(identity in str(error.value) for identity in (first["key"], other["key"], topic))


def test_one_device_can_share_its_command_topic_across_actions():
    catalog = live_catalog(load_bindings(EXAMPLE), "live-test")
    commands = catalog.program_config("/Real/BiosafetyCabinet1").commands
    assert commands["setBlower"].topic == commands["setUv"].topic == "lab/biosafety1/cmd"


@pytest.mark.parametrize("distinct_topic", ["Lab/incubator2/cmd", "/lab/incubator2/cmd"])
def test_command_topic_ownership_preserves_exact_mqtt_topic_semantics(distinct_topic):
    value = document()
    value["entities"][1]["config"]["commands"]["setUv"]["topic"] = distinct_topic
    live_catalog(LiveBindings.model_validate(value), "live-test")
