"""Resolve one immutable Harness into the explicitly restricted execution profile."""

import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from apps.cli.lw.skills import bundle_root
from packages.info.plans import PlanError
from packages.library.agents import unpack_harness

PROFILE = "lw.agent-restricted/1"
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class FrozenHarness:
    agent_id: str
    version: int
    harness_hash: str
    archive_hash: str
    skills: tuple[tuple[str, str], ...]
    files: tuple[tuple[str, bytes], ...]
    platform_mcp: bool
    profile: str = PROFILE

    @property
    def manifest(self):
        return {
            "profile": self.profile,
            "agentId": self.agent_id,
            "harnessVersion": self.version,
            "harnessHash": self.harness_hash,
            "archiveHash": self.archive_hash,
            "skills": [{"name": name, "sha256": digest} for name, digest in self.skills],
            "files": [
                {"path": name, "sha256": sha256(data), "bytes": len(data)} for name, data in self.files
            ],
            "platformMcp": self.platform_mcp,
        }


def _unsupported(message):
    raise PlanError("unsupported_harness_capability", message, status=422)


def _profile(files, directory):
    # Readme is an explicitly retained document, never an executable hook.
    if any(name.startswith("hooks/") and name != "hooks/README.md" for name in files):
        _unsupported("Restricted Agent runs do not execute candidate hook files")
    servers = yaml.safe_load((directory / "mcp.yaml").read_text(encoding="utf-8"))["servers"]
    if not servers:
        return False
    if set(servers) != {"lab"}:
        _unsupported("Restricted Agent runs support only the owned platform MCP transport")
    declaration = servers["lab"]
    if declaration != {"command": "lw", "args": ["mcp"], "env": {"LW_URL": "${LW_URL}"}}:
        _unsupported("Platform MCP must use its declared fixed command and owned URL binding")
    # The runtime constructs this trusted transport; it never executes arbitrary
    # raw mcp.yaml commands or resolves their executable through candidate PATH.
    return True


def _skill(name, digest, source):
    if not NAME.fullmatch(name) or not DIGEST.fullmatch(digest):
        raise PlanError(
            "skill_version_unavailable", "An exact content-addressed skill pin is required", status=422
        )
    candidates = (source / "archive" / name / digest / "SKILL.md", source / name / "SKILL.md")
    for candidate in candidates:
        try:
            if any(path.is_symlink() or path.is_junction() for path in (candidate, *candidate.parents)):
                continue
            # Product skill discovery defines canonical UTF-8/LF bytes on all platforms.
            content = candidate.read_text(encoding="utf-8").encode("utf-8")
        except (OSError, UnicodeError):
            continue
        if sha256(content) == digest:
            return content
    raise PlanError("skill_version_unavailable", "The locked skill content is unavailable", status=422)


def prepare_harness(agents, agent_id, version, *, skills_root: Path | None = None):
    record, archive = agents.version(agent_id, version), agents.export(agent_id, version)
    if sha256(archive) != record["archiveHash"]:
        raise PlanError("harness_changed", "The selected immutable Harness changed", status=409)
    with tempfile.TemporaryDirectory(prefix="lw-harness-verify-") as temporary:
        directory = Path(temporary)
        paths = unpack_harness(archive, directory)
        platform_mcp = _profile(paths, directory)
        lock = yaml.safe_load((directory / "skills.lock").read_text(encoding="utf-8"))["skills"]
        if len({item["name"] for item in lock}) != len(lock):
            _unsupported("A Harness cannot repeat a skill name")
        source = skills_root if skills_root is not None else bundle_root()
        resolved = [
            (item["name"], item["version"], _skill(item["name"], item["version"], source)) for item in lock
        ]
        files = [("harness/" + name, (directory / name).read_bytes()) for name in paths]
        files.extend((f"skills/{name}/SKILL.md", data) for name, _digest, data in resolved)
    return FrozenHarness(
        agent_id="agent/" + agent_id.removeprefix("agent/"),
        version=version,
        harness_hash=record["contentHash"],
        archive_hash=record["archiveHash"],
        skills=tuple((name, digest) for name, digest, _data in resolved),
        files=tuple(sorted(files)),
        platform_mcp=platform_mcp,
    )
