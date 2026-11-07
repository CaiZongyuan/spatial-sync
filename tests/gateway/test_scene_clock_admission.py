"""Clock intent is distinguished from executed evidence before Source allocation."""

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource


@pytest.mark.parametrize("metadata_dt,intent,measured,expected", [
    (.0005, None, None, 409),
    (None, .0005, None, 409),
    (None, .0005, .001, 409),
    (None, .0005, .0005, 503),
    (None, None, None, 503),
], ids=["metadata-unmeasured", "usd-unmeasured", "mismatched-clock", "measured-clock", "legacy-clock"])
def test_clock_admission_uses_report_intent_and_real_evidence_before_source_creation(
    tmp_path, monkeypatch, metadata_dt, intent, measured, expected
):
    # These are controlled pre-clock/contradictory report fixtures. The test
    # proves admission ordering; the real CPU/HTTP test proves clock execution.
    root = tmp_path / "library"
    path = root / "scenes/clock-admission/1"
    path.mkdir(parents=True)
    metadata = {"name": "clock-admission", "title": "Clock admission", "sources": ["newton"],
                "stage": "python:falling-boxes"}
    if metadata_dt is not None:
        metadata["simulation"] = {"physicsDt": metadata_dt}
    (path / "scene.yaml").write_text(yaml.safe_dump(metadata))
    (path / "devices.yaml").write_text("scene: clock-admission\nentities: []\n")
    (path / "locations.yaml").write_text("locations: {}\n")
    report = {"schema": "lw.validation/1", "subject": "scene:clock-admission@1", "launchable": True,
              "checks": [{"id": "P1", "tier": "physics", "status": "pass", "message": "Legacy fixture"}]}
    if intent is not None:
        report["authoredPhysicsDt"] = intent
    if measured is not None:
        report["physics"] = {"status": "completed", "device": "cpu", "ranAt": "2026-11-07T00:00:00Z",
                             "toolVersions": {"newton": "1.7.0"}, "physicsDt": measured}
    (path / "validation.json").write_text(json.dumps(report))
    monkeypatch.setenv("LW_LIBRARY", str(root))
    allocated = []
    started = []

    class Boundary(SyntheticSource):
        async def start(self, scene_id, options):
            started.append(options.physicsDt)
            raise RuntimeError("Intentional SDK-free boundary; no physics verdict")

    def factory(kind, sid):
        allocated.append(sid)
        return Boundary(sid)

    with TestClient(create_app(Settings(recordings_enabled=False),
                               source_factory=factory)) as client:
        result = client.post("/api/v1/sessions", json={"sceneVersion": "clock-admission@1"})
        assert result.status_code == expected, result.text
        if expected == 409:
            assert result.json()["detail"]["code"] == "scene_clock_unqualified"
            assert allocated == started == []
        else:
            assert len(allocated) == 1
            assert started == [measured if measured is not None else 1 / 240]
