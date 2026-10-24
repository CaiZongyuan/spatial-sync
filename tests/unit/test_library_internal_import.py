"""Server-staged internal import: confinement, caps and the SF2 large-package path."""

import os
import subprocess

import pytest

from packages.library.archive import MAX_PACKAGE_BYTES, MAX_UPLOAD_BYTES
from packages.library.assets import AssetLibrary
from packages.library.imports import INTERNAL_MAX_BYTES, INTERNAL_MAX_FILES, StagedLimits

ASSET_YAML = b"id: labware/test-tube\nkind: labware\ntitle: Test tube\nstage: asset.usda\n"
STAGE = b'#usda 1.0\n(defaultPrim = "Asset"\n upAxis = "Z"\n metersPerUnit = 1)\ndef Xform "Asset" {}\n'


def package_files():
    return {"asset.yaml": ASSET_YAML, "asset.usda": STAGE, "LICENSE": b"Fixture; CC0-1.0\n"}


def library(tmp_path):
    return AssetLibrary(
        tmp_path / "state", builtin_root=tmp_path / "builtin", user_root=tmp_path / "library"
    )


def staged(tmp_path, files=None, name="package"):
    root = tmp_path / "staging"
    stage = root / "acq-1" / name
    stage.mkdir(parents=True)
    for relative, content in (files if files is not None else package_files()).items():
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return root, stage


def rejected(tmp_path, root, stage, **kwargs):
    with pytest.raises(Exception) as caught:
        library(tmp_path).import_staged(stage, allowed_root=root, **kwargs)
    error = caught.value
    assert getattr(error, "status", None) == 422
    assert getattr(error, "detail", {}).get("code") == "import_invalid"
    assert not list((tmp_path / "library").glob(".import-*"))
    return error


def test_internal_import_accepts_a_server_owned_stage_and_reindexes(tmp_path):
    root, stage = staged(tmp_path)
    assets = library(tmp_path)
    job = assets.import_staged(stage, allowed_root=root)
    assert job["assetVersion"] == "labware/test-tube@1"
    assert job["deduplicated"] is False
    detail = assets.describe("labware/test-tube@1")
    assert set(detail["files"]) == set(package_files()) | {"validation.json"}
    assert detail["contentHash"] == job["contentHash"]
    assert len(detail["validation"]["importContentHash"]) == 64
    assert assets.import_staged(stage, allowed_root=root)["deduplicated"] is True
    assert not list((tmp_path / "library").glob(".import-*"))


def test_internal_import_above_public_upload_cap(tmp_path):
    """129 MiB is past the 128 MiB public upload yet inside the 512 MiB staged budget."""
    files = package_files() | {"source/bulk.bin": b""}
    root, stage = staged(tmp_path, files)
    with (stage / "source/bulk.bin").open("wb") as stream:
        stream.truncate(129 * 1024 * 1024)
    assert MAX_UPLOAD_BYTES < (stage / "source/bulk.bin").stat().st_size
    assets = library(tmp_path)
    job = assets.import_staged(stage, allowed_root=root)
    assert job["assetVersion"] == "labware/test-tube@1"
    detail = assets.describe("labware/test-tube@1")
    assert "source/bulk.bin" in detail["files"]
    assert (tmp_path / "library/assets/labware/test-tube/1/source/bulk.bin").stat().st_size == (
        129 * 1024 * 1024
    )


def test_internal_import_rejects_paths_outside_the_server_staging_root(tmp_path):
    root, stage = staged(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    for name, content in package_files().items():
        (outside / name).write_bytes(content)
    rejected(tmp_path, root, outside)


def test_internal_import_rejects_links_escapes_and_missing_manifests(tmp_path):
    root, stage = staged(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("secret", encoding="utf-8")
    if os.name == "nt":
        command = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(stage / "linked"), str(outside)],
            capture_output=True,
            text=True,
        )
        if command.returncode != 0:
            pytest.skip("Junctions are unavailable in this environment")
        assert (stage / "linked").is_junction()
    else:
        (stage / "linked").symlink_to(outside, target_is_directory=True)
    rejected(tmp_path, root, stage)
    if os.name == "nt":
        os.rmdir(stage / "linked")
    else:
        (stage / "linked").unlink()

    empty = root / "acq-empty" / "package"
    empty.mkdir(parents=True)
    rejected(tmp_path, root, empty)
    rejected(tmp_path, root, root)  # the staging root itself is not a package

    (stage / "scene.yaml").write_bytes(b"name: lab\n")
    rejected(tmp_path, root, stage)
    (stage / "scene.yaml").unlink()

    (stage / "Validation.json").write_bytes(b"{}")
    rejected(tmp_path, root, stage)
    (stage / "Validation.json").unlink()

    (stage / "asset.yaml").rename(stage / "ASSET.YAML")
    rejected(tmp_path, root, stage)


def test_internal_import_enforces_staged_byte_and_file_caps(tmp_path):
    root, stage = staged(tmp_path)
    rejected(tmp_path, root, stage, limits=StagedLimits(max_bytes=8))
    rejected(tmp_path, root, stage, limits=StagedLimits(max_files=2))
    assert INTERNAL_MAX_BYTES == 512 * 1024 * 1024
    assert INTERNAL_MAX_FILES == 4096
    assert MAX_PACKAGE_BYTES == 256 * 1024 * 1024


def test_stage_on_library_filesystem_is_published_without_a_second_copy(tmp_path, monkeypatch):
    assets = library(tmp_path)
    root = tmp_path / "library" / ".acquisition-staging"
    stage = root / "acq-1" / "package"
    stage.mkdir(parents=True)
    for relative, content in package_files().items():
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    imports = __import__("packages.library.imports", fromlist=["copy_stage"])
    monkeypatch.setattr(
        imports, "copy_stage", lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("copy_stage must not run for a library-owned stage")
        )
    )
    job = assets.import_staged(stage, allowed_root=root)
    assert job["assetVersion"] == "labware/test-tube@1"
    assert not stage.exists()
    assert (tmp_path / "library/assets/labware/test-tube/1/asset.yaml").is_file()
