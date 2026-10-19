"""Public upload/index boundaries, independent of Newton and the gateway host filesystem."""

import hashlib
import io
import os
import subprocess
import tarfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.library.assets import AssetLibrary


def package_files():
    return {
        "asset.yaml": b"id: labware/test-tube\nkind: labware\ntitle: Test tube\nstage: asset.usda\n",
        "asset.usda": (b'#usda 1.0\n(defaultPrim = "Asset"; upAxis = "Z"; metersPerUnit = 1)\n'
                       b'def Xform "Asset" {}\n'),
        "LICENSE": b"Fixture created for testing; CC0-1.0\n",
    }


def archive(files, *, reverse=False, extra=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        for name, value in sorted(files.items(), reverse=reverse):
            entry = tarfile.TarInfo(name)
            entry.size = len(value)
            entry.mtime = 999 if reverse else 0
            tar.addfile(entry, io.BytesIO(value))
        if extra is not None:
            tar.addfile(extra)
    return output.getvalue()


def upload(client, files=None, **kwargs):
    return client.post(
        "/api/v1/library/imports",
        content=archive(files or package_files(), **kwargs),
        headers={"Content-Type": "application/x-tar"},
    )


def tree_oracle(files):
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        digest.update(name.encode() + b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def directory_link(link, target):
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        command = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True
        )
        assert command.returncode == 0, command.stderr
        assert link.is_junction()
    else:
        link.symlink_to(target, target_is_directory=True)


def test_import_revision_dedup_restart_full_hash_and_indexed_files(tmp_path):
    with TestClient(create_app()) as client:
        first = upload(client)
        assert first.status_code == 200, first.text
        job = first.json()
        assert job["assetVersion"] == "labware/test-tube@1" and not job["deduplicated"]
        again = upload(client, reverse=True).json()
        assert again["assetVersion"] == job["assetVersion"] and again["deduplicated"]
        detail = client.get("/api/v1/library/assets/labware/test-tube/1").json()
        stored = {name: client.get(url).content for name, url in detail["files"].items()}
        assert tree_oracle(stored) == detail["contentHash"] == job["contentHash"]
        assert detail["validation"]["importContentHash"] == tree_oracle(package_files())
        assert client.get("/api/v1/library/imports/" + job["id"]).json() == job
        assert len(client.get("/api/v1/library/imports").json()["imports"]) == 2
        assert client.get("/api/v1/library/files/user/.import.lock").status_code == 404
        assert (
            client.get("/api/v1/library/files/user/assets/labware/test-tube/1/unindexed").status_code == 404
        )
        assert (
            client.get("/api/v1/library/files/builtin/assets/labware/test-tube/1/LICENSE").status_code == 404
        )
        exported = upload(client, stored).json()
        assert exported["assetVersion"] == job["assetVersion"] and exported["deduplicated"]
        changed = package_files() | {"LICENSE": b"Changed fixture license\n"}
        second = upload(client, changed).json()
        assert second["assetVersion"] == "labware/test-tube@2"
        assert client.get("/api/v1/library/assets/labware/test-tube").json()["id"] == second["assetVersion"]
        assert "visual" in detail["gaps"] and "physics_validation" in detail["gaps"]
        assert [check["id"] for check in detail["validation"]["checks"] if check["status"] == "pass"] == [
            "S1"
        ]
    with TestClient(create_app()) as client:
        assert upload(client).json()["assetVersion"] == "labware/test-tube@1"
        assert client.get("/api/v1/library/imports/" + job["id"]).json() == job
        assert upload(client, package_files() | {"NOTICE": b"Third content"}).json()["assetVersion"] == (
            "labware/test-tube@3"
        )


@pytest.mark.parametrize(
    "metadata",
    [
        b"[]",
        b"id: robot/x\nkind: labware\ntitle: X\nstage: asset.usda\n",
        b"id: labware/test-tube@1\nkind: labware\ntitle: X\nstage: asset.usda\n",
        b"id: labware/test-tube\nkind: labware\ntitle: X\nstage: ../asset.usda\n",
        b"id: labware/test-tube\nkind: labware\ntitle: X\nstage: asset.usda\nphysics: {massKg: -1}\n",
        b"id: labware/test-tube\nkind: labware\ntitle: X\nstage: asset.usda\ndimensionsM: [1, .nan, 2]\n",
    ],
)
def test_schema_errors_have_problems_without_allocating_versions(metadata, tmp_path):
    with TestClient(create_app()) as client:
        rejected = upload(client, package_files() | {"asset.yaml": metadata})
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["detail"]["code"] == "import_invalid"
        assert rejected.json()["detail"]["problems"]
        assert not (tmp_path / "user-library" / "assets").exists()
        assert upload(client).json()["assetVersion"] == "labware/test-tube@1"


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../outside", tarfile.REGTYPE),
        ("/outside", tarfile.REGTYPE),
        ("C:/outside", tarfile.REGTYPE),
        ("a\\outside", tarfile.REGTYPE),
        ("NUL.txt", tarfile.REGTYPE),
        ("COM¹.txt", tarfile.REGTYPE),
        ("CONOUT$", tarfile.REGTYPE),
        ("file.", tarfile.REGTYPE),
        ("file ", tarfile.REGTYPE),
        ("a/../outside", tarfile.REGTYPE),
        ("License", tarfile.REGTYPE),
        ("LICENSE/child", tarfile.REGTYPE),
        ("link", tarfile.SYMTYPE),
        ("hard", tarfile.LNKTYPE),
        ("fifo", tarfile.FIFOTYPE),
    ],
)
def test_unsafe_archive_never_publishes_or_escapes(tmp_path, name, kind):
    entry = tarfile.TarInfo(name)
    entry.type, entry.linkname = kind, "../../outside"
    with TestClient(create_app()) as client:
        rejected = upload(client, extra=entry)
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["detail"]["code"] == "import_invalid"
        assert not (tmp_path / "outside").exists()
        assert not (tmp_path / "user-library" / "assets").exists()
        assert not list((tmp_path / "user-library").glob(".import-*"))


def test_api_never_accepts_gateway_host_paths_or_untrusted_validation(tmp_path):
    with TestClient(create_app()) as client:
        rejected = client.post("/api/v1/library/imports", json={"path": str(tmp_path)})
        assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "import_invalid"
        rejected = upload(client, package_files() | {"validation.json": b'{"launchable":true}'})
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["problems"][0]["path"] == "validation.json"


def test_concurrent_workers_allocate_once_and_preserve_changed_revisions(tmp_path):
    root = tmp_path / "library"
    libraries = [
        AssetLibrary(tmp_path / "state", builtin_root=tmp_path / "builtin", user_root=root) for _ in range(4)
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(
            pool.map(lambda library: library.import_tar(io.BytesIO(archive(package_files()))), libraries)
        )
    assert {job["assetVersion"] for job in jobs} == {"labware/test-tube@1"}
    assert sum(not job["deduplicated"] for job in jobs) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(
            pool.map(
                lambda pair: pair[1].import_tar(
                    io.BytesIO(archive(package_files() | {"extra": str(pair[0]).encode()}))
                ),
                enumerate(libraries),
            )
        )
    assert {job["assetVersion"] for job in jobs} == {f"labware/test-tube@{rev}" for rev in range(2, 6)}
    assert len(list((root / "assets/labware/test-tube").glob("*/asset.yaml"))) == 5


def test_same_builtin_user_full_id_conflicts_but_next_revision_is_allowed(tmp_path):
    source = tmp_path / "builtin/assets/labware/test-tube/1"
    source.mkdir(parents=True)
    for name, data in package_files().items():
        (source / name).write_bytes(data)
    library = AssetLibrary(tmp_path / "state", builtin_root=tmp_path / "builtin", user_root=tmp_path / "user")
    library.startup()
    assert library.import_tar(io.BytesIO(archive(package_files())))["deduplicated"]
    assert library.import_tar(io.BytesIO(archive(package_files() | {"extra": b"1"})))[
        "assetVersion"
    ].endswith("@2")
    target = tmp_path / "user/assets/labware/test-tube/1"
    target.mkdir()
    for name, data in package_files().items():
        (target / name).write_bytes(data)
    with pytest.raises(ValueError, match="Conflicting asset version"):
        library.startup()


def test_mutating_an_indexed_asset_cannot_replace_served_bytes(tmp_path):
    with TestClient(create_app()) as client:
        upload(client)
        detail = client.get("/api/v1/library/assets/labware/test-tube").json()
        (tmp_path / "user-library/assets/labware/test-tube/1/LICENSE").write_text("changed")
        response = client.get(detail["files"]["LICENSE"])
        assert response.status_code == 409 and response.json()["detail"]["code"] == "asset_changed"
        assert upload(client).status_code == 409


@pytest.mark.parametrize("ancestor", ["assets", "assets/labware", "assets/labware/test-tube"])
def test_preexisting_destination_link_is_rejected_before_any_external_write(tmp_path, ancestor):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "user-library"
    link = root / ancestor
    directory_link(link, outside)
    with TestClient(create_app()) as client:
        response = upload(client)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "import_invalid"
        assert list(outside.iterdir()) == []
        assert client.get("/api/v1/library/imports").json()["imports"] == []


def test_populated_destination_junction_added_after_startup_is_rejected_before_refresh(tmp_path):
    outside = tmp_path / "outside"
    package = outside / "labware/test-tube/1"
    package.mkdir(parents=True)
    for name, data in package_files().items():
        (package / name).write_bytes(data)
    expected = {file.relative_to(outside): file.read_bytes() for file in outside.rglob("*") if file.is_file()}
    with TestClient(create_app()) as client:
        directory_link(tmp_path / "user-library/assets", outside)
        response = upload(client)
        assert response.status_code == 422 and response.json()["detail"]["code"] == "import_invalid"
    actual = {file.relative_to(outside): file.read_bytes() for file in outside.rglob("*") if file.is_file()}
    assert actual == expected


def test_upload_limits_and_duplicate_entries_are_schema_errors(tmp_path, monkeypatch):
    import packages.library.assets as assets

    with TestClient(create_app()) as client:
        duplicate = tarfile.TarInfo("LICENSE")
        assert upload(client, extra=duplicate).status_code == 422
        monkeypatch.setattr(assets, "MAX_PACKAGE_BYTES", 1)
        assert upload(client).status_code == 422
        assert not (tmp_path / "user-library/assets").exists()


def test_active_content_is_downloaded_as_bytes_and_only_completed_jobs_are_indexed():
    with TestClient(create_app()) as client:
        upload(client, package_files() | {"preview.html": b"<script>alert(1)</script>"})
        detail = client.get("/api/v1/library/assets/labware/test-tube").json()
        response = client.get(detail["files"]["preview.html"])
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["x-content-type-options"] == "nosniff"
