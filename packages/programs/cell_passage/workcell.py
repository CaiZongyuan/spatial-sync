"""Flex actuator owner and atomic, recorded material/control state.

Liquid is a symbolic model driven by measured pump displacement, never elapsed
time or the requested target. PhysicsPort supplies the mechanical observations.
"""

from copy import deepcopy

import numpy as np
from pydantic import Field

from packages.info.program import ProgramConfig, Reject

from .guard import guard


class Vessel(ProgramConfig):
    id: str
    entityKey: str
    site: str
    capacityUl: float = Field(gt=0)
    volumeUl: float = Field(default=0, ge=0)
    reagent: str = ''
    culture: str = ''
    parent: str = ''
    live: float = Field(default=0, ge=0)
    dead: float = Field(default=0, ge=0)
    attached: bool = False
    waste: bool = False
    lidProperty: str = ''
    requiredStation: str = ''


class Tip(ProgramConfig):
    id: str
    site: str


def empty_content():
    return {'volumeUl': 0.0, 'components': [], 'cells': []}


def move_content(source, target, amount):
    """Move only homogeneous suspended material; attached/pelleted cells remain."""
    fraction = amount / source['volumeUl'] if source['volumeUl'] else 0
    for component in source['components']:
        moved = component['volumeUl'] * fraction
        component['volumeUl'] -= moved
        existing = next((row for row in target['components'] if row['name'] == component['name']), None)
        if existing is None:
            existing = {'name': component['name'], 'volumeUl': 0.0}
            target['components'].append(existing)
        existing['volumeUl'] += moved
    for cell in source['cells']:
        existing = next((row for row in target['cells'] if row['culture'] == cell['culture']), None)
        if existing is None:
            existing = {key: (cell[key] if key in ('culture', 'parent') else 0.0) for key in cell}
            target['cells'].append(existing)
        for key in ('suspendedLive', 'suspendedDead'):
            moved = cell[key] * fraction
            cell[key] -= moved
            existing[key] += moved
    source['volumeUl'] -= amount
    target['volumeUl'] += amount


class WorkcellProgram:
    id, type, tick_priority = 'cell_passage.workcell', 'cell-passage-workcell/v1', 5

    class Config(ProgramConfig):
        axisJoints: list[str] = Field(min_length=3, max_length=3, json_schema_extra={'spatialReference': 'joint'})
        pumpJoint: str = Field(json_schema_extra={'spatialReference': 'joint'})
        tipSite: str = Field(json_schema_extra={'spatialReference': 'site'})
        vessels: list[Vessel]
        tips: list[Tip]
        managedDevices: list[str] = []
        pumpUlPerUnit: float = Field(default=100000, gt=0)
        tipCapacityUl: float = Field(default=1000, gt=0)
        alignmentToleranceM: float = Field(default=.003, gt=0)
        axisToleranceM: float = Field(default=.0005, gt=0)
        wasteSite: str = ''
        detachSeconds: float = Field(default=2, gt=0)

    def init(self, ctx):
        self.axes = [ctx.physics.bind_joint(key) for key in ctx.config.axisJoints]
        self.pump = ctx.physics.bind_joint(ctx.config.pumpJoint)
        self.tip_site = ctx.physics.bind_site(ctx.config.tipSite)
        self.configs = {row.id: row for row in ctx.config.vessels}
        self.sites = {row.id: ctx.physics.bind_site(row.site) for row in ctx.config.vessels}
        self.tip_sites = {row.id: ctx.physics.bind_site(row.site) for row in ctx.config.tips}
        self.handle, self.stroke, self.pending = None, None, None
        vessels = []
        for config in ctx.config.vessels:
            row = empty_content() | {'id': config.id, 'entityKey': config.entityKey,
                'capacityUl': config.capacityUl, 'culture': config.culture, 'parent': config.parent,
                'waste': config.waste, 'mixed': True, 'exposureSince': -1.0, 'processedCycle': ''}
            row['volumeUl'] = config.volumeUl
            if config.volumeUl:
                row['components'] = [{'name': config.reagent or 'medium', 'volumeUl': config.volumeUl}]
            if config.culture:
                row['cells'] = [{'culture': config.culture, 'parent': config.parent,
                    'attachedLive': config.live if config.attached else 0.0,
                    'attachedDead': config.dead if config.attached else 0.0,
                    'suspendedLive': 0.0 if config.attached else config.live,
                    'suspendedDead': 0.0 if config.attached else config.dead,
                    'pelletLive': 0.0, 'pelletDead': 0.0}]
            vessels.append(row)
        self.state = {'revision': 0, 'vessels': vessels,
            'tips': [{'id': row.id, 'state': 'unused'} for row in ctx.config.tips],
            'tip': empty_content() | {'id': '', 'contactCulture': ''},
            'reservations': [], 'operations': [], 'observations': [], 'controlRequests': []}
        self.initial_volume = sum(row['volumeUl'] for row in vessels)
        ctx.set(controlOwner='auto', controlEpoch=0, controlMode='auto', controlSafe=True,
                phase='idle', materialSnapshot=self.state, materialRevision=0, auditPassed=True,
                lastOperationId='', lastAppliedUl=0.0, remainingUl=0.0, observationRevision=0)

    def _publish(self, ctx):
        self.state['revision'] += 1
        total = sum(row['volumeUl'] for row in self.state['vessels']) + self.state['tip']['volumeUl']
        ctx.set(materialSnapshot=self.state, materialRevision=self.state['revision'],
                auditPassed=abs(total - self.initial_volume) < 1e-6)

    def _vessel(self, name):
        return next((row for row in self.state['vessels'] if row['id'] == name), None)

    def _aligned(self, ctx, site):
        return np.linalg.norm(np.array(ctx.physics.site_pose(self.tip_site)[0])
                              - np.array(ctx.physics.site_pose(site)[0])) <= ctx.config.alignmentToleranceM

    def _accessible(self, ctx, name):
        config = self.configs[name]
        if config.lidProperty and ctx.read(config.entityKey, config.lidProperty) is not False:
            return Reject(code='closed_vessel', message='Vessel lid must be confirmed open')
        if config.requiredStation and (ctx.read(config.entityKey, 'station') != config.requiredStation
                                       or ctx.read(config.entityKey, 'settled') is not True):
            return Reject(code='wrong_station', message='Vessel is not settled at its operation station')
        if not self._aligned(ctx, self.sites[name]):
            return Reject(code='not_aligned', message='Actual pipette tip is not aligned with the vessel')

    def _reject(self, code, message):
        return Reject(code=code, message=message)

    def accept(self, inv, ctx):
        p, action = inv.params, inv.action
        if any(row['operationId'] == p['operationId'] for row in self.state['operations']):
            return self._reject('operation_applied', 'This domain operation has already been applied; use current remainder')
        if action in ('requestControl', 'confirmControl'):
            return self._control(inv, ctx, commit=True)
        rejection = guard(ctx, ctx.key, p)
        if rejection:
            return rejection
        if action == 'reserve':
            for row in self.state['reservations']:
                if row['resource'] == p['resource'] and row['device'] != p['device']:
                    return self._reject('resource_busy', 'Resource is already reserved by another device')
        if action == 'release':
            if self.handle or self.state['tip']['volumeUl'] > 1e-8:
                return self._reject('unsafe_release', 'Stop motion and resolve in-flight liquid before release')
            if any(ctx.read(key, 'controlSafe') is not True for key in ctx.config.managedDevices):
                return self._reject('unsafe_release', 'A managed device has not established safe hold')
        if action == 'pickupTip':
            tip = next((row for row in self.state['tips'] if row['id'] == p['tip']), None)
            if self.state['tip']['id'] or tip is None or tip['state'] != 'unused':
                return self._reject('tip_unavailable', 'A fresh tip and an empty pipette are required')
            if not self._aligned(ctx, self.tip_sites[p['tip']]):
                return self._reject('not_aligned', 'Pipette is not at the selected actual tip position')
        if action == 'discardTip':
            if not self.state['tip']['id']:
                return self._reject('tip_unavailable', 'No tip is installed')
            if not ctx.config.wasteSite or not self._aligned(ctx, ctx.physics.bind_site(ctx.config.wasteSite)):
                return self._reject('not_aligned', 'Move to the solid waste position before discarding')
            waste = self._vessel(p['waste'])
            if not waste or not waste['waste'] or waste['volumeUl'] + self.state['tip']['volumeUl'] > waste['capacityUl']:
                return self._reject('waste_full', 'A waste compartment with sufficient capacity is required')
        if action in ('aspirate', 'dispense', 'mix', 'detach', 'observe', 'count'):
            row = self._vessel(p['vessel'])
            if row is None:
                return self._reject('unknown_vessel', 'Unknown content compartment')
            rejection = self._accessible(ctx, row['id'])
            if rejection:
                return rejection
            rejection = guard(ctx, ctx.key, p, [self.configs[row['id']].entityKey])
            if rejection:
                return rejection
            if action in ('aspirate', 'dispense', 'mix') and not self.state['tip']['id']:
                return self._reject('tip_unavailable', 'Install a fresh tip before liquid handling')
            if action in ('aspirate', 'dispense'):
                tip, amount = self.state['tip'], p['volumeUl']
                source, destination = (row, tip) if action == 'aspirate' else (tip, row)
                capacity = ctx.config.tipCapacityUl if action == 'aspirate' else row['capacityUl']
                if source['volumeUl'] + 1e-8 < amount:
                    return self._reject('insufficient_liquid', 'Requested transfer exceeds actual available liquid')
                if destination['volumeUl'] + amount > capacity + 1e-8:
                    return self._reject('capacity_exceeded', 'Requested transfer exceeds destination capacity')
                cultures = {cell['culture'] for cell in row['cells']}
                contact = tip['contactCulture']
                allowed = {row['culture'], row['parent']} | cultures
                if contact and not row['waste'] and contact not in allowed:
                    return self._reject('cross_sample', 'Sample-contact tips cannot enter another culture or common reagent')
                if action == 'dispense' and cultures and contact and contact not in cultures and contact != row['parent']:
                    return self._reject('cross_sample', 'Destination culture is not descended from this sample')
                if not row['mixed'] and action == 'aspirate' and any(c['suspendedLive'] or c['suspendedDead'] for c in row['cells']):
                    return self._reject('not_mixed', 'Resuspend before transferring suspended cells')
            if action == 'detach' and (row['exposureSince'] < 0 or ctx.sim_time - row['exposureSince'] < ctx.config.detachSeconds):
                return self._reject('exposure_incomplete', 'Teaching dissociation exposure is not complete')
        return None

    def _control(self, inv, ctx, *, commit):
        p = inv.params
        old = next((r for r in self.state['controlRequests'] if r['operationId'] == p['operationId']), None)
        if old:
            return None
        if p['epoch'] != ctx.get('controlEpoch') or p['owner'] != ctx.get('controlOwner'):
            return self._reject('stale_control', 'Control request belongs to an older owner or epoch')
        if inv.action == 'confirmControl':
            if ctx.get('controlMode') != 'holding' or ctx.get('controlSafe') is not True:
                return self._reject('not_safe', 'Workcell has not established safe hold')
            if any(ctx.read(key, 'controlSafe') is not True for key in ctx.config.managedDevices):
                return self._reject('not_safe', 'A managed device has not established safe hold')
        if commit:
            if inv.action == 'requestControl':
                ctx.set(controlEpoch=ctx.get('controlEpoch') + 1, controlMode='holding', controlSafe=False)
                self.pending = {'owner': p['nextOwner'], 'mode': p['mode']}
                self._hold(ctx)
            else:
                ctx.set(controlOwner=self.pending['owner'], controlMode=self.pending['mode'])
                self.state['reservations'] = [r | {'owner': self.pending['owner'], 'epoch': ctx.get('controlEpoch')}
                                              for r in self.state['reservations']]
                self.pending = None
            self.state['controlRequests'].append({'operationId': p['operationId'], 'epoch': ctx.get('controlEpoch'),
                                                  'mode': ctx.get('controlMode'), 'owner': ctx.get('controlOwner')})
            self._publish(ctx)
        return None

    def _hold(self, ctx):
        # Capture already executed stroke before replacing the old drive target.
        if self.stroke:
            self._apply_stroke(ctx)
        for joint in [*self.axes, self.pump]:
            ctx.physics.set_joint_target(joint, ctx.physics.joint_q(joint))
            ctx.physics.set_joint_target_velocity(joint, [0])
        if self.handle:
            self.handle.cancelled()
            self._finish(ctx, 'cancelled')
        ctx.set(controlSafe=True, phase='holding')

    def start(self, inv, ctx):
        if inv.action in ('requestControl', 'confirmControl'):
            # accept committed the request even while paused; start is receipt only.
            result = next(r for r in self.state['controlRequests'] if r['operationId'] == inv.params['operationId'])
            inv.succeed(result)
            return
        rejection = self.accept(inv, ctx)
        if rejection:
            inv.fail(rejection.code, rejection.message)
            return
        p, action = inv.params, inv.action
        operation = {'operationId': p['operationId'], 'action': action, 'status': 'running',
                     'appliedUl': 0.0, 'requestedUl': p.get('volumeUl', 0.0), 'vessel': p.get('vessel', ''),
                     'epoch': p['epoch'], 'startedSimTime': ctx.sim_time}
        self.state['operations'].append(operation)
        self.handle, self.started = inv, ctx.sim_time
        ctx.set(controlSafe=False, phase=action, lastOperationId=p['operationId'], lastAppliedUl=0.0,
                remainingUl=p.get('volumeUl', 0.0))
        if action == 'move':
            for joint, value in zip(self.axes, p['position'], strict=True):
                ctx.physics.set_joint_target(joint, [value])
        elif action in ('aspirate', 'dispense'):
            # Return stroke is dry mechanically; transfer starts only after actual zero.
            self.stroke = {'stage': 'reset', 'origin': 0.0, 'appliedUl': 0.0, 'direction': 1}
            ctx.physics.set_joint_target(self.pump, [0.0])
        elif action == 'mix':
            self.stroke = {'stage': 'reset', 'origin': 0.0, 'appliedUl': 0.0, 'direction': 1}
            ctx.physics.set_joint_target(self.pump, [0.0])
        else:
            self._immediate(ctx)
        self._publish(ctx)

    def _immediate(self, ctx):
        inv, tip = self.handle, self.state['tip']
        p, action = inv.params, inv.action
        if action == 'reserve':
            self.state['reservations'] = [r for r in self.state['reservations'] if r['resource'] != p['resource']]
            self.state['reservations'].append({k: p[k] for k in ('resource', 'device', 'owner', 'epoch')})
        elif action == 'release':
            self.state['reservations'] = [r for r in self.state['reservations'] if r['resource'] != p['resource']]
        elif action == 'pickupTip':
            next(r for r in self.state['tips'] if r['id'] == p['tip'])['state'] = 'installed'
            tip['id'] = p['tip']
        elif action == 'discardTip':
            move_content(tip, self._vessel(p['waste']), tip['volumeUl'])
            next(r for r in self.state['tips'] if r['id'] == tip['id'])['state'] = 'discarded'
            self.state['tip'] = empty_content() | {'id': '', 'contactCulture': ''}
        elif action == 'detach':
            for cell in self._vessel(p['vessel'])['cells']:
                for viability in ('Live', 'Dead'):
                    cell['suspended' + viability] += cell['attached' + viability]
                    cell['attached' + viability] = 0.0
        elif action in ('observe', 'count'):
            row = self._vessel(p['vessel'])
            live = sum(c['suspendedLive'] + c['attachedLive'] + c['pelletLive'] for c in row['cells'])
            dead = sum(c['suspendedDead'] + c['attachedDead'] + c['pelletDead'] for c in row['cells'])
            self.state['observations'].append({'operationId': p['operationId'], 'vessel': row['id'],
                'materialRevision': self.state['revision'], 'source': 'simulated-state-model',
                'live': live, 'dead': dead, 'volumeUl': row['volumeUl'], 'simTime': ctx.sim_time})
            ctx.set(observationRevision=len(self.state['observations']))
        elif action in ('wait', 'move'):
            return
        self._finish(ctx, 'succeeded')

    def _apply_stroke(self, ctx):
        if not self.handle or not self.stroke or self.stroke['stage'] != 'transfer':
            return
        inv, stroke = self.handle, self.stroke
        p, action = inv.params, inv.action
        requested = p['volumeUl']
        actual = max(0.0, min(requested, (float(ctx.physics.joint_q(self.pump)[0]) - stroke['origin'])
                              * ctx.config.pumpUlPerUnit))
        amount = max(0.0, actual - stroke['appliedUl'])
        if amount <= 1e-10:
            return
        row, tip = self._vessel(p['vessel']), self.state['tip']
        source, destination = (row, tip) if action == 'aspirate' else (tip, row)
        move_content(source, destination, amount)
        if action == 'aspirate':
            cultures = [c['culture'] for c in row['cells'] if any(c[k] for k in c if k not in ('culture', 'parent'))]
            if cultures:
                tip['contactCulture'] = row['parent'] or cultures[0]
        elif any(c['name'] == 'trypsin' and c['volumeUl'] > 0 for c in row['components']):
            if row['exposureSince'] < 0:
                row['exposureSince'] = ctx.sim_time
        stroke['appliedUl'] = actual
        self.state['operations'][-1]['appliedUl'] = actual
        ctx.set(lastAppliedUl=actual, remainingUl=max(0, requested - actual))
        self._publish(ctx)

    def _finish(self, ctx, status):
        inv = self.handle
        self.state['operations'][-1]['status'] = status
        if status == 'succeeded':
            inv.succeed({'operationId': inv.params['operationId'], 'materialRevision': self.state['revision'] + 1})
        self.handle, self.stroke = None, None
        ctx.set(controlSafe=True, phase='idle')
        self._publish(ctx)

    def tick(self, ctx, dt):
        inv = self.handle
        if inv is None:
            return
        rejection = guard(ctx, ctx.key, inv.params)
        if rejection:
            self._hold(ctx)
            return
        p, action = inv.params, inv.action
        if action in ('aspirate', 'dispense', 'mix'):
            rejection = self._accessible(ctx, p['vessel'])
            if rejection:
                self._hold(ctx)
                return
            q = float(ctx.physics.joint_q(self.pump)[0])
            if self.stroke['stage'] == 'reset':
                if abs(q) > 1e-7:
                    return
                self.stroke['stage'] = 'transfer'
                self.stroke['origin'] = q
                target = p['volumeUl'] / ctx.config.pumpUlPerUnit
                ctx.physics.set_joint_target(self.pump, [target])
            elif action == 'mix':
                target = p['volumeUl'] / ctx.config.pumpUlPerUnit
                if abs(q - target) < 1e-7:
                    for cell in self._vessel(p['vessel'])['cells']:
                        for viability in ('Live', 'Dead'):
                            cell['suspended' + viability] += cell['pellet' + viability]
                            cell['pellet' + viability] = 0.0
                    self._vessel(p['vessel'])['mixed'] = True
                    self._finish(ctx, 'succeeded')
            else:
                self._apply_stroke(ctx)
                inv.progress(self.stroke['appliedUl'] / p['volumeUl'])
                if p['volumeUl'] - self.stroke['appliedUl'] < 1e-6:
                    self._finish(ctx, 'succeeded')
        elif action == 'wait':
            if ctx.sim_time - self.started >= p['seconds']:
                self._finish(ctx, 'succeeded')
        elif action == 'move':
            if all(abs(float(ctx.physics.joint_q(j)[0]) - value) <= ctx.config.axisToleranceM
                   for j, value in zip(self.axes, p['position'], strict=True)):
                self._finish(ctx, 'succeeded')

    def cancel(self, inv, ctx):
        if self.handle and self.handle.id == inv.id:
            self._hold(ctx)
        else:
            inv.cancelled()
