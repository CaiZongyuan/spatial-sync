"""Publication work, pending final values and canonical Host event ordering."""

from test_info_host import CounterProgram, definition_root

from packages.info.catalog import load_catalog
from packages.info.program import HostCore
from packages.protocol.schema import InvokeRequest


class WriterProgram(CounterProgram):
    def init(self, ctx):
        self.ticks = 0
        ctx.set(value=0, fast=0)

    def start(self, inv, ctx):
        ctx.set(**inv.params["values"])
        ctx.emit("written", values=inv.params["values"])
        inv.succeed()

    def tick(self, ctx, dt):
        self.ticks += 1


def publication_host(tmp_path):
    properties = {
        "value": {"schema": {"type": "integer"}, "deadband": 2, "maxRateHz": 2},
        "fast": {"schema": {"type": "integer"}},
    }
    manifest = definition_root(
        tmp_path,
        bindings=[
            {"key": key, "type": WriterProgram.type, "program": WriterProgram.id}
            for key in ("/good", "/bad")
        ],
        definition={
            "type": WriterProgram.type,
            "title": "Publication fixture",
            "properties": properties,
            "actions": {
                "work": {
                    "params": {
                        "values": {
                            "type": "object",
                            "properties": {key: value["schema"] for key, value in properties.items()},
                        }
                    },
                    "required": ["values"],
                    "concurrent": True,
                }
            },
            "events": {"written": {}},
        },
    )
    registry = {WriterProgram.id: WriterProgram}
    catalog = load_catalog(manifest, "test", registry, root=tmp_path)
    now = [0.0]
    return HostCore(catalog, registry, publication_clock=lambda: now[0]), now


def write(host, key, token, **values):
    accepted = host.invoke(
        InvokeRequest(
            invocationId=token,
            requestId=token,
            entityKey=key,
            action="work",
            params={"values": values},
            epoch=host.epoch,
            createdSimTime=host.sim_time,
            createdAtMs=0,
        )
    )
    assert accepted.status == "accepted"


def tick(host):
    return host.tick(host.sim_time + 1 / 60, 1 / 60)


def test_idle_entities_do_no_diff_work_but_pending_final_values_flush_before_terminal(tmp_path, monkeypatch):
    host, now = publication_host(tmp_path)
    calls = []
    for state in host._states:
        original = state.diff.update

        def observed(values, at, *, force=(), original=original, key=state.key):
            calls.append(key)
            return original(values, at, force=force)

        monkeypatch.setattr(state.diff, "update", observed)
    assert host.drain_events() == []
    assert calls == []
    now[0] = 0.1
    write(host, "/good", "pending", value=3, fast=1)
    first = tick(host)
    assert [event.type for event in first] == ["entity.observations"]
    assert first[0].changes[0].values == {"fast": 1}
    assert calls == ["/good"]
    assert [program.ticks for program in host._programs] == [1, 1]
    frozen_time = host.sim_time
    now[0] = 0.2
    assert host.drain_events() == []
    now[0] = 0.5
    flushed = host.drain_events()
    assert [event.type for event in flushed] == ["entity.observations", "entity.event", "invocation.update"]
    assert flushed[0].changes[0].values == {"value": 3}
    assert flushed[-1].status == "succeeded"
    assert host.sim_time == frozen_time
    assert calls == ["/good"] * 3
    assert host.drain_events() == []
    assert calls == ["/good"] * 3


def test_sparse_publications_keep_priority_and_entity_order_then_events_then_terminals(tmp_path):
    host, now = publication_host(tmp_path)
    now[0] = 0.5
    # Catalog index and intake order are opposite to canonical Host key order.
    write(host, "/good", "good", value=3, fast=1)
    write(host, "/bad", "bad", value=4, fast=2)
    messages = tick(host)
    assert [message.type for message in messages] == [
        "entity.observations", "entity.event", "entity.event", "invocation.update", "invocation.update"
    ]
    assert [change.key for change in messages[0].changes] == ["/bad", "/good"]
    assert [change.rev for change in messages[0].changes] == [4, 4]
    assert [message.entityKey for message in messages[1:]] == ["/bad", "/good", "/bad", "/good"]
    assert all(message.status == "succeeded" for message in messages[-2:])


def test_return_to_published_value_clears_pending_and_reset_drops_old_epoch_publications(tmp_path):
    host, now = publication_host(tmp_path)
    now[0] = 0.1
    write(host, "/good", "outbound", value=3)
    assert tick(host) == []
    now[0] = 0.2
    write(host, "/good", "returned", value=0)
    returned = tick(host)
    assert [message.type for message in returned] == [
        "entity.event", "entity.event", "invocation.update", "invocation.update"
    ]
    assert host.drain_events() == []
    write(host, "/good", "before-reset", value=4)
    assert tick(host) == []
    reset = host.reset(1)
    snapshot = next(message for message in reset if message.type == "entity.snapshot")
    assert snapshot.epoch == 1
    assert all(entity.values == {"value": 0, "fast": 0} for entity in snapshot.entities)
    now[0] = 0.7
    assert host.drain_events() == []
    write(host, "/good", "after-reset", value=5)
    current = tick(host)
    assert current[0].epoch == 1 and current[0].changes[0].values == {"value": 5}
    assert current[-1].status == "succeeded" and current[-1].invocationId == "after-reset"
