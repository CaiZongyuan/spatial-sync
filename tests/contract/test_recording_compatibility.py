"""Published metadata versions share the original binary segment contract."""

import json
from pathlib import Path

import pytest

from packages.info.log import read_segment
from packages.info.recording import recording_metadata

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/recordings"


@pytest.mark.parametrize("version", [1, 2])
def test_published_recording_identity_and_binary_snapshots(version):
    directory = FIXTURES / f"v{version}"
    metadata = recording_metadata(json.loads((directory / "recording.json").read_bytes()))
    assert metadata["formatVersion"] == version and metadata["protocolVersion"] == 2
    assert metadata["kind"] == "session" and metadata["sceneVersion"] == f"lab-demo@{version}"
    assert metadata["sceneContentHash"] is None if version == 1 else len(metadata["sceneContentHash"]) == 64
    assert 10000 <= metadata["durationMs"] < 11000
    for segment in metadata["segments"]:
        payload = (directory / segment["file"]).read_bytes()
        assert payload[:6] == b"LWLG\x01\x00"
        decoded = read_segment(payload)
        assert not decoded.truncated and decoded.valid_bytes == len(payload)
        assert decoded.records[0].kind == 1
        assert json.loads(decoded.records[1].payload)["type"] == "entity.snapshot"
