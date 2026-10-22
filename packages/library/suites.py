"""Pinned evaluation suite packages. Public projections never include private inputs."""

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import yaml

from packages.info.conditions import CompiledConditions
from packages.library.agent_suites import load_agent_suite
from packages.library.archive import package_files
from packages.library.common import NAME, LibraryError, content_hash, document
from packages.protocol.schema import MAX_SESSION_SEED

SUITE_ID = re.compile(r"suite/([a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)")
HASH = re.compile(r"[a-f0-9]{64}")


def invalid_suite():
    # Parser diagnostics can contain held-out cases, conditions or private seeds.
    return LibraryError("invalid_suite", "Invalid embodied suite package", 422)


@dataclass(frozen=True)
class SuiteVersion:
    id: str
    path: Path
    root: str
    content_hash: str
    metadata: dict
    task: dict
    seeds: dict

    def public(self):
        cases = [
            {"id": case["id"], "params": deepcopy(case["params"])}
            for case in self.metadata["episodes"]
            if case["split"] == "train"
        ]
        coverage = {}
        for case in cases:
            for name, value in case["params"].items():
                if value not in coverage.setdefault(name, []):
                    coverage[name].append(value)
        return {
            "id": self.id,
            "layer": "embodied",
            "title": self.metadata["title"],
            "contentHash": self.content_hash,
            "scene": self.metadata["scene"],
            "sceneContentHash": self.metadata["sceneContentHash"],
            "source": self.metadata["source"],
            "target": self.task["robot"],
            "implements": self.task["implements"],
            "split": "train",
            "episodes": cases,
            "coverage": coverage,
            "milestones": [stage["id"] for stage in self.task["milestones"]],
        }

    def verify(self):
        if content_hash(self.path) != self.content_hash:
            raise LibraryError("suite_changed", "Suite package changed after indexing", 409)


def load_suite(path, identifier, root):
    try:
        files = set(package_files(path))
        data = document(path / "suite.yaml")
        if data.get("layer") == "agent":
            return load_agent_suite(path, identifier, root, data)
        if set(data) != {"id", "title", "layer", "scene", "sceneContentHash", "source", "episodes"}:
            raise ValueError
        match = SUITE_ID.fullmatch(identifier)
        if (
            not match
            or data["id"] != "suite/" + match[1]
            or data["layer"] != "embodied"
            or not isinstance(data["title"], str)
            or not data["title"]
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*", data["scene"])
            or not HASH.fullmatch(data["sceneContentHash"])
            or data["source"] not in ("synthetic", "newton")
            or files != {"suite.yaml", "task.yaml", "private/seeds.yaml"}
        ):
            raise ValueError
        task, seeds = document(path / "task.yaml"), document(path / "private/seeds.yaml")
        json.dumps(task, allow_nan=False)
        if (
            set(task) != {"name", "scene", "robot", "implements", "success", "milestones", "timeoutSeconds"}
            or not NAME.fullmatch(task["name"])
            or task["scene"] != data["scene"]
            or not isinstance(task["robot"], str)
            or not task["robot"].startswith("/")
            or not re.fullmatch(r"[a-z][a-z0-9-]*/v[1-9][0-9]*\.[a-zA-Z][a-zA-Z0-9]*", task["implements"])
            or type(task["timeoutSeconds"]) not in (int, float)
            or not 0 < task["timeoutSeconds"] <= 3600
        ):
            raise ValueError
        CompiledConditions([task["success"]])
        if not isinstance(task["milestones"], list) or not task["milestones"]:
            raise ValueError
        stages = set()
        for stage in task["milestones"]:
            if set(stage) != {"id", "when"} or not NAME.fullmatch(stage["id"]) or stage["id"] in stages:
                raise ValueError
            stages.add(stage["id"])
            CompiledConditions([stage["when"]])
        if not isinstance(data["episodes"], list) or not 1 <= len(data["episodes"]) <= 10000:
            raise ValueError
        identifiers, train = set(), 0
        for case in data["episodes"]:
            if (
                set(case) != {"id", "split", "params"}
                or not NAME.fullmatch(case["id"])
                or case["id"] in identifiers
                or case["split"] not in ("train", "test")
                or not isinstance(case["params"], dict)
            ):
                raise ValueError
            json.dumps(case["params"], allow_nan=False)
            identifiers.add(case["id"])
            train += case["split"] == "train"
        if (
            not train
            or set(seeds) != identifiers
            or any(type(seed) is not int or not 0 <= seed <= MAX_SESSION_SEED for seed in seeds.values())
        ):
            raise ValueError
        return SuiteVersion(identifier, path, root, content_hash(path), data, task, seeds)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
        raise invalid_suite() from None


class SuiteLibrary:
    def __init__(self, roots):
        self.roots = roots
        self.versions = {}

    def refresh(self):
        versions = {}
        for root_name, root in self.roots.items():
            for path in sorted((root / "suites").glob("*/*/suite.yaml")):
                if not path.parent.resolve().is_relative_to(root) or any(
                    parent.is_symlink() or parent.is_junction()
                    for parent in (path, path.parent, path.parent.parent, path.parent.parent.parent)
                ):
                    raise invalid_suite()
                name, revision = path.parent.relative_to(root / "suites").parts
                if not NAME.fullmatch(name) or not revision.isdecimal() or str(int(revision)) != revision:
                    raise invalid_suite()
                identifier = f"suite/{name}@{revision}"
                version = load_suite(path.parent, identifier, root_name)
                if identifier in versions and versions[identifier].content_hash != version.content_hash:
                    raise LibraryError("suite_conflict", "Ambiguous suite revision", 409)
                versions[identifier] = version
        self.versions = versions

    def resolve(self, identifier):
        version = self.versions.get(identifier)
        if version is None:
            raise LibraryError("unknown_suite", "Select an exact suite/name@revision", 404)
        version.verify()
        return version

    def list(self):
        return [version.public() for version in self.versions.values()]
