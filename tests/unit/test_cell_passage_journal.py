"""Cabinet interlock and retained operation evidence through the public Host seam."""

import pytest

from packages.protocol.schema import InvokeRequest
from tests.fixtures.cell_passage import build_workcell_host
from tests.unit.test_cell_passage import execute, invoke


def set_ready(host, ready, token):
    result = host.invoke(InvokeRequest(invocationId=token, requestId=token, entityKey='/cabinet',
        action='setReady', epoch=0, createdSimTime=host.sim_time, createdAtMs=0, params={'ready': ready}))
    assert result.status == 'accepted'


@pytest.mark.parametrize('ready', [False, None])
def test_liquid_admission_requires_confirmed_cabinet_readiness(tmp_path, ready):
    host, _ = build_workcell_host(tmp_path, cabinet_ready=ready)
    before = host.values()['/workcell']['materialSnapshot']
    result = invoke(host, 'aspirate', 'asp', vessel='source', volumeUl=50)
    assert result.error.code == 'cabinet_not_ready'
    assert host.values()['/workcell']['materialSnapshot'] == before


def test_cabinet_loss_before_start_rejects_without_material_changes(tmp_path):
    host, port = build_workcell_host(tmp_path)
    execute(host, port, 'reserve', 'reserve', resource='/source', device='/workcell')
    execute(host, port, 'pickupTip', 'pick', tip='T1')
    invoke(host, 'aspirate', 'asp', vessel='source', volumeUl=100)
    set_ready(host, False, 'cabinet-off')
    host.tick(host.sim_time + .1, .1)
    terminal = next(row for row in host.snapshot().invocations if row.invocationId == 'asp')
    assert terminal.status == 'failed' and terminal.error.code == 'cabinet_not_ready'
    assert host.values()['/workcell']['inFlightUl'] == 0


@pytest.mark.parametrize('ready', [False, None])
def test_cabinet_loss_accounts_braking_then_fails_with_partial_liquid_and_tip_retained(tmp_path, ready):
    host, port = build_workcell_host(tmp_path)
    execute(host, port, 'reserve', 'reserve', resource='/source', device='/workcell')
    execute(host, port, 'pickupTip', 'pick', tip='T1')
    invoke(host, 'aspirate', 'asp', vessel='source', volumeUl=100)
    host.tick(host.sim_time + .1, .1)
    port.advance(.0004)
    port.velocities['pump'] = .001
    set_ready(host, ready, 'cabinet-off')
    host.tick(host.sim_time + .1, .1)
    state = host.values()['/workcell']
    assert state['inFlightUl'] == pytest.approx(40)
    assert state['phase'] == 'holding' and state['controlSafe'] is False
    assert host.active_invocations('/workcell')[0].status == 'running'
    port.q['pump'] = .00045
    host.tick(host.sim_time + .1, .1)
    assert host.values()['/workcell']['inFlightUl'] == pytest.approx(45)
    port.q['pump'], port.velocities['pump'] = .0004, 0
    host.tick(host.sim_time + .1, .1)
    terminal = next(row for row in host.snapshot().invocations if row.invocationId == 'asp')
    assert terminal.status == 'failed' and terminal.error.code == 'cabinet_not_ready'
    state = host.values()['/workcell']
    assert state['materialSnapshot']['vessels'][0]['volumeUl'] == pytest.approx(460)
    assert state['materialSnapshot']['tip']['id'] == 'T1' and ('/pipette', '/tip1') in port.welds
    assert state['inFlightUl'] == pytest.approx(40) and state['auditPassed'] is True
