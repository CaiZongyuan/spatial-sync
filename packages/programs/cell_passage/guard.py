"""Shared source-side guard for the new Cell Passage devices only."""

from packages.info.program import Reject


def guard(ctx, control_entity, params, resources=()):
    if (ctx.read(control_entity, 'controlMode') not in ('auto', 'manual')
            or ctx.read(control_entity, 'controlOwner') != params.get('owner')
            or ctx.read(control_entity, 'controlEpoch') != params.get('epoch')):
        return Reject(code='stale_control', message='Control owner or epoch has changed')
    snapshot = ctx.read(control_entity, 'materialSnapshot')
    reservations = snapshot['reservations'] if snapshot else []
    for resource in resources:
        if not any(row['resource'] == resource and row['device'] == ctx.key
                   and row['owner'] == params.get('owner') and row['epoch'] == params.get('epoch')
                   for row in reservations):
            return Reject(code='reservation_required', message=f'Reserve {resource} for {ctx.key}')
    return None
