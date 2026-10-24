"""Trusted common runtime facts separate actual installations without including candidate evidence."""

from types import SimpleNamespace

import pytest

from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.info.evaluation_identity import bind_source_identity
from packages.protocol.internal import SessionOptions


def bind(facts):
    session = SimpleNamespace(
        source=SyntheticSource("owned-runtime-identity"),
        manifest=SimpleNamespace(source=SimpleNamespace(kind="synthetic", newtonVersion=None, solver=None)),
        options=SessionOptions(),
    )
    frozen = {"candidateIdentity": {"kind": "agent-harness"}, "environmentInputs": {}, "judgeInputs": {}}
    return bind_source_identity(frozen, session, execution_facts=facts)


def test_actual_runtime_version_changes_environment_and_known_absence_is_retained():
    one, actual, reason = bind({"available": True, "runtimeVersion": "1.0", "availableProviders": ["CPU"]})
    two, _, _ = bind({"available": True, "runtimeVersion": "2.0", "availableProviders": ["CPU"]})
    assert reason is None and one["environment"] != two["environment"] and one["judge"] == two["judge"]
    assert actual["observationRuntime"] == {
        "available": True,
        "runtimeVersion": "1.0",
        "availableProviders": ["CPU"],
    }
    missing, actual, reason = bind({"available": False, "runtimeVersion": None, "availableProviders": []})
    assert reason is None and missing is not None and actual["observationRuntime"]["available"] is False


@pytest.mark.parametrize(
    "facts",
    [
        {},
        {"runtimeVersion": None, "availableProviders": []},
        {
            "available": True,
            "runtimeVersion": "1.0",
            "availableProviders": ["CPU"],
            "weightsSha": "candidate",
        },
    ],
)
def test_unknown_or_candidate_mixed_runtime_facts_never_claim_equality(facts):
    identity, _, reason = bind(facts)
    assert identity is None and reason == "execution_runtime_unknown"
