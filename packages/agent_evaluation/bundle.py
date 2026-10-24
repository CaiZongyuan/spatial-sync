"""Freeze credential-free Harness inputs and exact locked product-skill bytes."""

import hashlib
import re
import tempfile
from pathlib import Path

import yaml

from apps.cli.lw.skills import bundle_root
from packages.info.plans import PlanError
from packages.library.agents import unpack_harness


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def freeze_harness(agents, name, version):
    metadata, archive = agents.version(name, version), agents.export(name, version)
    with tempfile.TemporaryDirectory(prefix="lw-public-harness-") as temporary:
        root = Path(temporary)
        names = unpack_harness(archive, root)
        lock = yaml.safe_load((root / "skills.lock").read_text(encoding="utf-8"))["skills"]
        files = {"harness/" + name: (root / name).read_text(encoding="utf-8") for name in names}
    source = bundle_root()
    skills = []
    if len({item["name"] for item in lock}) != len(lock):
        raise PlanError("skill_version_unavailable", "Locked skills must have unique names", status=422)
    for item in lock:
        name, expected = item["name"], item["version"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise PlanError(
                "skill_version_unavailable", "Exact content-addressed skill pins are required", status=422
            )
        found = files.get(f"harness/skills/{name}/SKILL.md")
        if found is not None and sha256(found.encode()) != expected:
            raise PlanError("skill_version_unavailable", "Agent skill differs from its exact pin", status=422)
        for path in (source / "archive" / name / expected / "SKILL.md", source / name / "SKILL.md"):
            if found is not None:
                break
            try:
                if any(parent.is_symlink() or parent.is_junction() for parent in (path, *path.parents)):
                    continue
                content = path.read_text(encoding="utf-8")
                if sha256(content.encode()) == expected:
                    found = content
                    break
            except (OSError, UnicodeError):
                pass
        if found is None:
            raise PlanError(
                "skill_version_unavailable", "Exact locked skill bytes are unavailable", status=422
            )
        files[f"skills/{name}/SKILL.md"] = found
        skills.append({"name": name, "sha256": expected})
    return {
        "contentHash": metadata["contentHash"],
        "archiveHash": sha256(archive),
        "skills": skills,
        "files": [
            {
                "path": name,
                "sha256": sha256(content.encode()),
                "bytes": len(content.encode()),
                "content": content,
            }
            for name, content in sorted(files.items())
        ],
    }
