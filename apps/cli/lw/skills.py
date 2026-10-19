"""Bundled product skills and a local, non-overwriting installer."""

import hashlib
import os
import stat
import sysconfig
import tempfile
from pathlib import Path

import yaml


class SkillError(ValueError):
    pass


def bundle_root():
    checkout = Path(__file__).resolve().parents[3]
    if (checkout / "pyproject.toml").is_file() and (checkout / "skills" / "lab" / "SKILL.md").is_file():
        return checkout / "skills"
    return Path(sysconfig.get_path("data")) / "share" / "spatial-sync" / "skills"


def _bundles():
    try:
        paths = sorted(bundle_root().glob("*/SKILL.md"))
        if not paths:
            raise SkillError("No product skills are bundled with this installation")
        result = []
        for path in paths:
            text = path.read_text(encoding="utf-8")
            content = text.encode("utf-8")
            header, separator, _ = text[4:].partition("\n---\n")
            if not text.startswith("---\n") or not separator:
                raise SkillError("A bundled skill has invalid frontmatter")
            metadata = yaml.safe_load(header)
            if (
                not isinstance(metadata, dict)
                or metadata.get("name") != path.parent.name
                or not isinstance(metadata.get("description"), str)
                or not metadata["description"].strip()
            ):
                raise SkillError("A bundled skill has invalid name or description")
            result.append((metadata, content))
        return result
    except (OSError, UnicodeError, yaml.YAMLError):
        raise SkillError("Bundled product skills are unavailable or invalid") from None


def list_skills():
    return [
        {
            "name": metadata["name"],
            "description": metadata["description"],
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for metadata, content in _bundles()
    ]


def _regular(path, directory=False):
    information = path.lstat()
    return (
        not path.is_symlink()
        and not getattr(information, "st_file_attributes", 0) & 0x400
        and (stat.S_ISDIR(information.st_mode) if directory else stat.S_ISREG(information.st_mode))
    )


def _existing(destination, content):
    if not destination.exists() and not destination.is_symlink():
        return False
    document = destination / "SKILL.md"
    if (
        _regular(destination, directory=True)
        and document.exists()
        and _regular(document)
        and document.read_bytes() == content
    ):
        return True
    raise SkillError(
        f"Skill destination conflicts: {destination}. Existing files were kept; choose another target"
    )


def install_skills(target=None):
    if target is not None and not target.strip():
        raise SkillError("An explicit target path must not be empty")
    destination = (
        Path(target).expanduser().absolute() if target is not None else Path.home() / ".claude" / "skills"
    )
    bundles = _bundles()
    try:
        if (destination.exists() or destination.is_symlink()) and not _regular(destination, directory=True):
            raise SkillError("The target must be a regular directory, not a file or link")
        # Detect every known conflict before creating either skill directory.
        existing = {
            metadata["name"]: _existing(destination / metadata["name"], content)
            for metadata, content in bundles
        }
        destination.mkdir(parents=True, exist_ok=True)
        installed, unchanged = [], []
        for metadata, content in bundles:
            name = metadata["name"]
            if existing[name]:
                unchanged.append(name)
                continue
            folder = destination / name
            try:
                folder.mkdir()
            except FileExistsError:
                if _existing(folder, content):
                    unchanged.append(name)
                    continue
                raise
            temporary = None
            try:
                descriptor, temporary = tempfile.mkstemp(prefix=".install-", dir=folder)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                # Publish a complete file atomically without replacing an existing name.
                os.link(temporary, folder / "SKILL.md")
                installed.append(name)
            finally:
                if temporary is not None:
                    Path(temporary).unlink(missing_ok=True)
                if not (folder / "SKILL.md").exists():
                    try:
                        folder.rmdir()  # Removes only an empty directory we created.
                    except OSError:
                        pass
        return {"target": str(destination), "installed": installed, "unchanged": unchanged}
    except OSError:
        raise SkillError(
            "Cannot install product skills; existing files were kept. Check the target and retry"
        ) from None
