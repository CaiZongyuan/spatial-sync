"""World Sites shared by Synthetic display and nonzero-height program checks."""

from packages.protocol.math import compose_pose


def tube_height(manifest, key):
    """The toy source's actual bottom-origin, upright cylinder geometry."""
    nodes = [node for node in manifest.nodes if node.entityKey == key and node.kind == "body"]
    if len(nodes) != 1 or len(nodes[0].visuals) != 1:
        raise ValueError("Actual Synthetic tube geometry is unknown: " + key)
    visual = nodes[0].visuals[0]
    if (visual.kind != "primitive" or visual.geometry.type != "cylinder"
            or list(visual.localPose.q) not in ([0, 0, 0, 1], [0, 0, 0, -1])
            or visual.localPose.p[2] != visual.geometry.halfHeight):
        raise ValueError("Actual Synthetic tube must be a bottom-origin +Z cylinder: " + key)
    return 2 * visual.geometry.halfHeight


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
