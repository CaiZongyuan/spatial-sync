"""The real teaching writer preserves prior output across filesystem failures."""

import io
from pathlib import Path

import pytest
import yaml

from tools import teach as teaching


@pytest.mark.parametrize("failure", ["write", "replace"])
def test_failed_output_preserves_old_bytes_and_cleans_temporary_files(
    tmp_path, monkeypatch, failure, record_property,
):
    target = tmp_path / "locations.yaml"
    previous = b"robot: previous\nlocations: {}\n"
    target.write_bytes(previous)
    real_open = io.open
    injected = []

    class InterruptedWrite:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, text):
            self.stream.write(text[:len(text) // 2])
            self.stream.flush()
            siblings = sorted(str(path) for path in tmp_path.iterdir())
            injected.append(("write", str(self.stream.name), siblings))
            raise OSError("injected incomplete write")

    def failing_open(file, mode="r", *args, **kwargs):
        stream = real_open(file, mode, *args, **kwargs)
        if "w" in mode and Path(file).resolve().is_relative_to(tmp_path):
            return InterruptedWrite(stream)
        return stream

    def failing_replace(source, destination):
        injected.append(("replace", str(source), str(destination)))
        assert Path(source).parent == target.parent
        assert Path(destination) == target
        assert yaml.safe_load(Path(source).read_text()) == {"locations": {"rack.A1": {"grasp": [1, 2]}}}
        raise OSError("injected failure before replacement")

    if failure == "write":
        monkeypatch.setattr(io, "open", failing_open)
    else:
        monkeypatch.setattr(teaching.os, "replace", failing_replace)
    with pytest.raises(OSError, match="injected"):
        teaching.write_locations(target, {"locations": {"rack.A1": {"grasp": [1, 2]}}})
    record_property("injected_failure", repr(injected))
    assert len(injected) == 1
    assert target.read_bytes() == previous
    if failure == "write":
        staged = [Path(path) for path in injected[0][2] if Path(path) != target]
        assert len(staged) == 1 and staged[0].parent == target.parent
    assert sorted(path.name for path in tmp_path.iterdir()) == [target.name]


def test_success_replaces_complete_yaml_and_keeps_legacy_order_and_target_path(tmp_path):
    target = tmp_path / "factory" / "locations.yaml"
    target.parent.mkdir()
    target.write_text("previous")
    document = {"robot": "FR3", "locations": {"rack.A1": {
        "site": "/World/Lab/Rack/A1", "zone": "rack", "approach": [0.] * 7,
        "grasp": [1.] * 7, "requires": [],
    }}}
    teaching.write_locations(target, document)
    assert target.read_text(encoding="utf-8") == yaml.safe_dump(document, sort_keys=False)
    assert yaml.safe_load(target.read_text()) == document
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("root_kind", ["builtin", "environment", "selected"])
def test_published_scene_guards_preserve_previous_output(tmp_path, monkeypatch, root_kind):
    monkeypatch.setattr(teaching, "ROOT", tmp_path / "repository")
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "environment-library"))
    roots = {"builtin": teaching.ROOT / "library", "environment": tmp_path / "environment-library",
             "selected": tmp_path / "selected-library"}
    target = roots[root_kind] / "scenes/fr3-test/2/locations.yaml"
    target.parent.mkdir(parents=True)
    previous = b"published output\n"
    target.write_bytes(previous)
    with pytest.raises(ValueError, match="immutable"):
        teaching.write_locations(target, {"locations": {}}, (roots["selected"],))
    assert target.read_bytes() == previous
    assert list(target.parent.iterdir()) == [target]
