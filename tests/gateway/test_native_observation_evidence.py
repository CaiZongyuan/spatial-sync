"""Trusted verification is derived and reachable; numerical/native-looking caller labels cannot enable it."""

import pytest
from fastapi.testclient import TestClient
from onnx import TensorProto, helper

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.info.plans import PlanError
from packages.policies.qualification import NativeProfiles
from tests.fixtures.native_policy_evidence import native_inputs
from tests.gateway.test_training_jobs import TOKEN


def initialized_export(defect):
    weights = [
        helper.make_tensor("mean", TensorProto.FLOAT, [1, 1], [0.0]),
        helper.make_tensor("divisor", TensorProto.FLOAT, [1, 1], [1.02 if defect == "divisor" else 1.01]),
        helper.make_tensor("weight", TensorProto.FLOAT, [1, 1], [1.0]),
        helper.make_tensor("bias", TensorProto.FLOAT, [1], [0.0]),
    ]
    nodes = [
        helper.make_node("Sub", ["observations", "mean"], ["centered"]),
        helper.make_node("Div", ["centered", "divisor"], ["normalized"]),
        helper.make_node("Gemm", ["normalized", "weight", "bias"], ["projected"]),
        helper.make_node("Elu", ["projected"], ["activated"]),
        helper.make_node("Gemm", ["activated", "weight", "bias"], ["hidden"]),
        helper.make_node("Elu", ["hidden"], ["activated_again"]),
        helper.make_node("Gemm", ["activated_again", "weight", "bias"], ["actions"]),
    ]
    if defect == "constant":
        value = helper.make_tensor("constant", TensorProto.FLOAT, [1, 1], [0.25])
        nodes = [helper.make_node("Constant", [], ["actions"], value=value)]
        weights = []
    if defect == "unused":
        nodes[2].input[0] = "observations"
    graph = helper.make_graph(
        nodes, "initialized-export-fixture",
        [helper.make_tensor_value_info("observations", TensorProto.FLOAT, [1, 1])],
        [helper.make_tensor_value_info("actions", TensorProto.FLOAT, [1, 1])],
        weights,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)], ir_version=10)
    return model.SerializeToString()


@pytest.mark.parametrize(
    "defect", ["constant", "divisor", "unused", "bool_mean", "string_std", "huge_std", None],
)
def test_verify_source_checks_initialized_onnx_and_typed_statistics(tmp_path, monkeypatch, defect):
    registry = NativeProfiles([], allow_controlled=True)
    initialization = {
        "schema": "lw.reference-actor-init/1", "sha256": "a" * 64, "taskContentHash": "b" * 64,
        "referenceMotionSha256": "c" * 64, "observationSha256": "d" * 64, "initialStd": .1,
        "shift": 8.5, "fitActionAbsMax": .5, "normalization": {"mean": [0.0], "variance": [1.0], "count": 3},
    }
    state = {"_mean": [[0.0]], "_std": [[1.0]], "_var": [[1.0]], "count": 3}
    if defect == "bool_mean":
        state["_mean"] = [[False]]
    if defect == "string_std":
        state["_std"] = [["1.0"]]
    if defect == "huge_std":
        state["_std"] = [[10**400]]
    with TestClient(create_app(Settings(node_token=TOKEN), observation_profiles=registry)) as client:
        _, route = native_inputs(
            client, tmp_path, monkeypatch, registry, initialization=initialization,
            normalizer_state=state, model=initialized_export(defect),
        )
        check = client.post(route, json={"artifact": "observations/native-samples.json"})
        assert check.status_code == 201, check.text
        assert check.json()["result"]["numericalPass"]
        verified = client.post(f"{route}/{check.json()['id']}/verify-source",
                               json={"artifact": "observations/native-capture.json"})
        if defect:
            assert verified.status_code == 422, verified.text
            assert verified.json()["detail"]["code"] == "observation_evidence_invalid"
        else:
            assert verified.status_code == 200, verified.text
            assert verified.json()["sourceVerification"]["qualified"]


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
