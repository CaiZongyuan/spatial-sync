"""Deterministic kinematic load fixtures; these scenes do not simulate physics."""

import math

from packages.protocol.schema import SceneManifest

STRESS_SCENES = {
    "stress-100": (100, 0),
    "stress-500": (500, 0),
    "stress-mixed": (500, 2000),
    "stress-2000": (2000, 0),
    "stress-10000": (10000, 0),
}
ROOT = "/World/Stress"
IDENTITY = {"p": [0, 0, 0], "q": [0, 0, 0, 1]}


def stream_id(index):
    # Non-contiguous IDs catch dense-slot/stream-ID confusion in public consumers.
    return 100 + 3 * index


def location(index, total):
    columns = math.ceil(math.sqrt(total))
    rows = math.ceil(total / columns)
    return ((index % columns - (columns - 1) / 2) * 0.24, (index // columns - (rows - 1) / 2) * 0.24)


def geometry(index):
    return (
        {"type": "box", "halfExtents": [0.08, 0.08, 0.08]},
        {"type": "sphere", "radius": 0.08},
        {"type": "capsule", "radius": 0.05, "halfHeight": 0.06},
    )[index % 3]


def poses(sim_time, moving, static):
    total = moving + static
    result = []
    # Seven is coprime to every registered moving count: rows deliberately differ
    # from Manifest order, while every frame still contains the complete state.
    for slot in range(moving):
        index = slot * 7 % moving
        x, y = location(index, total)
        angle = 0.4 * math.sin(sim_time + index * 0.013)
        z = 0.45 + 0.12 * math.sin(sim_time * 1.3 + index * 0.017)
        result.append((stream_id(index), x, y, z, 0, 0, math.sin(angle / 2), math.cos(angle / 2)))
    return result


def manifest(session_id, revision, scene_id, options):
    moving, static = STRESS_SCENES[scene_id]
    nodes = [
        {"kind": "group", "entityKey": "/World", "name": "World"},
        {"kind": "group", "entityKey": ROOT, "name": "Synthetic stress", "parentEntityKey": "/World"},
        {
            "kind": "group",
            "entityKey": ROOT + "/Moving",
            "name": f"{moving} moving bodies",
            "parentEntityKey": ROOT,
        },
        {
            "kind": "group",
            "entityKey": ROOT + "/Static",
            "name": f"{static} static visuals",
            "parentEntityKey": ROOT,
        },
    ]
    for index in range(moving + static):
        visual = {"kind": "primitive", "geometry": geometry(index), "localPose": IDENTITY}
        if index < moving:
            node = {
                "kind": "body",
                "entityKey": f"{ROOT}/Moving/Body{index:05d}",
                "name": f"Body {index:05d}",
                "parentEntityKey": ROOT + "/Moving",
                "streamId": stream_id(index),
                "debug": {"syntheticIndex": index},
            }
        else:
            x, y = location(index, moving + static)
            node = {
                "kind": "static",
                "entityKey": f"{ROOT}/Static/Part{index - moving:05d}",
                "name": f"Static {index - moving:05d}",
                "parentEntityKey": ROOT + "/Static",
                "initialPose": {"p": [x, y, 0.15], "q": [0, 0, 0, 1]},
            }
        nodes.append({**node, "visuals": [visual], "colliders": []})
    return SceneManifest.model_validate(
        {
            "sessionId": session_id,
            "sceneRevision": revision,
            "revisionNumericId": 1,
            "epoch": 0,
            "sceneName": scene_id,
            "source": {
                "kind": "synthetic",
                "solver": "DeterministicKinematics",
                "physicsDt": options.physicsDt,
                "basePublishHz": options.basePublishHz,
                "realtimeFactor": options.realtimeFactor,
            },
            "nodes": nodes,
        }
    )
