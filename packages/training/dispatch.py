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
