"""A real timed-out worker is reaped through its current owned process handle."""

from types import SimpleNamespace

import pytest

from packages.library.common import LibraryError
from packages.library.validation import StructuralValidator


def test_structural_timeout_reaps_owned_cpu_child_and_removes_temporary_inputs(tmp_path, monkeypatch):
    import packages.library.validation as validation

    package = tmp_path / "package"
    package.mkdir()
    (package / "asset.usda").write_text('#usda 1.0\ndef Xform "Asset" {}\n', encoding="utf-8")
    processes = []
    real_popen = validation.subprocess.Popen

    def owned_process(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(validation.subprocess, "Popen", owned_process)
    validator = StructuralValidator(tmp_path / "state", timeout=0.000001)
    with pytest.raises(LibraryError) as failure:
        validator.run(
            package,
            "asset",
            "asset:labware/timeout-fixture@1",
            {"stage": "asset.usda"},
            SimpleNamespace(versions={}),
        )
    assert failure.value.detail["code"] == "validation_unavailable"
    assert len(processes) == 1 and processes[0].poll() is not None
    assert not list((tmp_path / "state").glob(".structure-*"))
