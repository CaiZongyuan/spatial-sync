"""Public HostCore seam; a deterministic external PhysicsPort records real stroke feedback."""

import numpy as np
import yaml

from packages.info.catalog import load_catalog
from packages.info.program import HostCore
from packages.protocol.schema import InvokeRequest, SceneManifest
from packages.programs.cell_passage.workcell import WorkcellProgram


class StrokePort:
    def __init__(self):
        self.q = {key: 0.0 for key in ('x', 'y', 'z', 'pump')}
        self.targets = self.q.copy()

    def bind_joint(self, key):
        return key

    def bind_site(self, key):
        return key

    def joint_q(self, key):
        return np.array([self.q[key]])

    def joint_qd(self, key):
        return np.array([0.0])

    def set_joint_target(self, key, value):
        self.targets[key] = float(value[0])

    def set_joint_target_velocity(self, key, value):
        pass

    def site_pose(self, key):
        return np.zeros(3), np.array([0, 0, 0, 1])

    def advance(self, pump):
        self.q['pump'] = min(pump, self.targets['pump'])


def workcell(tmp_path):
    scene = tmp_path / 'devices.yaml'
    scene.write_text(yaml.safe_dump({'scene': 'test', 'entities': [{
        'key': '/workcell', 'type': WorkcellProgram.type, 'program': WorkcellProgram.id,
        'config': {'axisJoints': ['x', 'y', 'z'], 'pumpJoint': 'pump', 'tipSite': 'tip',
                   'vessels': [{'id': 'source', 'entityKey': '/source', 'site': '/sourceSite',
                                'capacityUl': 1000, 'volumeUl': 500, 'reagent': 'medium'}],
                   'tips': [{'id': 'T1', 'site': '/T1'}]}}]}), encoding='utf8')
    manifest = SceneManifest(sessionId='test', sceneRevision='r', revisionNumericId=1,
                             sceneName='test', source={'kind': 'synthetic', 'physicsDt': 1/240},
                             nodes=[{'kind': 'group', 'entityKey': '/workcell', 'name': 'workcell'}],
                             sites=[{'entityKey': name, 'parentBodyKey': None,
                                     'localPose': {}}
                                    for name in ('/sourceSite', '/T1')])
    registry = {WorkcellProgram.id: WorkcellProgram}
    port = StrokePort()
    host = HostCore(load_catalog(manifest, 'test', registry, scene_file=scene), registry, physics=port)
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

