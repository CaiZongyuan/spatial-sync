"""Public HostCore seam; a deterministic external PhysicsPort records real stroke feedback."""

import numpy as np
import pytest
import yaml
from pathlib import Path

from packages.info.catalog import load_catalog
from packages.info.program import HostCore, ProgramConfig
from packages.protocol.schema import InvokeRequest, SceneManifest
from packages.programs.cell_passage.workcell import WorkcellProgram


class StrokePort:
    def __init__(self):
        self.q = {key: 0.0 for key in ('x', 'y', 'z', 'pump')}
        self.targets = self.q.copy()
        self.welds = set()
        self.velocities = self.q.copy()

    def bind_body(self, key):
        return key

    def weld(self, parent, child):
        self.welds.add((parent, child))

    def unweld(self, parent, child):
        self.welds.remove((parent, child))

    def bind_joint(self, key):
        return key

    def bind_site(self, key):
        return key

    def joint_q(self, key):
        return np.array([self.q[key]])

    def joint_qd(self, key):
        return np.array([self.velocities[key]])

    def set_joint_target(self, key, value):
        self.targets[key] = float(value[0])

    def set_joint_target_velocity(self, key, value):
        pass

    def site_pose(self, key):
        return np.zeros(3), np.array([0, 0, 0, 1])

    def advance(self, pump):
        self.q['pump'] = float(np.clip(pump, min(self.q['pump'], self.targets['pump']),
                                      max(self.q['pump'], self.targets['pump'])))


class FactsProgram:
    id, type, tick_priority = 'facts.test', 'facts-test/v1', 1

    class Config(ProgramConfig):
        station: str = '/imager'
        settled: bool = True
        completedCycle: str = 'cycle-1'
        completedLoad: list[str] = ['/source']
        cycleRunSeconds: float = 2
        actualRotorTurns: float = 1
        phase: str = 'idle'

    def init(self, ctx):
        ctx.set(**ctx.config.model_dump())


def workcell(tmp_path, *, vessels=None, config=None, facts=None):
    bindings = []
    if facts is not None:
        bindings.append({'key': '/peer', 'type': FactsProgram.type, 'program': FactsProgram.id,
                         'config': facts})
    scene = tmp_path / 'devices.yaml'
    bindings.append({
        'key': '/workcell', 'type': WorkcellProgram.type, 'program': WorkcellProgram.id,
        'config': {'axisJoints': ['x', 'y', 'z'], 'pumpJoint': 'pump', 'tipSite': 'tip', 'pipetteBody': '/pipette',
                   'vessels': vessels or [{'id': 'source', 'entityKey': '/source', 'site': '/sourceSite',
                                'capacityUl': 1000, 'volumeUl': 500, 'reagent': 'medium'}],
                   'tips': [{'id': 'T1', 'site': '/T1', 'bodyKey': '/tip1'}], **(config or {})}})
    scene.write_text(yaml.safe_dump({'scene': 'test', 'entities': bindings}), encoding='utf8')
    manifest = SceneManifest(sessionId='test', sceneRevision='r', revisionNumericId=1,
                             sceneName='test', source={'kind': 'synthetic', 'physicsDt': 1/240},
                             nodes=[{'kind': 'group', 'entityKey': row['key'], 'name': row['key']} for row in bindings],
                             sites=[{'entityKey': name, 'parentBodyKey': None,
                                     'localPose': {}}
                                    for name in ('/sourceSite', '/T1')])
    registry = {WorkcellProgram.id: WorkcellProgram, FactsProgram.id: FactsProgram}
    (tmp_path / 'types').mkdir()
    definition = Path('devices/types/cell-passage-workcell.v1.yaml').read_text(encoding='utf8')
    (tmp_path / 'types/workcell.yaml').write_text(definition, encoding='utf8')
    fields = FactsProgram.Config.model_fields
    properties = {name: {'schema': {'type': 'array', 'items': {'type': 'string'}} if name == 'completedLoad'
                  else {'type': 'boolean' if name == 'settled' else 'number' if name in ('cycleRunSeconds', 'actualRotorTurns') else 'string'}}
                  for name in fields}
    (tmp_path / 'types/facts.yaml').write_text(yaml.safe_dump({'type': FactsProgram.type,
        'title': 'External mechanical facts', 'properties': properties}), encoding='utf8')
    port = StrokePort()
    host = HostCore(load_catalog(manifest, 'test', registry, root=tmp_path, scene_file=scene), registry, physics=port)
    return host, port


def invoke(host, action, token, **params):
    return host.invoke(InvokeRequest(invocationId=token, requestId=token, entityKey='/workcell',
        action=action, epoch=0, createdSimTime=host.sim_time, createdAtMs=0,
        params={'owner': 'auto', 'epoch': 0, 'operationId': token, **params}))


def test_partial_stroke_cancel_retains_liquid_and_operation_identity(tmp_path):
    host, port = workcell(tmp_path)
    assert invoke(host, 'reserve', 'reserve', resource='/source', device='/workcell').status == 'accepted'
    host.tick(.05, .05)
    assert invoke(host, 'pickupTip', 'pick', tip='T1').status == 'accepted'
    host.tick(.1, .1)
    assert invoke(host, 'aspirate', 'asp', vessel='source', volumeUl=100).status == 'accepted'
    host.tick(.2, .1)
    port.advance(.0004)
    host.tick(.3, .1)
    host.cancel('asp')
    state = host.values()['/workcell']['materialSnapshot']
    assert state['vessels'][0]['volumeUl'] == 460
    assert state['tip']['volumeUl'] == 40
    assert state['operations'][-1]['appliedUl'] == 40
    assert port.targets['pump'] == .0004
    retry = invoke(host, 'aspirate', 'new-request', operationId='asp', vessel='source', volumeUl=100)
    assert retry.error.code == 'operation_applied'


def test_paused_control_accept_stops_old_targets_and_start_only_acknowledges(tmp_path):
    host, port = workcell(tmp_path)
    invoke(host, 'move', 'move', position=[.2, .1, .05])
    host.tick(.1, .1)
    assert port.targets['x'] == .2
    assert invoke(host, 'requestControl', 'handoff', nextOwner='human', mode='manual').status == 'accepted'
    state = host.values()['/workcell']
    assert (state['controlEpoch'], state['controlMode'], state['controlSafe']) == (1, 'holding', True)
    assert port.targets['x'] == 0
    assert invoke(host, 'move', 'late', position=[1, 1, 1]).error.code == 'stale_control'
    # A second request identity with the same domain identity cannot apply a second transition.
    assert invoke(host, 'requestControl', 'retry', operationId='handoff', nextOwner='human', mode='manual').status == 'accepted'
    host.tick(.2, .1)
    assert host.values()['/workcell']['controlEpoch'] == 1
    assert invoke(host, 'confirmControl', 'confirm', epoch=1).status == 'accepted'
    assert host.values()['/workcell']['controlOwner'] == 'human'
    host.tick(.3, .1)
    assert (host.values()['/workcell']['controlEpoch'], host.values()['/workcell']['controlMode']) == (1, 'manual')


def test_mix_cancel_retains_actual_aspirated_fraction_of_cells(tmp_path):
    host, port = workcell(tmp_path, vessels=[{'id': 'source', 'entityKey': '/source',
        'site': '/sourceSite', 'capacityUl': 1000, 'volumeUl': 500, 'culture': 'M1',
        'live': 1000, 'dead': 100}])
    invoke(host, 'reserve', 'res', resource='/source', device='/workcell')
    host.tick(.05, .05)
    invoke(host, 'pickupTip', 'pick', tip='T1')
    host.tick(.1, .05)
    invoke(host, 'mix', 'mix', vessel='source', volumeUl=100)
    host.tick(.2, .1)
    port.advance(.0004)
    host.tick(.3, .1)
    host.cancel('mix')
    state = host.values()['/workcell']['materialSnapshot']
    assert state['tip']['volumeUl'] == 40
    assert state['tip']['cells'][0]['suspendedLive'] == 80
    assert state['vessels'][0]['cells'][0]['suspendedLive'] == 920


def test_partial_dispense_uses_reverse_executed_stroke_and_conserves_cells(tmp_path):
    host, port = workcell(tmp_path, vessels=[{'id': 'source', 'entityKey': '/source',
        'site': '/sourceSite', 'capacityUl': 1000, 'volumeUl': 500, 'culture': 'M1',
        'live': 1000, 'dead': 100}, {'id': 'child', 'entityKey': '/child',
        'site': '/sourceSite', 'capacityUl': 1000, 'culture': 'D1', 'parent': 'M1'}])
    for resource in ('/source', '/child'):
        invoke(host, 'reserve', resource, resource=resource, device='/workcell')
        host.tick(host.sim_time + .05, .05)
    invoke(host, 'pickupTip', 'pick', tip='T1')
    host.tick(.2, .1)
    invoke(host, 'aspirate', 'asp', vessel='source', volumeUl=100)
    host.tick(.3, .1)
    port.advance(.001)
    host.tick(.4, .1)
    assert invoke(host, 'dispense', 'disp', vessel='child', volumeUl=100).status == 'accepted'
    host.tick(.5, .1)
    assert port.targets['pump'] == 0
    port.advance(.0006)
    host.tick(.6, .1)
    host.cancel('disp')
    state = host.values()['/workcell']['materialSnapshot']
    assert abs(state['tip']['volumeUl'] - 60) < 1e-8
    assert abs(state['vessels'][1]['volumeUl'] - 40) < 1e-8
    assert abs(state['vessels'][1]['cells'][0]['suspendedLive'] - 80) < 1e-8
    assert state['vessels'][1]['parent'] == 'M1'
    assert host.values()['/workcell']['auditPassed'] is True


def test_handoff_requires_measured_standstill_not_only_written_hold_target(tmp_path):
    host, port = workcell(tmp_path)
    invoke(host, 'move', 'move', position=[.2, .1, .05])
    host.tick(.1, .1)
    port.velocities['x'] = .5
    invoke(host, 'requestControl', 'handoff', nextOwner='human', mode='manual')
    assert host.values()['/workcell']['controlSafe'] is False
    assert invoke(host, 'confirmControl', 'premature', epoch=1).error.code == 'not_safe'
    port.velocities['x'] = 0
    host.tick(.2, .1)
    assert invoke(host, 'confirmControl', 'settled', epoch=1).status == 'accepted'


@pytest.mark.parametrize('invalid_feedback', [.0015, .0002])
def test_pump_overtravel_or_reverse_feedback_cannot_report_a_balanced_transfer(tmp_path, invalid_feedback):
    host, port = workcell(tmp_path)
    invoke(host, 'reserve', 'res', resource='/source', device='/workcell')
    host.tick(.05, .05)
    invoke(host, 'pickupTip', 'pick', tip='T1')
    host.tick(.1, .05)
    invoke(host, 'aspirate', 'asp', vessel='source', volumeUl=100)
    host.tick(.2, .1)
    port.advance(.0004)
    host.tick(.3, .1)
    port.q['pump'] = invalid_feedback  # External actuator fault, beyond target or reversing.
    host.tick(.4, .1)
    state = host.values()['/workcell']
    assert state['auditPassed'] is False
    assert state['materialSnapshot']['reconciled'] is False
    assert state['materialSnapshot']['operations'][-1]['fault'] == 'pump_feedback_invalid'
    assert state['materialSnapshot']['operations'][-1]['observedUl'] == pytest.approx(invalid_feedback * 100000)
    assert port.targets['pump'] == invalid_feedback


def test_model_pellet_requires_actual_completed_load_and_applies_cycle_once(tmp_path):
    host, _ = workcell(tmp_path, vessels=[{'id': 'source', 'entityKey': '/source',
        'site': '/sourceSite', 'capacityUl': 1000, 'volumeUl': 500, 'culture': 'M1', 'live': 1000}], facts={})
    assert invoke(host, 'pellet', 'wrong-cycle', centrifuge='/peer', cycle='missing', vessels=['source']).error.code == 'cycle_incomplete'
    assert invoke(host, 'pellet', 'pellet', centrifuge='/peer', cycle='cycle-1', vessels=['source']).status == 'accepted'
    host.tick(.1, .1)
    cell = host.values()['/workcell']['materialSnapshot']['vessels'][0]['cells'][0]
    assert (cell['suspendedLive'], cell['pelletLive']) == (0, 1000)
    assert invoke(host, 'pellet', 'duplicate', centrifuge='/peer', cycle='cycle-1', vessels=['source']).error.code == 'cycle_applied'


@pytest.mark.parametrize('facts,code', [({'completedLoad': []}, 'wrong_load'),
    ({'cycleRunSeconds': .1}, 'cycle_incomplete'), ({'actualRotorTurns': 0}, 'cycle_incomplete'),
    ({'phase': 'decelerating'}, 'cycle_incomplete')])
def test_pellet_rejects_unproven_physical_processing(tmp_path, facts, code):
    host, _ = workcell(tmp_path, facts=facts)
    assert invoke(host, 'pellet', 'pellet', centrifuge='/peer', cycle='cycle-1', vessels=['source']).error.code == code


def test_count_is_simulated_observation_of_actual_loaded_sample_and_never_changes_truth(tmp_path):
    host, _ = workcell(tmp_path, vessels=[{'id': 'sample', 'entityKey': '/peer',
        'site': '/sourceSite', 'capacityUl': 100, 'volumeUl': 20, 'culture': 'M1', 'live': 80, 'dead': 8}],
        config={'imagerStation': '/imager'}, facts={})
    invoke(host, 'reserve', 'res', resource='/peer', device='/workcell')
    host.tick(.1, .1)
    assert invoke(host, 'count', 'count', vessel='sample').status == 'accepted'
    host.tick(.2, .1)
    state = host.values()['/workcell']['materialSnapshot']
    assert state['observations'][-1]['source'] == 'simulated-state-model'
    assert (state['observations'][-1]['live'], state['observations'][-1]['dead']) == (80, 8)
    assert state['vessels'][0]['volumeUl'] == 20


def test_teaching_wait_only_advances_with_simulation_clock(tmp_path):
    host, _ = workcell(tmp_path)
    assert invoke(host, 'wait', 'wait', seconds=2).status == 'accepted'
    host.tick(.1, .1)
    host.tick(.1, .1)
    assert host.active_invocations('/workcell')[0].status == 'running'
    host.tick(2, 1.9)
    assert host.active_invocations('/workcell')[0].status == 'running'
    host.tick(2.1, .1)
    assert host.active_invocations('/workcell') == []

