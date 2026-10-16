import pytest

from packages.assets.bindings import apply_bindings, load_bindings
from packages.assets.registry import AssetRegistry, asset_id_from_url


def test_registry_only_serves_registered_paths_and_rechecks_disappearing_files(tmp_path):
    path = tmp_path / "registered.glb"
    path.write_bytes(b"glTF fixture")
    (tmp_path / "private.glb").write_bytes(b"not registered")
    registry = AssetRegistry({"public.glb": "registered.glb"}, root=tmp_path)
    assert registry.available("/api/v1/assets/public.glb")
    value = registry.read("public.glb")
    assert value.data == b"glTF fixture" and value.media_type == "model/gltf-binary"
    assert value.etag.startswith('"sha256-')
    for request in ["private.glb", "../registered.glb", str(path), "%2e%2e/registered.glb"]:
        with pytest.raises(KeyError):
            registry.read(request)
    path.unlink()
    assert not registry.available("/api/v1/assets/public.glb")
    with pytest.raises(KeyError):
        registry.read("public.glb")


@pytest.mark.parametrize("path", ["../outside.glb", "C:/Windows/private.glb", "private.py"])
def test_registry_rejects_paths_outside_asset_root_or_non_assets(tmp_path, path):
    with pytest.raises(ValueError):
        AssetRegistry({"public.glb": path}, root=tmp_path)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/file.glb",
        "file:///private.glb",
        "/api/v1/assets/../file.glb",
        "/api/v1/assets/%2e%2e/file.glb",
        "/api/v1/assets/file.glb?secret=1",
        "/api/v1/assets/a//b.glb",
    ],
)
def test_binding_urls_stay_inside_public_asset_id_namespace(url):
    with pytest.raises(ValueError):
        asset_id_from_url(url)


def test_binding_requires_axis_scale_pose_and_preserves_complete_native_group(tmp_path):
    path = tmp_path / "bindings.yaml"
    path.write_text(
        "/World/Body:\n  url: /api/v1/assets/missing.glb\n  assetUpAxis: Y\n"
        "  scale: [2, 3, 4]\n  localPose: {p: [0.1, 0.2, 0.3], q: [0, 0, 0, 1]}\n",
        encoding="utf-8",
    )
    native = [{"kind": "mesh", "meshId": "sha256-native"}, {"kind": "primitive", "geometry": {"type": "box"}}]
    colliders = [{"kind": "primitive", "geometry": {"type": "sphere"}}]
    nodes = [
        {"entityKey": "/World/Body", "kind": "body", "visuals": native.copy(), "colliders": colliders.copy()}
    ]
    warnings = []
    apply_bindings(nodes, "fixture", warnings, path=path, registry=AssetRegistry(root=tmp_path))
    assert len(nodes[0]["visuals"]) == 1 and nodes[0]["visuals"][0]["kind"] == "glb"
    assert nodes[0]["visuals"][0]["scale"] == [2, 3, 4]
    assert nodes[0]["visuals"][0]["localPose"]["p"] == [0.1, 0.2, 0.3]
    assert nodes[0]["fallbackVisuals"] == native and nodes[0]["colliders"] == colliders
    assert {warning["code"] for warning in warnings} == {"asset_missing", "asset_fallback"}
    path.write_text("/World/Body: {url: /api/v1/assets/cover.glb}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_bindings(path)


def test_body_without_visuals_reports_collider_or_placeholder_fallback(tmp_path):
    nodes = [{"entityKey": "/World/Invisible", "kind": "body", "visuals": [], "colliders": []}]
    warnings = []
    apply_bindings(
        nodes, "unbound", warnings, path=tmp_path / "absent.yaml", registry=AssetRegistry(root=tmp_path)
    )
    assert [warning["code"] for warning in warnings] == ["asset_fallback"]
    assert warnings[0]["entityKey"] == "/World/Invisible"
