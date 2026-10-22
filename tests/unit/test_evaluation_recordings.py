import json
from pathlib import Path

from apps.api.app.evaluation_recordings import EvaluationRecordingRepository
from apps.api.app.recordings import RecordingRepository
from apps.api.app.settings import Settings

FIXTURE = Path(__file__).resolve().parents[2] / "tests/fixtures/recordings/v2/recording.json"


def metadata(root, identifier, order):
    directory = root / identifier
    directory.mkdir(parents=True)
    value = json.loads(FIXTURE.read_bytes()) | {
        "sessionId": identifier,
        "status": "complete",
        "createdAt": f"2026-01-01T00:00:0{order}Z",
        "segments": [],
        "revisions": {},
    }
    (directory / "recording.json").write_text(json.dumps(value), encoding="utf-8")


async def test_durable_pins_load_before_startup_pruning_and_ordinary_limit_stays_one(tmp_path):
    root = tmp_path / "recordings"
    for order, identifier in enumerate(("session-pin-a", "session-pin-b", "session-old", "session-new")):
        metadata(root, identifier, order)
    live_directory = root / "live-retained"
    live_directory.mkdir()
    live_value = json.loads((FIXTURE.parents[1] / "v2-live/recording.json").read_bytes()) | {
        "sessionId": live_directory.name, "status": "complete", "createdAt": "2025-01-01T00:00:00Z",
    }
    live_metadata = json.dumps(live_value).encode()
    (live_directory / "recording.json").write_bytes(live_metadata)
    settings = Settings(recordings_root=root, state_root=tmp_path / "state", recordings_keep=1)
    first = EvaluationRecordingRepository(settings, None)
    first.pin("session-pin-a")
    first.pin("session-pin-b")
    restarted = EvaluationRecordingRepository(settings, None)
    await restarted.startup()
    assert {value["sessionId"] for value in restarted.list()} == {
        "session-pin-a",
        "session-pin-b",
        "session-new",
        "live-retained",
    }
    assert (live_directory / "recording.json").read_bytes() == live_metadata
    assert not (root / "session-old").exists()
    assert json.loads((settings.state_root / "evaluations/recording-pins.json").read_bytes()) == [
        "session-pin-a", "session-pin-b",
    ]
    assert all(path.is_dir() for path in root.iterdir())


async def test_plain_recording_repository_keeps_its_existing_ordinary_policy(tmp_path):
    root = tmp_path / "ordinary"
    metadata(root, "session-old", 0)
    metadata(root, "session-new", 1)
    repository = RecordingRepository(Settings(recordings_root=root, recordings_keep=1), None)
    await repository.startup()
    assert [row["sessionId"] for row in repository.list()] == ["session-new"]
