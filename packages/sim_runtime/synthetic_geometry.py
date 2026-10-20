"""World Sites shared by Synthetic display and nonzero-height program checks."""

from packages.protocol.math import compose_pose


def world_site_pose(site, body_poses):
    local = site.localPose.model_dump()
    if site.parentBodyKey is None:
        return local
    try:
        parent = body_poses[site.parentBodyKey]
    except KeyError as error:
        raise ValueError("Actual Synthetic Site parent pose is unavailable: " + site.parentBodyKey) from error
    return compose_pose(parent, local)


def world_site_poses(sites, body_poses):
    result = {}
    for site in sites:
        if site.entityKey in result:
            raise ValueError("Actual Synthetic Site identity is ambiguous: " + site.entityKey)
        result[site.entityKey] = world_site_pose(site, body_poses)
    return result
