import pytest

from apps.api.app.subscription_scope import ScopeResolver
from packages.protocol.schema import SceneManifest


@pytest.fixture
def resolver():
    identity = {"p": [0, 0, 0], "q": [0, 0, 0, 1]}
    box = {
        "kind": "primitive",
        "geometry": {"type": "box", "halfExtents": [0.2, 0.2, 0.2]},
        "localPose": identity,
    }
    manifest = SceneManifest.model_validate(
        {
            "sessionId": "s",
            "sceneRevision": "r",
            "revisionNumericId": 1,
            "sceneName": "scope",
            "source": {"kind": "synthetic", "physicsDt": 1 / 240},
            "nodes": [
                {"kind": "group", "entityKey": "/World", "name": "World"},
                {
                    "kind": "group",
                    "entityKey": "/World/Device",
                    "name": "Device",
                    "parentEntityKey": "/World",
                },
                *[
                    {
                        "kind": "body",
                        "entityKey": f"/World/Device/{name}",
                        "name": name,
                        "streamId": stream_id,
                        "parentEntityKey": "/World/Device",
                        "visuals": [box],
                    }
                    for name, stream_id in [("Parent", 1), ("Child", 2)]
                ],
                {
                    "kind": "body",
                    "entityKey": "/World/UnknownMesh",
                    "name": "UnknownMesh",
                    "streamId": 3,
                    "visuals": [
                        {
                            "kind": "mesh",
                            "meshId": "sha256-unknown",
                            "url": "/meshes/unknown",
                            "assetUpAxis": "Z",
                            "scale": [1, 1, 1],
                            "localPose": identity,
                        }
                    ],
                },
            ],
            "sites": [
                {
                    "entityKey": "/World/Joint/anchor",
                    "role": "joint-anchor",
                    "parentBodyKey": "/World/Device/Child",
                    "jointKey": "/World/Joint",
                    "localPose": identity,
                }
            ],
            "joints": [
                {
                    "entityKey": "/World/Joint",
                    "type": "fixed",
                    "parentBodyKey": "/World/Device/Parent",
                    "childBodyKey": "/World/Device/Child",
                    "parentLocalPose": identity,
                    "anchorSiteKey": "/World/Joint/anchor",
                    "dofCount": 0,
                }
            ],
        }
    )
    return ScopeResolver(manifest)


def test_device_descendants_site_parent_and_joint_both_sides(resolver):
    assert resolver.resolve("device", device_key="/World/Device").stream_ids == {1, 2}
    assert resolver.resolve("entities", entity_keys=["/World/Joint/anchor"]).stream_ids == {2}
    assert resolver.resolve("entities", entity_keys=["/World/Joint"]).stream_ids == {1, 2}
    assert resolver.resolve("entities", entity_keys=["/World/Device"]).stream_ids == {1, 2}
    with pytest.raises(ValueError):
        resolver.resolve("device", device_key="/World/Device/Child")


def test_viewport_keeps_overlapping_shapes_and_unknown_meshes_even_with_origins_outside(resolver):
    planes = [(1, 0, 0, 1), (-1, 0, 0, 1), (0, 1, 0, 1), (0, -1, 0, 1), (0, 0, 1, 1), (0, 0, -1, 1)]
    scope = resolver.resolve("viewport", viewport_planes=planes)
    poses = [(1, 1.1, 0, 0, 0, 0, 0, 1), (2, 2, 0, 0, 0, 0, 0, 1), (3, 100, 0, 0, 0, 0, 0, 1)]
    assert [pose[0] for pose in scope.select(poses)] == [1, 3]
    assert resolver.resolve("all").select(poses) is poses
    with pytest.raises(ValueError):
        resolver.resolve("viewport", viewport_planes=[(0, 0, 0, 1)] * 6)
