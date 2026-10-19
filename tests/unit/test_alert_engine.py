"""Alert duration and episode semantics at the public pure-engine interface."""

import json
from pathlib import Path

import pytest
import yaml

from packages.info.alerts import AlertEngine, AlertUnavailable
from packages.info.catalog import load_catalog
from packages.info.program import ProgramConfig
from packages.protocol.schema import Actor, SceneManifest

KEY = "/World/Test/Reading"


class ReadingProgram:
    type, id, Config = "reading/v1", "reading.virtual", ProgramConfig


@pytest.fixture
def declared_catalog(tmp_path):
    def create(*, by=1, reference="target", kind="number"):
        root = tmp_path / "devices"
        (root / "types").mkdir(parents=True, exist_ok=True)
        definition = {
            "type": ReadingProgram.type,
            "title": "Reading",
            "properties": {"reading": {"schema": {"type": "number"}}, "target": {"schema": {"type": kind}}},
            "alerts": {
                "deviation": {
                    "when": {"all": [{"property": "reading", "deviatesFrom": reference, "by": by}]},
                    "forSeconds": 2,
                    "severity": "critical",
                    "message": "Reading deviates",
                }
            },
        }
        (root / "types/reading.yaml").write_text(yaml.safe_dump(definition), encoding="utf-8")
        scene = root / "scene.yaml"
        scene.write_text(
            yaml.safe_dump(
                {
                    "scene": "reading",
                    "entities": [
                        {
                            "key": KEY,
                            "type": ReadingProgram.type,
                            "program": ReadingProgram.id,
                            "unplaced": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        manifest = SceneManifest.model_validate(
            json.loads(
                (
                    Path(__file__).resolve().parents[2] / "packages/protocol/fixtures/manifest.json"
                ).read_bytes()
            )
        )
        return load_catalog(
            manifest, "reading", {ReadingProgram.id: ReadingProgram}, root=root, scene_file=scene
        )

    return create


def sample(engine, at, *, reading=39, target=37, metadata=None, epoch=0):
    values = {"reading": reading}
    if target is not None:
        values["target"] = target
    return engine.advance(epoch, at, 1000 + at * 1000, {KEY: values}, metadata or {})


def test_duration_restarts_after_known_false_or_unknown_and_fires_once_at_boundary(declared_catalog):
    engine = AlertEngine(declared_catalog())
    assert sample(engine, 0) == []
    assert sample(engine, 1, reading=37) == []
    assert sample(engine, 2) == []
    assert sample(engine, 3, target=None) == []
    assert sample(engine, 4) == []
    assert sample(engine, 5.999) == []
    [raised] = sample(engine, 6)
    assert raised.since == 4 and raised.simTime == 6 and raised.value == 39
    assert sample(engine, 20) == []


@pytest.mark.parametrize("property", ["reading", "target"])
def test_unavailable_operand_never_clears_an_active_episode(declared_catalog, property):
    engine = AlertEngine(declared_catalog())
    sample(engine, 0)
    [raised] = sample(engine, 2)
    stale = {KEY: {property: {"stale": True}}}
    assert sample(engine, 3, metadata=stale) == []
    assert sample(engine, 4, target=None) == []
    assert engine.get(raised.alertId)["active"] is True
    [cleared] = sample(engine, 5, reading=37)
    assert cleared.alertId == raised.alertId and cleared.state == "cleared" and cleared.value == 37
    assert sample(engine, 6, reading=37) == []


def test_acknowledgement_retries_keep_first_actor_note_and_recurrence_gets_another_id(declared_catalog):
    engine = AlertEngine(declared_catalog())
    sample(engine, 0)
    [raised] = sample(engine, 2)
    actor = Actor(kind="agent", name="first")
    document, messages = engine.acknowledge(raised.alertId, actor, "Checking", 3001)
    assert [message.type for message in messages] == ["entity.alert", "entity.event"]
    assert messages[0].actor == actor
    assert messages[1].data == {
        "alertId": raised.alertId,
        "actor": {"kind": "agent", "name": "first"},
        "note": "Checking",
    }
    again, retries = engine.acknowledge(raised.alertId, Actor(name="second"), "Overwrite", 4000)
    assert again == document and retries == []
    sample(engine, 3, reading=37)
    assert engine.get(raised.alertId)["acknowledgement"]["note"] == "Checking"
    with pytest.raises(AlertUnavailable):
        engine.acknowledge(raised.alertId, actor, "Late", 5000)
    sample(engine, 4)
    [recurrence] = sample(engine, 6)
    assert recurrence.alertId != raised.alertId
    assert recurrence.since == 4 and recurrence.actor is None


def test_reset_and_closed_contexts_preserve_unresolved_state_without_false_recovery(declared_catalog):
    engine = AlertEngine(declared_catalog())
    sample(engine, 0)
    [old] = sample(engine, 2)
    assert sample(engine, 0, epoch=1) == []
    archived = engine.get(old.alertId)
    assert archived["state"] == "raised" and archived["active"] is False
    assert archived["closedContext"]["reason"] == "session_reset"
    assert sample(engine, 100, epoch=0) == []
    [current] = sample(engine, 2, epoch=1)
    assert current.alertId != old.alertId and current.epoch == 1
    engine.close_context("session_stopped")
    assert engine.get(current.alertId)["state"] == "raised"
    assert engine.get(current.alertId)["closedContext"]["reason"] == "session_stopped"
    with pytest.raises(AlertUnavailable):
        engine.acknowledge(current.alertId, Actor(), "Late", 9000)


@pytest.mark.parametrize("by", [True, -1, float("nan"), float("inf")])
def test_deviation_rejects_non_numeric_or_unbounded_thresholds(declared_catalog, by):
    with pytest.raises(ValueError):
        declared_catalog(by=by)


def test_deviation_requires_two_declared_numeric_properties(declared_catalog):
    with pytest.raises(ValueError, match="numeric"):
        declared_catalog(kind="boolean")
    with pytest.raises(KeyError):
        declared_catalog(reference="missing")
