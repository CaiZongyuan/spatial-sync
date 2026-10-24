"""Check the declared one-change surface against exact Harness archive bytes."""

import difflib
import json
import re
import tempfile
from pathlib import Path

import yaml

from packages.info.plans import PlanError
from packages.library.agents import unpack_harness


def rejected():
    raise PlanError("agent_change_out_of_scope", "Declare one bounded permitted Harness change", status=422)


def validate_change(agents, identifier, reference, candidate, change):
    before, after = agents.export(identifier, reference), agents.export(identifier, candidate)
    with tempfile.TemporaryDirectory(prefix="lw-agent-change-") as temporary:
        left, right = Path(temporary) / "before", Path(temporary) / "after"
        left.mkdir()
        right.mkdir()
        names = set(unpack_harness(before, left)) | set(unpack_harness(after, right))

        def content(root, name):
            return (root / name).read_text(encoding="utf-8") if (root / name).exists() else ""

        changed = [name for name in sorted(names) if content(left, name) != content(right, name)]
        if change.kind == "instructions":
            if changed != ["AGENTS.md"] or change.path != "AGENTS.md" or change.field is not None:
                rejected()
            blocks = [content(root, "AGENTS.md").strip().split("\n\n") for root in (left, right)]
            edits = [
                op
                for op in difflib.SequenceMatcher(a=blocks[0], b=blocks[1]).get_opcodes()
                if op[0] != "equal"
            ]
            if len(edits) != 1 or max(edits[0][2] - edits[0][1], edits[0][4] - edits[0][3]) > 1:
                rejected()
        elif change.kind == "skill":
            if (
                not re.fullmatch(r"skills/[a-z0-9]+(?:-[a-z0-9]+)*/SKILL\.md", change.path)
                or change.field is not None
                or change.path not in changed
                or set(changed) - {change.path, "skills.lock"}
            ):
                rejected()
            old = yaml.safe_load(content(left, "skills.lock"))["skills"]
            new = yaml.safe_load(content(right, "skills.lock"))["skills"]
            name = change.path.split("/")[1]
            if [item for item in old if item["name"] != name] != [
                item for item in new if item["name"] != name
            ]:
                rejected()
        else:
            if changed != ["mcp.yaml"] or change.path != "mcp.yaml" or change.field is None:
                rejected()
            match = re.fullmatch(r"servers\.([a-z0-9]+(?:-[a-z0-9]+)*)\.args", change.field)
            if match is None:
                rejected()
            old, new = (yaml.safe_load(content(root, "mcp.yaml")) for root in (left, right))
            try:
                old["servers"][match[1]]["args"] = new["servers"][match[1]]["args"]
            except (KeyError, TypeError):
                rejected()
            if json.dumps(old, sort_keys=True) != json.dumps(new, sort_keys=True):
                rejected()
        return changed
