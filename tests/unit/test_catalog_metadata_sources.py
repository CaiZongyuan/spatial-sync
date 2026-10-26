"""Pinned upstream HTTP journeys for three native metadata structures."""

import threading

import httpx
import pytest

from packages.catalog_metadata import HFHubReader, MetadataError, MetadataStore
from packages.catalog_metadata.models import DEFAULT_PROFILES

REVISION = "a" * 40
OTHER_REVISION = "b" * 40


def profile(recipe):
    return next(value for value in DEFAULT_PROFILES if value["recipe"] == recipe)


def upstream(routes, requests, *, offline=False):
    def respond(request):
        requests.append(str(request.url))
        if offline:
            raise httpx.ConnectError("offline", request=request)
        key = request.url.path + ("?" + request.url.query.decode() if request.url.query else "")
        value = routes.get(key, routes.get(request.url.path))
        if value is None:
            return httpx.Response(404)
        if callable(value):
            return value(request)
        if isinstance(value, httpx.Response):
            return value
        if isinstance(value, bytes):
            return httpx.Response(200, content=value)
        return httpx.Response(200, json=value)

    return httpx.Client(transport=httpx.MockTransport(respond))


def file(path, size=10, **values):
    return {"type": "file", "path": path, "size": size, "oid": "d" * 40, **values}


def test_simready_full_identity_pages_object_preview_and_incremental_inputs(tmp_path):
    tree = f"/api/datasets/nvidia/simready-assets/tree/{REVISION}"
    resolve = f"/datasets/nvidia/simready-assets/resolve/{REVISION}"
    requests = []
    routes = {
        tree: httpx.Response(
            200,
            json=[{"type": "directory", "path": "beaker", "oid": "c" * 40}],
            headers={"Link": f'<https://huggingface.co{tree}?cursor=2>; rel="next"'},
        ),
        tree + "?cursor=2": [{"type": "directory", "path": "bucket", "oid": "e" * 40}],
    }
    for name in ("beaker", "bucket"):
        routes[tree + "/" + name] = [
            file(f"{name}/com.nvidia.simready.packaging.json"),
            file(f"{name}/.thumbs/preview.png", 100),
            file(f"{name}/.thumbs/normal.png", 1),
            file(f"{name}/Textures/source_assets/.thumbs/preview.png", 1),
            file(f"{name}/simready_usd/model.usd", 1000),
        ]
        routes[resolve + f"/{name}/com.nvidia.simready.packaging.json"] = {
            "name": name,
            "description": "Publisher description",
        }
    reader = HFHubReader(client=upstream(routes, requests), cache_root=tmp_path / "blobs")
    result = reader.enumerate(profile("simready"), REVISION)
    assert result["complete"]
    assert result["counts"]["entries"] == 2
    assert {value["upstreamId"] for value in result["entries"]} == {"beaker", "bucket"}
    assert all(value["preview"]["path"].endswith("preview.png") for value in result["entries"])
    assert result["usage"]["previewBytes"] == 0
    assert all(not url.endswith((".png", ".usd")) for url in requests)
    store = MetadataStore(tmp_path / "store")
    inventory = store.stage_inventory(
        "simready", REVISION, result["entries"], complete=True, counts=result["counts"]
    )
    before = store.inventory_entries(inventory["id"])
    # A different dataset commit with unchanged package tree hashes reuses the
    # package inventory and metadata, while keeping a new exact source identity.
    next_routes = {key.replace(REVISION, OTHER_REVISION): value for key, value in routes.items()}
    next_tree = tree.replace(REVISION, OTHER_REVISION)
    next_routes[next_tree] = [
        {"type": "directory", "path": "beaker", "oid": "c" * 40},
        {"type": "directory", "path": "bucket", "oid": "e" * 40},
    ]
    next_requests = []
    next_reader = HFHubReader(client=upstream(next_routes, next_requests), cache_root=tmp_path / "blobs")
    replacement = next_reader.enumerate(profile("simready"), OTHER_REVISION)
    assert len(next_requests) == 1
    assert [value["inputDigest"] for value in replacement["entries"]] == [
        value["inputDigest"] for value in before
    ]
    assert replacement["usage"]["cacheBytes"] > 0


def test_scenario_table_rows_and_output_variants_have_separate_denominators(tmp_path):
    tree = f"/api/datasets/scenario-labs/3d-collection/tree/{REVISION}/data"
    resolve = f"/datasets/scenario-labs/3d-collection/resolve/{REVISION}"
    routes = {
        tree: [file("data/train.json")],
        resolve + "/README.md": b"---\nlicense: cc-by-4.0\n---",
        resolve + "/data/train.json": {
            "rows": [
                {
                    "id": "example-a",
                    "prompt": "Laboratory storage box",
                    "outputs": [
                        {"id": "glb-a", "format": "glb", "path": "assets/a.glb", "sha256": "1" * 64},
                        {"id": "blend-a", "format": "blend", "path": "assets/a.blend", "sha256": "2" * 64},
                    ],
                    "preview_file": {"path": "assets/a_preview.png", "sha256": "3" * 64},
                },
                {
                    "id": "example-b",
                    "prompt": "A desk",
                    "outputs": [{"id": "glb-b", "format": "glb", "path": "assets/b.glb"}],
                },
            ]
        },
    }
    requests = []
    reader = HFHubReader(client=upstream(routes, requests), cache_root=tmp_path / "blobs")
    result = reader.enumerate(profile("scenario"), REVISION)
    assert result["counts"]["tableRows"] == result["counts"]["entries"] == 2
    assert result["counts"]["outputFiles"] == 3
    first = result["entries"][0]
    assert len(first["facts"]["variants"]) == 2
    assert first["facts"]["dimensionsM"] is None
    assert first["facts"]["license"]["name"] == "CC-BY-4.0"
    assert first["preview"]["role"] == "object"
    assert first["summary"]["en"] == "Laboratory storage box"
    assert all(not url.endswith((".png", ".glb", ".blend")) for url in requests)


def test_chemlab_release_union_includes_non_instrument_subsets_and_publisher_facts(tmp_path):
    tree = f"/api/datasets/MBZUAI/ChemLab-Assets/tree/{REVISION}"
    resolve = f"/datasets/MBZUAI/ChemLab-Assets/resolve/{REVISION}"
    routes = {
        tree: [
            file("assets/balance/model.glb", 200),
            file("assets/balance/preview.png", 100),
            file("assets/balance/normal.png", 1),
            file("assets/desk/model.glb", 500),
            file("assets/beaker/model.glb", 50),
        ],
        resolve + "/release_manifest.json": {
            "assets": {
                "instrument.balance": {"shipped": 3},
                "furniture.desk": {"shipped": 1},
                "labware.beaker": {"shipped": 1},
            }
        },
        resolve + "/catalog/catalog.csv": (
            b"id,name,name_zh,path,size_x_m,size_y_m,size_z_m,n_components,n_joints,glb,glb_up\n"
            b"instrument.balance,Balance,\xe5\xa4\xa9\xe5\xb9\xb3,assets/balance,0.2,0.3,0.4,4,2,assets/balance/model.glb,z\n"
        ),
        resolve + "/LICENSE-ASSETS.md": b"Assets and previews: CC BY-NC 4.0. Code: MIT.",
    }
    requests = []
    reader = HFHubReader(client=upstream(routes, requests), cache_root=tmp_path / "blobs")
    result = reader.enumerate(profile("chemlab"), REVISION)
    assert result["counts"]["entries"] == 3
    assert result["counts"]["catalogRows"] == 1
    assert result["counts"]["subsets"] == {"instrument": 1, "furniture": 1, "labware": 1}
    balance = next(value for value in result["entries"] if value["upstreamId"] == "instrument.balance")
    assert balance["name"]["zh"] == "天平"
    assert balance["facts"]["dimensionsM"] == [0.2, 0.3, 0.4]
    assert balance["facts"]["parts"] == 4
    assert balance["preview"]["path"] == "assets/balance/preview.png"
    assert balance["facts"]["license"]["name"] == "CC-BY-NC-4.0"
    desk = next(value for value in result["entries"] if value["upstreamId"] == "furniture.desk")
    assert desk["facts"]["dimensionsM"] is None
    assert desk["facts"]["declaredShippedFiles"] == 1
    assert all(not url.endswith((".png", ".glb")) for url in requests)


def test_wrong_revision_pagination_cannot_publish_partial_inventory(tmp_path):
    tree = f"/api/datasets/nvidia/simready-assets/tree/{REVISION}"
    response = httpx.Response(
        200,
        json=[{"type": "directory", "path": "a", "oid": "d" * 40}],
        headers={
            "Link": f'<https://huggingface.co{tree.replace(REVISION, OTHER_REVISION)}?cursor=2>; rel="next"'
        },
    )
    reader = HFHubReader(client=upstream({tree: response}, []), cache_root=tmp_path)
    with pytest.raises(MetadataError, match="fixed dataset/revision"):
        reader.enumerate(profile("simready"), REVISION)


def test_chemlab_native_indexes_import_bilingual_names_and_explicit_object_thumbnails(tmp_path):
    tree = f"/api/datasets/MBZUAI/ChemLab-Assets/tree/{REVISION}"
    resolve = f"/datasets/MBZUAI/ChemLab-Assets/resolve/{REVISION}"
    routes = {
        tree: [
            file("assets/balance/model.glb"),
            file("media/thumb/balance.webp", 100),
            file("assets/labware/containers/beaker/model.glb"),
        ],
        resolve + "/release_manifest.json": {
            "assets": {"instrument.balance": {"shipped": 1}, "labware.beaker": {"shipped": 1}}
        },
        resolve + "/catalog/catalog.json": {
            "assets": [
                {
                    "id": "instrument.balance",
                    "name": "Balance",
                    "name_zh": "天平",
                    "path": "assets/balance",
                    "size_m": [0.2, 0.3, 0.4],
                    "files": {"glb": "assets/balance/model.glb"},
                    "thumb": "media/thumb/balance.webp",
                }
            ]
        },
        resolve + "/assets/labware/asset_index.json": {
            "asset_count": 1,
            "assets": [
                {
                    "asset_type_id": "labware.beaker",
                    "name_zh": "烧杯",
                    "component_count": 1,
                    "contract_path": "containers/beaker/asset.contract.json",
                }
            ],
        },
        resolve + "/assets/labware/catalog.json": {
            "types": {
                "labware.beaker": {
                    "name_en": "Glass beaker",
                    "size_m": [0.1, 0.1, 0.15],
                    "dir": "assets/labware/containers/beaker",
                    "model_glb": "assets/labware/containers/beaker/model.glb",
                }
            },
            "conventions": {"up_axis": "+Z"},
        },
    }
    reader = HFHubReader(client=upstream(routes, []), cache_root=tmp_path)
    result = reader.enumerate(profile("chemlab"), REVISION)
    assert result["counts"]["metadataRecords"] == 2
    assert result["counts"]["metadataMissing"] == 0
    assert result["counts"]["catalogRows"] == result["counts"]["labwareRows"] == 1
    balance = next(value for value in result["entries"] if value["upstreamId"] == "instrument.balance")
    beaker = next(value for value in result["entries"] if value["upstreamId"] == "labware.beaker")
    assert balance["preview"]["path"] == "media/thumb/balance.webp"
    assert beaker["name"] == {"en": "Glass beaker", "zh": "烧杯"}
    assert beaker["facts"]["dimensionsM"] == [0.1, 0.1, 0.15]
    assert beaker["facts"]["coordinateSystem"] == {"up": "z"}
    assert beaker["fieldProvenance"]["facts"]["kind"] == "publisher"


def test_controlled_demand_preview_cache_and_primary_file_rejection(tmp_path):
    resolve = f"/datasets/nvidia/simready-assets/resolve/{REVISION}/beaker/preview.png"
    image = b"\x89PNG\r\n\x1a\n" + b"fixture"
    requests = []
    reader = HFHubReader(
        client=upstream({resolve: image}, requests),
        cache_root=tmp_path,
        budgets={"previewBytes": 2 * 1024 * 1024},
    )
    entry = {"revision": REVISION, "preview": {"role": "object", "path": "beaker/preview.png"}}
    assert reader.read_preview(profile("simready"), entry) == (image, "image/png")
    assert len(requests) == 1
    offline_requests = []
    offline = HFHubReader(
        client=upstream({}, offline_requests, offline=True),
        cache_root=tmp_path,
        budgets={"previewBytes": 2 * 1024 * 1024, "networkBytes": 0},
    )
    assert offline.read_preview(profile("simready"), entry)[0] == image
    assert offline_requests == []
    with pytest.raises(MetadataError, match="primary model"):
        reader.read_file(profile("simready"), REVISION, "beaker/model.glb")
    assert len(requests) == 1


def test_source_budget_access_and_cancellation_report_actual_usage(tmp_path):
    tree = f"/api/datasets/nvidia/simready-assets/tree/{REVISION}"
    progress, requests = [], []
    reader = HFHubReader(
        client=upstream({tree: [{"type": "directory", "path": "large"}]}, requests),
        cache_root=tmp_path,
        budgets={"networkBytes": 2},
        progress=progress.append,
    )
    with pytest.raises(MetadataError) as failure:
        reader.enumerate(profile("simready"), REVISION)
    assert failure.value.code == "budget_exhausted"
    assert failure.value.details["usage"]["networkBytes"] > 2
    assert progress[-1]["requests"] == 1
    assert progress[-1]["networkBytes"] > 2
    restricted = HFHubReader(client=upstream({tree: httpx.Response(403)}, []))
    with pytest.raises(MetadataError) as denied:
        restricted.enumerate(profile("simready"), REVISION)
    assert denied.value.code == "source_access_restricted"
    cancelled = threading.Event()
    cancelled.set()
    cancelled_requests = []
    reader = HFHubReader(client=upstream({}, cancelled_requests), cancel=cancelled)
    with pytest.raises(MetadataError) as cancelled_error:
        reader.enumerate(profile("simready"), REVISION)
    assert cancelled_error.value.code == "maintenance_cancelled"
    assert cancelled_requests == []


def test_retry_after_and_discovery_do_not_enable_unselected_datasets(tmp_path):
    calls = []

    def discover(request):
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json=[{"id": "publisher/new-assets", "gated": True}])

    client = upstream({"/api/datasets": discover}, calls)
    reader = HFHubReader(client=client, cache_root=tmp_path)
    values = reader.discover(20)
    assert values[0]["dataset"] == "publisher/new-assets"
    assert values[0]["enabled"] is False
    assert values[0]["access"] == "restricted"
    assert reader.usage["requests"] == 2


def test_complete_simready_identity_manifest_is_checkpointed_before_deep_budget_exhaustion(tmp_path):
    tree = f"/api/datasets/nvidia/simready-assets/tree/{REVISION}"
    requests, progress = [], []
    routes = {
        tree: [
            {"type": "directory", "path": "beaker", "oid": "c" * 40},
            {"type": "directory", "path": "bucket", "oid": "d" * 40},
        ]
    }
    reader = HFHubReader(
        client=upstream(routes, requests),
        cache_root=tmp_path,
        budgets={"requests": 1},
        progress=progress.append,
    )
    with pytest.raises(MetadataError) as stopped:
        reader.enumerate(profile("simready"), REVISION)
    assert stopped.value.code == "budget_exhausted"
    manifest = next(event["inventory"] for event in progress if "inventory" in event)
    assert manifest["complete"]
    assert manifest["counts"]["entries"] == manifest["counts"]["expectedEntries"] == 2
    assert manifest["counts"]["extracted"] == 0
    assert {value["upstreamId"] for value in manifest["entries"]} == {"beaker", "bucket"}
    assert {value["contentDigest"] for value in manifest["entries"]} == {"c" * 40, "d" * 40}
    assert len(requests) == 1


def test_checkpoint_rejection_is_not_retried_as_an_upstream_failure(tmp_path):
    tree = f"/api/datasets/nvidia/simready-assets/tree/{REVISION}"
    requests = []

    def reject_checkpoint(event):
        if "inventory" in event:
            raise httpx.ConnectError("Host checkpoint unavailable")

    reader = HFHubReader(
        client=upstream({tree: [{"type": "directory", "path": "beaker"}]}, requests),
        cache_root=tmp_path,
        progress=reject_checkpoint,
    )
    with pytest.raises(MetadataError) as stopped:
        reader.enumerate(profile("simready"), REVISION)
    assert stopped.value.code == "checkpoint_failed"
    assert len(requests) == 1


def test_chemlab_release_identity_union_is_checkpointed_before_detail_budget(tmp_path):
    resolve = f"/datasets/MBZUAI/ChemLab-Assets/resolve/{REVISION}/release_manifest.json"
    progress, requests = [], []
    reader = HFHubReader(
        client=upstream({resolve: {"assets": {"instrument.balance": {}, "labware.beaker": {}}}}, requests),
        cache_root=tmp_path,
        budgets={"requests": 1},
        progress=progress.append,
    )
    with pytest.raises(MetadataError) as stopped:
        reader.enumerate(profile("chemlab"), REVISION)
    assert stopped.value.code == "budget_exhausted"
    manifest = next(event["inventory"] for event in progress if "inventory" in event)
    assert manifest["complete"]
    assert {entry["upstreamId"] for entry in manifest["entries"]} == {"instrument.balance", "labware.beaker"}
    assert len(requests) == 1


def test_preview_eviction_preserves_metadata_and_restore_keeps_cached_images_offline(tmp_path):
    store = MetadataStore(tmp_path / "store")
    image_a = b"\x89PNG\r\n\x1a\nAAA"
    image_b = b"\x89PNG\r\n\x1a\nBBB"
    reader = HFHubReader(
        cache_root=store.blob_root, client=upstream({}, []), budgets={"imageCacheBytes": len(image_a)}
    )
    proof_key = reader.cache(b"retained publisher evidence")
    first_key = reader.cache(image_a, kind="preview")
    second_key = reader.cache(image_b, kind="preview")
    images = list((store.blob_root / "previews").iterdir())
    assert len(images) == 1
    assert sum(path.stat().st_size for path in images) <= len(image_a)
    assert store.blob(proof_key) == b"retained publisher evidence"
    cached_key = "sha256:" + images[0].name
    assert cached_key in (first_key, second_key)
    restored = MetadataStore(tmp_path / "restored")
    restored.restore(store.export())
    offline_requests = []
    offline = HFHubReader(cache_root=restored.blob_root, client=upstream({}, offline_requests, offline=True))
    entry = {
        "revision": REVISION,
        "preview": {"role": "object", "path": "missing/upstream.png", "cachedSha256": cached_key},
    }
    assert offline.read_preview(profile("simready"), entry)[0] in (image_a, image_b)
    assert offline_requests == []
