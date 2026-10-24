"""Trusted verification is derived and reachable; numerical/native-looking caller labels cannot enable it."""

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.info.plans import PlanError
from packages.policies.qualification import NativeProfiles
from tests.fixtures.native_policy_evidence import native_inputs
from tests.gateway.test_training_jobs import TOKEN


def test_production_registry_does_not_trust_controlled_native_looking_artifacts(tmp_path, monkeypatch):
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        policy, route = native_inputs(client, tmp_path, monkeypatch)
        check = client.post(route, json={"artifact": "observations/native-samples.json"})
        assert check.status_code == 201, check.text
        check = check.json()
        assert check["result"]["numericalPass"] and check["qualified"] is False
        source = client.post(
            route + "/" + check["id"] + "/verify-source",
            json={"artifact": "observations/native-capture.json"},
        )
        assert (
            source.status_code == 409 and source.json()["detail"]["code"] == "observation_source_unverified"
        )
        assert policy["qualified"] is False
        with pytest.raises(PlanError):
            app.state.policy_evidence.qualification(policy["id"], check["id"])


def test_controlled_trusted_registry_exercises_derived_qualification_retry_restart_and_bad_witness(
    tmp_path,
    monkeypatch,
):
    registry = NativeProfiles([], allow_controlled=True)
    settings = Settings(node_token=TOKEN)
    app = create_app(settings, observation_profiles=registry)
    with TestClient(app) as client:
        policy, route = native_inputs(client, tmp_path, monkeypatch, registry)
        check = client.post(route, json={"artifact": "observations/native-samples.json"}).json()
        path = route + "/" + check["id"] + "/verify-source"
        wrong = client.post(path, json={"artifact": "observations/wrong-native.json"})
        assert wrong.status_code == 422, wrong.text
        verified = client.post(path, json={"artifact": "observations/native-capture.json"})
        assert verified.status_code == 200, verified.text
        source = verified.json()["sourceVerification"]
        assert source["status"] == "native_input_verified" and source["evidenceMode"] == "controlled"
        assert source["qualified"] and verified.json()["qualified"] is False
        assert client.post(path, json={"artifact": "observations/native-capture.json"}).json()["idempotent"]
        facts = app.state.policy_evidence.qualification(policy["id"], check["id"])
        assert facts["qualified"] and facts["samples"] == 256 and facts["evidenceMode"] == "controlled"
        read = client.get("/api/v1/policies/training-probe/1").json()
        assert read["contentHash"] == policy["contentHash"] and read["qualified"] is False
    with TestClient(create_app(settings, observation_profiles=registry)) as client:
        facts = client.app.state.policy_evidence.qualification(policy["id"], check["id"])
        assert facts["qualified"] and facts["evidenceMode"] == "controlled"
