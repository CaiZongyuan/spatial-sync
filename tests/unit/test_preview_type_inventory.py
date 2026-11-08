"""A frozen preview must cover the Catalog producer's complete type-file input."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from apps.api.app.sources.synthetic_source import REGISTRY
from packages.info.catalog import load_catalog
from packages.library import previews
from packages.library.scenes import SceneLibrary
from packages.protocol.schema import SceneManifest

ROOT = Path(__file__).resolve().parents[2]


def frozen_preview(tmp_path, monkeypatch, scene, source_kind):
    original = next((ROOT / "assets/scene-previews" / scene / "1").glob(f"*/{source_kind}/capture.json"))
    metadata = json.loads(original.read_bytes())
    inputs = tmp_path / "inputs"
    for dependency in metadata["dependencies"]:
        source_file = ROOT / dependency["path"]
        target = inputs / dependency["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target)
        # This is an owned frozen-document fixture for the current source tree,
        # not a replacement for actual capture receipts in the repository.
        dependency["sha256"] = previews.input_hash(target)
    monkeypatch.setattr(previews, "REPO_ROOT", inputs)
    builtin = tmp_path / "builtin"
    package = builtin / "scenes" / scene / "1"
    package.mkdir(parents=True)
    for path in (ROOT / "library/scenes" / scene / "1").iterdir():
        if path.is_file():
            shutil.copyfile(path, package / path.name)
    library = SceneLibrary(builtin_root=builtin, user_root=tmp_path / "library",
                           state_root=tmp_path / "state")
    version = library.resolve(metadata["sceneVersion"])
    cache = tmp_path / "previews"
    directory = cache / version.name / str(version.rev) / version.content_hash / source_kind
    directory.mkdir(parents=True)
    for name in metadata["files"]:
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original.parent / name, target)
    (directory / "capture.json").write_text(json.dumps(metadata), encoding="utf-8")
    repository = previews.PreviewRepository(cache, bundled_root=tmp_path / "no-fallback")
    return repository, version, inputs, directory, metadata


@pytest.fixture
def captured_catalog(tmp_path, monkeypatch):
    return frozen_preview(tmp_path, monkeypatch, "lab-demo", "synthetic")


def add_unused_type(inputs):
    definition = yaml.safe_load((ROOT / "devices/types/tube.v1.yaml").read_text(encoding="utf-8"))
    definition["type"] = "unused-inventory-probe/v1"
    target = inputs / "devices/types/unused-inventory-probe.v1.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(definition), encoding="utf-8")
    return target


def test_added_unused_catalog_type_invalidates_an_already_cached_preview(captured_catalog):
    repository, version, inputs, directory, metadata = captured_catalog
    manifest = SceneManifest.model_validate_json((directory / metadata["documents"]["manifest"]).read_bytes())
    before = load_catalog(manifest, version.name, REGISTRY, root=inputs / "devices",
                          scene_file=version.path / "devices.yaml")
    assert "unused-inventory-probe/v1" not in before.types
    assert repository.describe(version, "synthetic")["status"] == "available"
    repository.read(version, "synthetic", metadata["documents"]["catalog"])

    add_unused_type(inputs)
    after = load_catalog(manifest, version.name, REGISTRY, root=inputs / "devices",
                         scene_file=version.path / "devices.yaml")
    assert "unused-inventory-probe/v1" in after.types
    assert repository.describe(version, "synthetic")["status"] == "unavailable"
    with pytest.raises(ValueError):
        repository.read(version, "synthetic", metadata["documents"]["catalog"])


def test_added_type_cannot_be_read_from_cached_preview_metadata(captured_catalog):
    repository, version, inputs, _, metadata = captured_catalog
    assert repository.describe(version, "synthetic")["status"] == "available"
    add_unused_type(inputs)
    with pytest.raises(ValueError):
        repository.read(version, "synthetic", metadata["documents"]["catalog"])


def test_complete_updated_catalog_inputs_are_readable(captured_catalog, tmp_path):
    previous, version, inputs, directory, metadata = captured_catalog
    assert previous.describe(version, "synthetic")["status"] == "available"
    added = add_unused_type(inputs)
    manifest = SceneManifest.model_validate_json((directory / metadata["documents"]["manifest"]).read_bytes())
    catalog = load_catalog(manifest, version.name, REGISTRY, root=inputs / "devices",
                           scene_file=version.path / "devices.yaml")

    # A new owned document fixture uses the real pure Catalog compiler and a
    # complete input list. No production capture or historical receipt is edited.
    fresh_root = tmp_path / "fresh-previews"
    fresh = fresh_root / version.name / str(version.rev) / version.content_hash / "synthetic"
    shutil.copytree(directory, fresh, copy_function=shutil.copyfile)
    (fresh / metadata["documents"]["catalog"]).write_text(catalog.model_dump_json(), encoding="utf-8")
    metadata["dependencies"].append({"path": added.relative_to(inputs).as_posix(),
                                      "sha256": previews.input_hash(added)})
    metadata["files"][metadata["documents"]["catalog"]] = previews.file_hash(
        fresh / metadata["documents"]["catalog"])
    (fresh / "capture.json").write_text(json.dumps(metadata), encoding="utf-8")
    current = previews.PreviewRepository(fresh_root, bundled_root=tmp_path / "no-fallback")
    assert previous.describe(version, "synthetic")["status"] == "unavailable"
    assert current.describe(version, "synthetic")["status"] == "available"
    document = json.loads(current.read(version, "synthetic", metadata["documents"]["catalog"]))
    assert "unused-inventory-probe/v1" in document["types"]


def test_partial_type_input_list_is_rejected(captured_catalog):
    repository, version, _, directory, metadata = captured_catalog
    metadata["dependencies"] = [item for item in metadata["dependencies"]
                                if item["path"] != "devices/types/tube.v1.yaml"]
    (directory / "capture.json").write_text(json.dumps(metadata), encoding="utf-8")
    assert repository.describe(version, "synthetic")["status"] == "unavailable"


@pytest.mark.parametrize("change", ["removed", "renamed", "modified"])
def test_changed_type_inputs_invalidate_a_cached_preview(captured_catalog, change):
    repository, version, inputs, _, metadata = captured_catalog
    assert repository.describe(version, "synthetic")["status"] == "available"
    path = inputs / "devices/types/tube.v1.yaml"
    if change == "removed":
        path.unlink()
    elif change == "renamed":
        path.rename(path.with_name("renamed-tube.v1.yaml"))
    else:
        definition = yaml.safe_load(path.read_text(encoding="utf-8"))
        definition["title"] = "Changed actual type input"
        path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    assert repository.describe(version, "synthetic")["status"] == "unavailable"
    with pytest.raises(ValueError):
        repository.read(version, "synthetic", metadata["documents"]["catalog"])


def test_unchanged_type_glob_remains_available(captured_catalog):
    repository, version, inputs, _, metadata = captured_catalog
    assert repository.describe(version, "synthetic")["status"] == "available"
    (inputs / "devices/types/README.txt").write_text("Not a Catalog type input.", encoding="utf-8")
    nested = inputs / "devices/types/nested"
    nested.mkdir()
    (nested / "ignored.yaml").write_text("Not part of the direct glob.", encoding="utf-8")
    assert repository.describe(version, "synthetic")["status"] == "available"
    assert repository.read(version, "synthetic", metadata["documents"]["catalog"])


def test_preview_without_type_inputs_does_not_gain_a_catalog_dependency(tmp_path, monkeypatch):
    repository, version, inputs, _, metadata = frozen_preview(
        tmp_path, monkeypatch, "falling-boxes", "newton")
    assert not any(item["path"].startswith("devices/types/") for item in metadata["dependencies"])
    assert repository.describe(version, "newton")["status"] == "available"
    add_unused_type(inputs)
    assert repository.describe(version, "newton")["status"] == "available"
    assert repository.read(version, "newton", metadata["documents"]["catalog"])
