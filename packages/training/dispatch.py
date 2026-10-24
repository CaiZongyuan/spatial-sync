"""Resolve the real training Plan layers; records without a layer stay embodied."""

from packages.info.plans import PlanError


def training_store(manager, *, document=None, identifier=None):
    if document is not None:
        layer = document.get("layer", "embodied")
        if layer not in ("agent", "embodied"):
            raise PlanError("invalid_plan_layer", "Select agent or embodied training", status=422)
        return manager.agent_rsi if layer == "agent" else manager.training_plans
    if identifier in manager.agent_rsi.records:
        return manager.agent_rsi
    return manager.training_plans


def training_stores(manager):
    return (manager.training_plans, manager.agent_rsi)


def require_goal_owner(manager, goal, layer, *, agent_id=None, suite=None):
    """Public Plan/grant publication shares one lock; a Goal belongs to one training layer."""
    from packages.training.loop_budgets import goal_id

    other = "embodied" if layer == "agent" else "agent"
    occupied = (
        any(
            goal_id(record.plan.model_dump(mode="json")) == goal
            for versions in manager.training_plans.history.values()
            for record in versions.values()
        )
        if other == "embodied"
        else goal in manager.agent_rsi.goals()
    )
    entries = getattr(getattr(getattr(manager, "notebooks", None), "store", None), "entries", {})
    occupied |= any(
        entry["goalId"] == goal and entry.get("layer", "embodied") == other for entry in entries.values()
    )
    if occupied:
        raise PlanError("goal_layer_conflict", "Select a Goal reserved for this training layer", status=409)
    if layer != "agent" or agent_id is None:
        return
    service = manager.agent_rsi
    owners = [
        {"agentId": record.plan.agentId, "suite": record.plan.suite}
        for record in service.history.values()
        if record.plan.goalId == goal
    ]
    baselines = {value["evaluationId"]: value for value in service.baselines.values()}
    owners += [
        {
            "agentId": value["agentId"],
            "suite": value.get("suite") or baselines.get(value["referenceEvaluationId"], {}).get("suite"),
        }
        for value in service.optimizer_sessions.values()
        if value["goalId"] == goal
    ]
    if any(owner["agentId"] != agent_id or suite is not None and owner["suite"] != suite for owner in owners):
        raise PlanError("goal_owner_conflict", "A Goal keeps its fixed Agent and Suite", status=409)
