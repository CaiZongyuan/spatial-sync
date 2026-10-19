"""Installed local skill commands preserve user files without a gateway or a home install."""

import hashlib
import json
import os
import subprocess

import pytest
from cli_support import LW, ROOT


def invoke(directory, *arguments, code=0):
    process = subprocess.run(
        [str(LW), *arguments, "--json"],
        cwd=directory,
        env=os.environ | {"LW_URL": "http://[broken"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert process.returncode == code, (process.stdout, process.stderr)
    return json.loads(process.stdout)


def test_local_catalog_and_repeat_install_preserve_all_user_files_in_a_space_path(tmp_path):
    catalog = invoke(tmp_path, "skills", "ls")
    assert [item["name"] for item in catalog] == ["lab", "lab-operate"]
    target = tmp_path / "Agent skills with spaces"
    target.mkdir()
    other = target / "unrelated-user-skill.md"
    other.write_bytes(b"keep exact user content")
    first = invoke(tmp_path, "skills", "install", "--target", str(target))
    assert first == {"target": str(target), "installed": ["lab", "lab-operate"], "unchanged": []}
    timestamps = {}
    for item in catalog:
        installed = target / item["name"] / "SKILL.md"
        source = ROOT / "skills" / item["name"] / "SKILL.md"
        assert installed.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
        assert hashlib.sha256(installed.read_bytes()).hexdigest() == item["sha256"]
        timestamps[item["name"]] = installed.stat().st_mtime_ns
    extra = target / "lab" / "personal-notes.txt"
    extra.write_bytes(b"user notes inside an installed skill")
    repeated = invoke(tmp_path, "skills", "install", "--target", str(target))
    assert repeated == {"target": str(target), "installed": [], "unchanged": ["lab", "lab-operate"]}
    assert all((target / name / "SKILL.md").stat().st_mtime_ns == stamp for name, stamp in timestamps.items())
    assert other.read_bytes() == b"keep exact user content"
    assert extra.read_bytes() == b"user notes inside an installed skill"


@pytest.mark.parametrize("collision", ["file", "different-skill", "missing-skill"])
def test_conflicting_destination_stops_before_installing_either_skill(tmp_path, collision):
    target = tmp_path / "user skills"
    blocked = target / "lab-operate"
    target.mkdir()
    if collision == "file":
        blocked.write_bytes(b"user-owned plain file")
    else:
        blocked.mkdir()
        (blocked / ("SKILL.md" if collision == "different-skill" else "notes.txt")).write_bytes(b"user-owned")
    before = {
        str(path.relative_to(target)): path.read_bytes() for path in target.rglob("*") if path.is_file()
    }
    result = invoke(tmp_path, "skills", "install", "--target", str(target), code=1)
    assert "conflict" in result["error"]["message"].lower()
    assert not (target / "lab").exists()
    assert before == {
        str(path.relative_to(target)): path.read_bytes() for path in target.rglob("*") if path.is_file()
    }


def test_existing_root_file_is_not_replaced(tmp_path):
    target = tmp_path / "user-file"
    target.write_bytes(b"keep")
    invoke(tmp_path, "skills", "install", "--target", str(target), code=1)
    assert target.read_bytes() == b"keep"


def test_empty_explicit_target_is_refused_instead_of_falling_back_to_home(tmp_path):
    result = invoke(tmp_path, "skills", "install", "--target", "", code=1)
    assert "must not be empty" in result["error"]["message"]
    assert list(tmp_path.iterdir()) == []


def test_linked_skill_directory_is_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_bytes(b"keep linked user file")
    target = tmp_path / "skills"
    target.mkdir()
    if os.name == "nt":
        linked = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target / "lab"), str(outside)],
            capture_output=True,
            timeout=10,
        )
        assert linked.returncode == 0, linked.stdout + linked.stderr
    else:
        (target / "lab").symlink_to(outside, target_is_directory=True)
    invoke(tmp_path, "skills", "install", "--target", str(target), code=1)
    assert (outside / "SKILL.md").read_bytes() == b"keep linked user file"
    assert not (target / "lab-operate").exists()
