"""Owned large-scene validation can extend wall time without changing physics."""

import pytest

from packages.library.validation import StructuralValidator
from tools.cell_passage import GatewayClient, _OwnedValidationClient


def test_default_and_explicit_physics_budgets_are_preserved(tmp_path, monkeypatch):
    monkeypatch.delenv("SPATIAL_LIBRARY_PHYSICS_TIMEOUT_SECONDS", raising=False)
    assert StructuralValidator(tmp_path).physics_timeout == 180
    monkeypatch.setenv("SPATIAL_LIBRARY_PHYSICS_TIMEOUT_SECONDS", "600")
    assert StructuralValidator(tmp_path).physics_timeout == 600
    assert StructuralValidator(tmp_path, physics_timeout=12).physics_timeout == 12


@pytest.mark.parametrize("value", ["nan", "inf", "0", "1801", "unknown"])
def test_owned_physics_budget_is_finite_and_bounded(tmp_path, monkeypatch, value):
    monkeypatch.setenv("SPATIAL_LIBRARY_PHYSICS_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError):
        StructuralValidator(tmp_path)


@pytest.mark.asyncio
async def test_owned_client_extends_only_validation_http_wall_time(monkeypatch):
    calls = []

    async def request(self, method, path, **kwargs):
        calls.append((path, kwargs.get("timeout")))
        return {}

    monkeypatch.setattr(GatewayClient, "request", request)
    async with _OwnedValidationClient(url="http://owned", validation_budget=600) as client:
        await client.request("POST", "/api/v1/library/imports", timeout=240)
        await client.request("POST", "/api/v1/library/scenes/demo/1/validate", timeout=120)
        await client.request("POST", "/api/v1/sessions/session/actions", timeout=.5)
    assert calls == [("/api/v1/library/imports", 630),
                     ("/api/v1/library/scenes/demo/1/validate", 630),
                     ("/api/v1/sessions/session/actions", .5)]
