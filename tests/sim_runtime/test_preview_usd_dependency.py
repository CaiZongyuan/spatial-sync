"""Real preview capture pins the exact USD physics helper executed by Newton."""

from pathlib import Path

import pytest

from packages.library import previews
from packages.library.entity_types import EntityTypes, RobotEntityType
from packages.library.scenes import SceneLibrary
from tools.capture_scene_preview import capture


@pytest.mark.newton
async def test_newton_preview_stales_when_its_usd_helper_changes(tmp_path, monkeypatch):
    pytest.importorskip("newton")
    pytest.importorskip("mujoco")

    root = Path(__file__).resolve().parents[2]
    user = tmp_path / "user"
    scenes = SceneLibrary(user_root=user, state_root=tmp_path / "state")
    version = scenes.resolve("microduck-arena@2", launch=True, source="newton")
    repository = previews.PreviewRepository(tmp_path / "captures", bundled_root=tmp_path / "empty")
    captured = await capture(version, "newton", tmp_path / "captures")
    helper = "apps/node/usd_mjcf.py"
    copy = tmp_path / "usd_mjcf.py"
    copy.write_bytes((root / helper).read_bytes())
    dependency_path = previews.dependency_path

    def isolated(dependency):
        resolved = dependency_path(dependency)
        return copy if dependency["path"] == helper else resolved

    monkeypatch.setattr(previews, "dependency_path", isolated)
    assert repository.describe(version, "newton")["status"] == "available"
    EntityTypes(user).publish(RobotEntityType.model_validate({
        "type": "u-other-bot/v1", "title": "Other Robot", "actions": {"wave": {"longRunning": True}},
    }))
    assert repository.describe(scenes.resolve(version.id), "newton")["status"] == "available"

    copy.write_bytes(copy.read_bytes() + b"\nDEFAULT_FRICTION = (0.37, 0.005, 0.0001)\n")
    assert repository.describe(version, "newton")["status"] == "stale"
    dependencies = {row["path"] for row in captured["dependencies"]}
    node_dependencies = {path for path in dependencies if path.startswith("apps/node/")}
    assert node_dependencies == {helper}
