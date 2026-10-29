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

    A failed or skipped step needs an explicit replacement, including any recalculated
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
        result = plan.get("stepResults", {}).get(str(identity), {})
        if status == "pending" and (result.get("invocationId") or result.get("invocationIds")
                                    or result.get("consumed")):
            raise ValueError("Observe the admitted pending step before revising")
        if status in ("failed", "skipped") and identity not in replacements:
            raise ValueError(f"{status.capitalize()} step {identity} requires a reviewed replacement")
        if status == "done" and identity in replacements:
            raise ValueError("Completed steps must be preserved")
        selected = replacements.get(identity, original)
        if selected["id"] != identity:
            raise ValueError("Replacement must preserve the step ID")
        step = deepcopy({name: selected[name] for name in ("id", "do", "check", "wait", "expect",
                                                         "timeoutSeconds") if name in selected})
        if (status == "failed" and original.get("do", {}).get("action")
                in CLIENT_CONTRACT["liquidActions"]["quantityIntent"] and "do" in step):
            if (step["do"]["action"] not in CLIENT_CONTRACT["liquidActions"]["literal"]
                    or "volumeUl" not in step["do"].get("params", {})):
                raise ValueError("Failed quantity intent requires an explicit literal remainder after review")
        if status != "done" and "do" in step:
            params = step["do"].setdefault("params", {})
            params.update(owner=owner, epoch=epoch)
            if status == "failed" or identity in replacements:
                params["operationId"] = f"passage-{plan['id']}-r{plan['revision'] + 1}-s{identity}-e{epoch}"
        steps.append(step)
    return {"title": plan["title"], "target": "session", "steps": steps}


def revise_round_remaining(plans, *, owner, epoch, material_revision, expected_material_revision,
                           replacements=None):
    """Prepare explicit round revisions and dependent references without changing done evidence.

    Callers publish every resulting document through the ordinary revision API
    while dispatch is stopped, then recheck the actual round before continuing.
    """
    replacements = replacements or {}
    documents, changed = {}, {}
    for plan in plans:
        if all(step["status"] == "done" for step in plan["steps"]):
            if replacements.get(plan["id"]):
                raise ValueError("Completed steps must be preserved")
            continue
        document = revise_remaining(
            plan, owner=owner, epoch=epoch, material_revision=material_revision,
            expected_material_revision=expected_material_revision, replacements=replacements.get(plan["id"]),
        )
        documents[plan["id"]] = document
        for old, new in zip(plan["steps"], document["steps"], strict=True):
            if "do" not in old or "do" not in new or old["status"] == "done":
                continue
            old_id, new_id = old["do"]["params"]["operationId"], new["do"]["params"]["operationId"]
            if old_id != new_id:
                if old_id in changed and changed[old_id] != new_id:
                    raise ValueError("Conflicting replacement for one domain operation identity")
                changed[old_id] = new_id

    def references(condition):
        children = condition.get("all")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    references(child)
        if condition.get("property") in ("completedCycle", "lastOperationId"):
            value = condition.get("equals")
            if isinstance(value, str) and value in changed:
                condition["equals"] = changed[value]

    for plan in plans:
        if plan["id"] not in documents:
            continue
        for old, step in zip(plan["steps"], documents[plan["id"]]["steps"], strict=True):
            if old["status"] == "done":
                continue
            call = step.get("do", {})
            if call.get("action") == "pellet":
                params = call["params"]
                params["cycle"] = changed.get(params["cycle"], params["cycle"])
            for name in ("check", "expect", "wait"):
                references(step.get(name, {}))
    return documents
