import pytest
import yaml
from pydantic import ValidationError

from packages.info.catalog import load_catalog
from packages.info.program import HostCore, ProgramConfig
from packages.protocol.schema import InvokeRequest, SceneManifest


class CounterProgram:
    id, type, tick_priority = "counter.virtual", "counter/v1", 10

    class Config(ProgramConfig):
        fail: bool = False

    def init(self, ctx):
        self.inv = None
        ctx.set(value=0)

    def accept(self, inv, ctx):
        return None

    def start(self, inv, ctx):
        self.inv = inv

    def tick(self, ctx, dt):
        if ctx.config.fail:
            raise RuntimeError("intentional device failure")
        ctx.set(value=ctx.get("value") + 1)

    def cancel(self, inv, ctx):
        inv.cancelled()


def definition_root(tmp_path, *, bindings=None, definition=None):
    (tmp_path / "types").mkdir()
    (tmp_path / "scenes").mkdir()
    definition = definition or {
        "type": "counter/v1",
        "title": "Counter",
        "properties": {"value": {"schema": {"type": "integer"}}},
        "actions": {"work": {"timeoutSeconds": 0.5}},
    }
    (tmp_path / "types/counter.yaml").write_text(yaml.safe_dump(definition), encoding="utf-8")
    bindings = bindings or [
        {"key": "/good", "type": "counter/v1", "program": "counter.virtual", "config": {}},
        {"key": "/bad", "type": "counter/v1", "program": "counter.virtual", "config": {"fail": True}},
    ]
    (tmp_path / "scenes/test.yaml").write_text(
        yaml.safe_dump({"scene": "test", "entities": bindings}), encoding="utf-8"
    )
    manifest = SceneManifest(
        sessionId="test",
        sceneRevision="revision",
        revisionNumericId=1,
        sceneName="test",
        source={"kind": "synthetic", "physicsDt": 1 / 240},
        nodes=[{"kind": "group", "entityKey": key, "name": key} for key in ("/good", "/bad")],
    )
    return manifest


def request(key="/bad", *, epoch=0, token="work", created=0):
    return InvokeRequest(
        invocationId=token,
        requestId=token,
        entityKey=key,
        action="work",
        epoch=epoch,
        createdSimTime=created,
        createdAtMs=0,
    )


def test_program_failure_isolated_three_consecutive_errors_disable_until_reset(tmp_path):
    manifest = definition_root(tmp_path)
    catalog = load_catalog(manifest, "test", {CounterProgram.id: CounterProgram}, root=tmp_path)
    host = HostCore(catalog, {CounterProgram.id: CounterProgram})
    assert host.invoke(request()).status == "accepted"
    first = host.tick(1 / 60, 1 / 60)
    failure = next(e for e in first if e.type == "invocation.update" and e.status == "failed")
    assert failure.error.code == "program_error"
    host.tick(2 / 60, 1 / 60)
    host.tick(3 / 60, 1 / 60)
    assert host.values()["/good"]["value"] == 3
    assert host.invoke(request(token="disabled")).error.code == "source_unavailable"
    events = host.reset(1)
    assert events[-1].type == "entity.snapshot"
    assert host.invoke(request(epoch=1, token="restored")).status == "accepted"


def test_host_deadline_uses_execution_time_and_fails_a_nonterminating_program(tmp_path):
    manifest = definition_root(tmp_path)
    catalog = load_catalog(manifest, "test", {CounterProgram.id: CounterProgram}, root=tmp_path)
    host = HostCore(catalog, {CounterProgram.id: CounterProgram})
    host.tick(100, 1 / 60)
    assert host.invoke(request("/good", created=0)).status == "accepted"
    assert all(not (e.type == "invocation.update" and e.status == "failed") for e in host.tick(100.1, 0.1))
    events = host.tick(100.7, 0.6)
    failure = next(e for e in events if e.type == "invocation.update" and e.status == "failed")
    assert failure.error.code == "timeout" and failure.createdSimTime == 0


def test_program_cannot_write_another_entity_even_with_a_retained_context(tmp_path):
    class Intruder(CounterProgram):
        contexts = {}

        def init(self, ctx):
            super().init(ctx)
            self.contexts[ctx.key] = ctx

        def tick(self, ctx, dt):
            if ctx.key == "/bad":
                self.contexts["/good"].set(value=999)
            else:
                super().tick(ctx, dt)

    manifest = definition_root(tmp_path)
    catalog = load_catalog(manifest, "test", {Intruder.id: Intruder}, root=tmp_path)
    host = HostCore(catalog, {Intruder.id: Intruder})
    events = host.tick(1 / 60, 1 / 60)
    error = next(event for event in events if event.type == "server.error")
    assert "own entity" in error.message
    assert host.values()["/good"]["value"] == 1
    assert host.values()["/bad"]["value"] == 0


@pytest.mark.parametrize(
    "mutation", ["duplicate", "missing", "program_type", "bad_config", "unknown_property", "wrong_operator"]
)
def test_catalog_rejects_invalid_bindings_and_conditions_at_load(tmp_path, mutation):
    manifest = definition_root(tmp_path)
    path = tmp_path / "scenes/test.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    binding = document["entities"][0]
    if mutation == "duplicate":
        document["entities"].append(binding.copy())
    elif mutation == "missing":
        binding["key"] = "/missing"
    elif mutation == "program_type":
        binding["type"] = "other/v1"
    elif mutation == "bad_config":
        binding["config"] = {"unknown": 1}
    else:
        binding["interlocks"] = {
            "work": [
                {
                    "entity": "/bad",
                    "property": "missing" if mutation == "unknown_property" else "value",
                    "equals" if mutation == "unknown_property" else "lt": "text",
                }
            ]
        }
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises((ValueError, KeyError, ValidationError)):
        load_catalog(manifest, "test", {CounterProgram.id: CounterProgram}, root=tmp_path)
