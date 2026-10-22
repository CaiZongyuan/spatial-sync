"""Private Agent grading packages with an explicit public train-only projection."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from packages.info.conditions import CompiledConditions
from packages.library.archive import package_files, relative_path
from packages.library.common import NAME, LibraryError, content_hash, document

DIMENSIONS = ("finalState", "rejectedHighRisk", "steps", "elapsed", "planFirst")


@dataclass(frozen=True)
class AgentSuiteVersion:
    id: str
    path: Path
    root: str
    content_hash: str
    metadata: dict
    statements: dict
    grading: dict

    def verify(self):
        if content_hash(self.path) != self.content_hash:
            raise LibraryError("suite_changed", "Suite package changed after indexing", 409)

    def public(self):
        return {
            "id": self.id, "layer": "agent", "title": self.metadata["title"],
            "contentHash": self.content_hash, "scene": self.metadata["scene"],
            "sceneContentHash": self.metadata["sceneContentHash"], "source": self.metadata["source"],
            "target": self.metadata["target"], "split": "train", "runsPerCase": self.metadata["runsPerCase"],
            "cases": [{"id": case["id"], "statement": self.statements[case["id"]]}
                      for case in self.metadata["cases"] if case["split"] == "train"],
            "gradingDimensions": list(DIMENSIONS),
        }


def load_agent_suite(path, identifier, root, data):
    try:
        if set(data) != {
            "id", "title", "layer", "scene", "sceneContentHash", "source", "target", "runsPerCase", "cases"
        }:
            raise ValueError
        if (
            data["id"] != identifier.partition("@")[0] or data["layer"] != "agent"
            or not isinstance(data["title"], str) or not data["title"]
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*", data["scene"])
            or not re.fullmatch(r"[a-f0-9]{64}", data["sceneContentHash"])
            or data["source"] not in ("synthetic", "newton")
            or not re.fullmatch(r"agent/[a-z0-9]+(?:-[a-z0-9]+)*", data["target"])
            or type(data["runsPerCase"]) is not int or not 3 <= data["runsPerCase"] <= 100
            or not isinstance(data["cases"], list) or not 1 <= len(data["cases"]) <= 1000
        ):
            raise ValueError
        files, statements, grading, expected, train = set(package_files(path)), {}, {}, {"suite.yaml"}, 0
        for case in data["cases"]:
            if (set(case) != {"id", "statement", "grading", "split"} or not NAME.fullmatch(case["id"])
                or case["id"] in statements or case["split"] not in ("train", "test")):
                raise ValueError
            paths = (("statement", "statements/", ".md"), ("grading", "private/", ".yaml"))
            for key, prefix, suffix in paths:
                name = case[key]
                relative_path(name)
                if not name.startswith(prefix) or not name.endswith(suffix) or name in expected:
                    raise ValueError
                expected.add(name)
            statements[case["id"]] = (path / case["statement"]).read_text(encoding="utf-8")
            if not statements[case["id"]].strip():
                raise ValueError
            rubric = document(path / case["grading"])
            if set(rubric) != {"dimensions"} or not isinstance(rubric["dimensions"], list):
                raise ValueError
            dimensions, weight = set(), 0
            for dimension in rubric["dimensions"]:
                if (set(dimension) != {"id", "weight", "when"} or dimension["id"] not in DIMENSIONS
                    or dimension["id"] in dimensions or type(dimension["weight"]) is not int
                    or not 0 < dimension["weight"] <= 100):
                    raise ValueError
                dimensions.add(dimension["id"])
                weight += dimension["weight"]
                conditions = CompiledConditions([dimension["when"]])
                for condition in conditions:
                    entity = condition.get("entity")
                    if not isinstance(entity, str) or not (
                        entity.startswith("/") or entity in ("$process", "$session", "$alerts")
                    ):
                        raise ValueError
                    if dimension["id"] != "finalState" and entity != "$process":
                        raise ValueError
            if weight != 100 or "finalState" not in dimensions:
                raise ValueError
            grading[case["id"]] = rubric
            train += case["split"] == "train"
        if not train or files != expected:
            raise ValueError
        return AgentSuiteVersion(identifier, path, root, content_hash(path), data, statements, grading)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
        raise LibraryError("invalid_suite", "Invalid Agent suite package", 422) from None
