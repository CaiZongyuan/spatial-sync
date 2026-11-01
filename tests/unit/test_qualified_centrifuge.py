"""Real centrifuge program handoff state over explicitly synthetic rotor feedback."""

from types import SimpleNamespace

from packages.programs.labware.assembly import attached_bodies
from tests.fixtures.centrifuge_cycles import advance_until_terminal, build_cycle_host
from tests.unit.test_centrifuge_cycle_receipts import start_cycle


def test_qualified_cycle_reuses_existing_slot_constraints_and_retains_them_at_idle(tmp_path):
    host, port, _ = build_cycle_host(tmp_path)
    program = next(p for p in host._programs if p.id == "cell_passage.centrifuge")
    program.qualified_placement = True
    original = {(0, body) for body in range(1, 5)}
    port.welded.update(original)
    port.welds = SimpleNamespace(
        active=lambda parent, body: (parent, body) in port.welded,
        attached_bodies=lambda roots: attached_bodies(roots, port.welded),
    )
    port.collision_contacts = lambda **kwargs: []
    calls = []
    port.weld = lambda *args: calls.append(("attach", *args))
    port.unweld = lambda *args: calls.append(("detach", *args))
    assert start_cycle(host).status == "accepted"
    terminal = advance_until_terminal(host, port, "cycle")
    assert terminal.status == "succeeded", terminal
    assert port.welded == original and calls == []
    assert host.values()["/cent"]["phase"] == "idle"
