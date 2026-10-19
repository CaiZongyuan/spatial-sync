"""Spatial content for virtual devices selected by a versioned lab-demo package."""

LAYOUT = {
    "incubator/v1": ("Incubator", [-1.45, 0, 0.7], [0.3, 0.3, 0.7]),
    "biosafety-cabinet/v1": ("Biosafety cabinet", [-1.3, -1, 0.9], [0.6, 0.3, 0.9]),
    "env-sensor/v1": ("Environment sensor", [1.4, 0.6, 0.85], [0.08, 0.04, 0.1]),
}


def device_nodes(bindings):
    nodes = []
    for binding in bindings:
        if binding["type"] not in LAYOUT:
            continue
        name, position, extents = LAYOUT[binding["type"]]
        nodes.append(
            {
                "kind": "static",
                "entityKey": binding["key"],
                "name": name,
                "parentEntityKey": "/World/Lab",
                "initialPose": {"p": position, "q": [0, 0, 0, 1]},
                "visuals": [
                    {
                        "kind": "primitive",
                        "geometry": {"type": "box", "halfExtents": extents},
                        "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]},
                    }
                ],
            }
        )
    return nodes
