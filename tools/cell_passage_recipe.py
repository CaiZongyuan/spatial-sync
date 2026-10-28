"""Cell Passage client recipe and revision rules; no gateway workflow engine."""

import json
from copy import deepcopy
from pathlib import Path

CLIENT_CONTRACT = json.loads(
    Path(__file__).with_name("cell_passage_contract.json").read_text(encoding="utf8")
)
STAGES = CLIENT_CONTRACT["stages"]


def revise_remaining(plan, *, owner, epoch, material_revision, expected_material_revision, replacements=None):
    """Build a normal revision document after reviewing the current material state.

    A failed action needs an explicit replacement, including any recalculated
    remaining volume. Merely changing operationId would replay the original amount.
    Done content is immutable; the gateway carries its existing result forward.
    """
    if material_revision != expected_material_revision:
        raise ValueError("Reviewed material revision changed; inspect again")
    replacements = replacements or {}
    if not owner or not isinstance(epoch, int) or epoch < 0:
        raise ValueError("A current control owner and epoch are required")
    known = {step["id"] for step in plan["steps"]}
    if replacements.keys() - known:
        raise ValueError("Replacement refers to an unknown step")
    steps = []
    for original in plan["steps"]:
        identity, status = original["id"], original["status"]
        if status == "running":
            raise ValueError("Observe or cancel running steps before revising")
        if status == "failed" and identity not in replacements:
            raise ValueError(f"Failed step {identity} requires a reviewed replacement")
        if status in ("done", "skipped") and identity in replacements:
            raise ValueError("Completed/skipped steps must be preserved")
        selected = replacements.get(identity, original)
        if selected["id"] != identity:
            raise ValueError("Replacement must preserve the step ID")
        step = deepcopy({name: selected[name] for name in ("id", "do", "check", "wait", "expect",
                                                         "timeoutSeconds") if name in selected})
        if status not in ("done", "skipped") and "do" in step:
            params = step["do"].setdefault("params", {})
            params.update(owner=owner, epoch=epoch,
                          operationId=f"passage-{plan['id']}-r{plan['revision'] + 1}-s{identity}-e{epoch}")
        steps.append(step)
    return {"title": plan["title"], "target": "session", "steps": steps}
