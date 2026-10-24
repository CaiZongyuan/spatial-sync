"""Freeze public Agent inputs and model capability facts before human admission."""

import hashlib
import json
from copy import deepcopy
from decimal import Decimal

from packages.agents.bundle import prepare_harness
from packages.agents.provider import ProviderQuote
from packages.info.plans import PlanError
from packages.models import ModelResources
from packages.vault import Vault

PRICE_FIELDS = {
    "input_tokens": "inputUsdPerMillion",
    "output_tokens": "outputUsdPerMillion",
    "cache_read_input_tokens": "cacheReadUsdPerMillion",
    "cache_creation_input_tokens": "cacheWriteUsdPerMillion",
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class AgentPlanInputs:
    def __init__(self, manager, agents, describe_adapter):
        self.manager, self.agents, self.describe_adapter = manager, agents, describe_adapter
        self.resources = ModelResources(manager.settings.state_root)
        self.vault = Vault(manager.settings.state_root)

    def quote(self, model):
        table = self.resources.prices()
        price = next(
            (value for value in table["models"] if value["provider"] + "/" + value["modelId"] == model), {}
        )
        return ProviderQuote(
            table["revision"], {key: price.get(field) for key, field in PRICE_FIELDS.items()}
        )

    def freeze(self, plan):
        model = next(
            (value for value in self.resources.models()["models"] if value["ref"] == plan.model.ref), None
        )
        if model is None or not model["credentialName"] or model["credentialName"] != plan.credentialRef:
            raise PlanError(
                "agent_model_unavailable",
                "Select a registered model and its configured credential",
                status=422,
            )
        if plan.credentialRef not in self.vault.names():
            raise PlanError(
                "agent_credential_unavailable", "The configured credential name is unavailable", status=422
            )
        harness = prepare_harness(self.agents, plan.agent.id, plan.agent.harnessVersion)
        suite = self.manager.evaluations.suites.resolve(plan.suite).public()
        if suite["layer"] != "agent" or suite["target"] != plan.agent.id or suite["source"] != "synthetic":
            raise PlanError(
                "agent_suite_unavailable", "Select a compatible public Synthetic Agent Suite", status=422
            )
        scene = self.manager.library.resolve(suite["scene"], launch=True, source=suite["source"])
        if scene.content_hash != suite["sceneContentHash"]:
            raise PlanError("agent_inputs_changed", "The Suite Scene content changed", status=409)
        required = len(suite["cases"]) * suite["runsPerCase"]
        if required > plan.budget.caseRuns:
            raise PlanError(
                "agent_budget_insufficient",
                "The Plan cannot cover the complete pinned train scope",
                status=422,
            )
        adapter = deepcopy(self.describe_adapter(model, plan.model.thinking))
        context = adapter.get("contextTokens")
        if (
            type(context) is not int
            or context < 1
            or any(
                adapter.get("enforcement", {}).get(name) != "hard"
                for name in ("requests", "tokens", "llmUsd")
            )
        ):
            raise PlanError(
                "agent_bound_unavailable", "Conservative provider admission is unavailable", status=422
            )
        quote = self.quote(plan.model.ref)
        bound_usd = quote.bound(context, plan.budget.maxOutputTokensPerRequest)
        if (
            context + plan.budget.maxOutputTokensPerRequest > plan.budget.tokens
            or bound_usd > Decimal(str(plan.budget.llmUsd))
        ):
            raise PlanError(
                "agent_budget_insufficient", "The Plan cannot reserve one bounded model request", status=422
            )
        inputs = {
            "harness": harness.manifest,
            "suite": {
                key: suite[key]
                for key in ("id", "contentHash", "scene", "sceneContentHash", "source", "runsPerCase")
            }
            | {
                "cases": [
                    {
                        "id": case["id"],
                        "statementHash": hashlib.sha256(case["statement"].encode()).hexdigest(),
                    }
                    for case in suite["cases"]
                ]
            },
            "caseRunsRequired": required,
            "model": plan.model.model_dump(),
            "credentialRef": plan.credentialRef,
            "executionProfile": plan.executionProfile,
            "adapter": adapter,
        }
        return inputs | {
            "immutablePlanInputHash": fingerprint(inputs),
            "priceQuote": {"revision": quote.revision, "rates": dict(quote.rates)},
        }

    def source_snapshot(self, plan, inputs):
        return {
            "kind": "agent-plan",
            "immutablePlanInputHash": inputs["immutablePlanInputHash"],
            "humanDeclaredLimit": plan.goalBudget.model_dump(mode="json"),
        }

    def verify(self, record):
        current = self.freeze(record.plan)
        if (
            record.frozenInputs is None
            or current["immutablePlanInputHash"] != record.frozenInputs["immutablePlanInputHash"]
        ):
            raise PlanError(
                "agent_inputs_changed",
                "Frozen Agent inputs or runtime changed; submit a new revision",
                status=409,
            )
        return current
