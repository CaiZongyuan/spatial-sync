"""Every shipped skill directory must reach the installed wheel's data files."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "share/spatial-sync/skills/"


def _declared_skill_data_files():
    documents = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    files = documents["tool"]["setuptools"]["data-files"]
    return {key: sources for key, sources in files.items() if key.startswith(PREFIX)}


def _shipped_skill_data_files():
    return {
        key.removeprefix(PREFIX): sources
        for key, sources in _declared_skill_data_files().items()
        if "/archive/" not in key
    }


def test_every_bundled_skill_is_declared_in_the_wheel_data_files():
    declared = _shipped_skill_data_files()
    shipped = {path.parent.name: path for path in (ROOT / "skills").glob("*/SKILL.md")}

    assert set(shipped) == set(declared)
    for name, path in shipped.items():
        assert declared[name] == [path.relative_to(ROOT).as_posix()]


def test_no_declared_skill_data_file_is_missing_from_the_checkout():
    for name, sources in _declared_skill_data_files().items():
        for source in sources:
            assert (ROOT / source).is_file(), f"{name} declares a missing file: {source}"
