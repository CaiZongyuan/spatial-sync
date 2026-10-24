"""Program material is inspectable without importing or executing its Python."""

import io
import math
import tarfile

import pytest

from packages.library.common import LibraryError
from packages.programs.library import ProgramLibrary, inspect_package
from packages.programs.metadata import ProgramMetadata
from packages.protocol.schema import Actor
from packages.training.inputs import pack_package
from tests.fixtures.script_program import SOURCE, program_directory


def test_inspection_hashes_original_dirty_bytes_and_config_without_importing_the_entry_class(tmp_path):
    package, manifest = program_directory(tmp_path)
    source = package / "controller.py"
    assert not hasattr(math, "_lw_uploaded_program_marker")
    first = inspect_package(package)
    assert first["metadata"] == manifest
    assert ProgramMetadata.model_validate(first["metadata"]).model_dump(mode="json") == manifest
    assert first["files"]["controller.py"]["size"] == len(SOURCE)
    assert source.read_bytes() == SOURCE
    assert not hasattr(math, "_lw_uploaded_program_marker")
    source.write_bytes(SOURCE.replace(b"\n", b"\r\n"))
    changed = inspect_package(package)
    assert first["contentHash"] != changed["contentHash"]
    assert first["codeHash"] != changed["codeHash"]
    assert first["configHash"] == changed["configHash"]
    assert not hasattr(math, "_lw_uploaded_program_marker")


def test_push_retries_keep_original_code_bytes_and_new_material_gets_a_new_version(tmp_path):
    package, _ = program_directory(tmp_path)
    root = tmp_path / "library"
    library = ProgramLibrary({"builtin": tmp_path / "builtin", "user": root})
    actor = Actor(kind="agent", name="owned-author")
    original = pack_package(package)
    first = library.push(io.BytesIO(original), actor)
    assert first["id"] == "program/robot_arm.script.probe@1"
    assert first["requestedBy"] == {"kind": "agent", "name": "owned-author"}
    assert first["executionScope"] == "simulation-runner"
    assert not first["idempotent"]
    assert library.push(io.BytesIO(original), actor)["idempotent"]
    (package / "controller.py").write_bytes(SOURCE + b"\n# Changed workspace version\n")
    second = library.push(io.BytesIO(pack_package(package)), actor)
    assert second["id"] == "program/robot_arm.script.probe@2"
    assert second["contentHash"] != first["contentHash"]
    assert library.show(first["id"])["contentHash"] == first["contentHash"]
    with tarfile.open(fileobj=io.BytesIO(library.export(first["id"]))) as archive:
        assert archive.extractfile("controller.py").read() == SOURCE
    assert len(library.list()) == 2 and not hasattr(math, "_lw_uploaded_program_marker")
    _, stored = library.resolve(first["id"])
    (stored / "controller.py").write_bytes(b"Changed immutable bytes\n")
    with pytest.raises(LibraryError) as changed:
        library.show(first["id"])
    assert changed.value.detail["code"] == "program_unavailable"
