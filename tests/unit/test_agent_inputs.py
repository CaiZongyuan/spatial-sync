"""Freeze actual archived Harness/Suite/Scene bytes and credential names, never values."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from apps.api.app.main import create_app
from packages.agents.inputs import AgentPlanInputs
from packages.info.plans import PlanError
from packages.models import ModelConfig, PricesInput
from packages.training.schema import PLAN_DOCUMENT
from tests.unit.test_agent_plan_schema import agent_plan


@pytest.fixture
async def prepared():
    app = create_app()
    async with app.router.lifespan_context(app):
        descriptor = {
            "kind": "offline-fixture",
            "runtimeHash": "a" * 64,
            "contextTokens": 100,
            "enforcement": {name: "hard" for name in ("requests", "tokens", "llmUsd")},
        }
        service = AgentPlanInputs(
            app.state.manager, app.state.agents, lambda model, thinking: deepcopy(descriptor)
        )
        service.resources.add(
            ModelConfig(provider="anthropic", modelId="fixture-model", credentialName="llm/fixture")
        )
        service.vault.set("llm/fixture", "FAKE_ONLY_CREDENTIAL_VALUE_44")
        service.resources.put_prices(
            PricesInput(
                models=[
                    {
                        "provider": "anthropic",
                        "modelId": "fixture-model",
                        "inputUsdPerMillion": 1.0,
                        "outputUsdPerMillion": 2.0,
                        "cacheReadUsdPerMillion": 0.1,
                        "cacheWriteUsdPerMillion": 1.25,
                    }
                ]
            ),
            {"kind": "user", "name": "fixture-price-editor"},
        )
        yield service, PLAN_DOCUMENT.validate_python(agent_plan()), descriptor, app


async def test_frozen_inputs_use_complete_train_scope_exact_archives_and_no_credential_value(
    prepared, monkeypatch
):
    service, plan, _, _ = prepared

    def no_secret_resolution(*args):
        raise AssertionError("Plan submission may check credential names only")

    monkeypatch.setattr(service.vault, "adapter_environment", no_secret_resolution)
    frozen = service.freeze(plan)
    assert len(frozen["suite"]["cases"]) == 10 and frozen["caseRunsRequired"] == 30
    assert frozen["harness"]["harnessVersion"] == 1
    assert all(len(case["statementHash"]) == 64 for case in frozen["suite"]["cases"])
    assert "FAKE_ONLY_CREDENTIAL_VALUE_44" not in json.dumps(frozen)
    assert "private/" not in json.dumps(frozen) and '"grading"' not in json.dumps(frozen)
    assert service.source_snapshot(plan, frozen) == {
        "kind": "agent-plan",
        "immutablePlanInputHash": frozen["immutablePlanInputHash"],
        "humanDeclaredLimit": {"caseRuns": 90, "llmUsd": 6.0, "days": 7.0},
    }
    # Credential rotation under the same name does not become a new immutable input.
    service.vault.set("llm/fixture", "FAKE_ROTATED_VALUE_44")
    assert service.freeze(plan) == frozen


async def test_new_prices_do_not_reprice_a_stored_quote_or_mutate_immutable_input_identity(prepared):
    service, plan, _, _ = prepared
    frozen = service.freeze(plan)
    old = deepcopy(frozen)
    table = service.resources.prices()
    table["models"][0]["cacheWriteUsdPerMillion"] = 2.5
    service.resources.put_prices(
        PricesInput(revision=table["revision"], models=table["models"]),
        {"kind": "user", "name": "fixture-price-editor"},
    )
    fresh = service.verify(SimpleNamespace(plan=plan, frozenInputs=frozen))
    assert frozen == old and fresh["immutablePlanInputHash"] == old["immutablePlanInputHash"]
    assert fresh["priceQuote"]["revision"] == old["priceQuote"]["revision"] + 1
    assert fresh["priceQuote"]["rates"]["cache_creation_input_tokens"] == 2.5


async def test_changed_runtime_or_unknown_bound_cannot_reuse_approved_inputs(prepared):
    service, plan, descriptor, _ = prepared
    frozen = service.freeze(plan)
    descriptor["runtimeHash"] = "b" * 64
    with pytest.raises(PlanError, match="Frozen Agent inputs"):
        service.verify(SimpleNamespace(plan=plan, frozenInputs=frozen))
    descriptor["contextTokens"] = None
    with pytest.raises(PlanError, match="Conservative provider admission"):
        service.freeze(plan)


async def test_missing_cache_write_price_or_incomplete_case_envelope_never_means_free(prepared):
    service, plan, _, _ = prepared
    table = service.resources.prices()
    table["models"][0]["cacheWriteUsdPerMillion"] = None
    service.resources.put_prices(
        PricesInput(revision=table["revision"], models=table["models"]),
        {"kind": "user", "name": "fixture-price-editor"},
    )
    with pytest.raises(PlanError, match="complete known price"):
        service.freeze(plan)
    plan.budget.caseRuns = 29
    with pytest.raises(PlanError, match="complete pinned train scope"):
        service.freeze(plan)
