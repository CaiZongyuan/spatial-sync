"""Trusted verification is derived and reachable; numerical/native-looking caller labels cannot enable it."""

import io
import json
import struct
import zipfile

import onnx
import pytest
from fastapi.testclient import TestClient
from onnx import TensorProto, external_data_helper, helper

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.info.plans import PlanError
from packages.policies import evidence, qualification
from packages.policies.qualification import INITIALIZATION_R3_VERIFIER, NativeProfiles
from tests.fixtures.native_policy_evidence import native_inputs
from tests.gateway.test_training_jobs import TOKEN


def initialized_export(defect):
    weights = [
        helper.make_tensor("mean", TensorProto.FLOAT, [1, 1], [0.0]),
        helper.make_tensor("divisor", TensorProto.FLOAT, [1, 1], [1.02 if defect == "divisor" else 1.01]),
    ]
    for index, (inputs, outputs) in enumerate(((1, 256), (256, 128), (128, 1))):
        shape = [outputs, inputs]
        weights.extend((
            helper.make_tensor(f"weight{index}", TensorProto.FLOAT, shape, [0.5] * (inputs * outputs)),
            helper.make_tensor(f"bias{index}", TensorProto.FLOAT, [outputs], [0.0] * outputs),
        ))
    gemm = {"alpha": 1.0, "beta": 1.0, "transB": 1}
    nodes = [
        helper.make_node("Sub", ["observations", "mean"], ["centered"]),
        helper.make_node("Div", ["centered", "divisor"], ["normalized"]),
        helper.make_node("Gemm", ["normalized", "weight0", "bias0"], ["projected"],
                         **(gemm | ({"alpha": 0.0} if defect == "gemm_alpha" else {}))),
        helper.make_node("Elu", ["projected"], ["activated"], alpha=2.0 if defect == "elu_alpha" else 1.0),
        helper.make_node("Gemm", ["activated", "weight1", "bias1"], ["hidden"],
                         **(gemm | ({"beta": 0.0} if defect == "gemm_beta" else {}))),
        helper.make_node("Elu", ["hidden"], ["activated_again"], alpha=1.0),
        helper.make_node("Gemm", ["activated_again", "weight2", "bias2"], ["actions"], **gemm),
    ]
    if defect == "constant":
        value = helper.make_tensor("constant", TensorProto.FLOAT, [1, 1], [0.25])
        nodes = [helper.make_node("Constant", [], ["actions"], value=value)]
        weights = []
    if defect == "unused":
        nodes[2].input[0] = "observations"
    if defect == "missing_bias":
        del nodes[2].input[-1]
    if defect == "gemm_transpose":
        nodes[2].attribute.remove(next(attr for attr in nodes[2].attribute if attr.name == "transB"))
        weights[2].dims[:] = [1, 256]  # Valid ONNX, using a different exporter layout.
    if defect == "nonfinite_weight":
        weights[2].float_data[0] = float("nan")
    if defect == "operator_defaults":
        del nodes[3].attribute[:]
        nodes[2].attribute.remove(next(attr for attr in nodes[2].attribute if attr.name == "alpha"))
        nodes[2].attribute.append(helper.make_attribute("transA", 0))
    if defect in ("constant_alias", "unused_alias", "external_alias"):
        source = "weight0" if defect == "external_alias" else "mean"
        nodes.insert(0, helper.make_node("Identity", [source], ["first_alias"]))
        nodes.insert(1, helper.make_node("Identity", ["first_alias"], ["second_alias"]))
        if defect == "constant_alias":
            nodes[2].input[1] = "second_alias"
        if defect == "external_alias":
            nodes[4].input[1] = "second_alias"
    if defect == "input_alias":
        nodes.insert(0, helper.make_node("Identity", ["observations"], ["input_alias"]))
        nodes[1].input[0] = "input_alias"
    if defect in ("external_weight", "external_alias"):
        tensor = weights[2]
        tensor.ClearField("float_data")
        tensor.raw_data = b"placeholder"
        external_data_helper.set_external_data(tensor, location="weights.bin", offset=0, length=1024)
        tensor.ClearField("raw_data")
    graph = helper.make_graph(
        nodes, "initialized-export-fixture",
        [helper.make_tensor_value_info("observations", TensorProto.FLOAT, [1, 1])],
        [helper.make_tensor_value_info("actions", TensorProto.FLOAT, [1, 1])],
        weights,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)], ir_version=10)
    return model.SerializeToString()


@pytest.mark.parametrize(
    "defect", [
        "constant", "divisor", "unused", "gemm_alpha", "gemm_beta", "gemm_transpose", "elu_alpha",
        "missing_bias", "nonfinite_weight", "bool_mean", "string_std", "huge_std", "operator_defaults", None,
        "constant_alias", "unused_alias", "input_alias", "external_weight", "external_alias",
    ],
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
    external = {"weights.bin": struct.pack("<256f", float("nan"), *([0.5] * 255))}
    with TestClient(create_app(Settings(node_token=TOKEN), observation_profiles=registry)) as client:
        _, route = native_inputs(
            client, tmp_path, monkeypatch, registry, initialization=initialization,
            normalizer_state=state, model=initialized_export(defect),
            external=external if defect in ("external_weight", "external_alias") else None,
        )
        check = client.post(route, json={"artifact": "observations/native-samples.json"})
        assert check.status_code == 201, check.text
        assert check.json()["result"]["numericalPass"]
        verified = client.post(f"{route}/{check.json()['id']}/verify-source",
                               json={"artifact": "observations/native-capture.json"})
        if defect not in (None, "operator_defaults", "constant_alias"):
            assert verified.status_code == 422, verified.text
            assert verified.json()["detail"]["code"] == "observation_evidence_invalid"
        else:
            assert verified.status_code == 200, verified.text
            assert verified.json()["sourceVerification"]["qualified"]


def test_retained_external_initialized_policy_downloads_without_qualification(tmp_path, monkeypatch):
    registry = NativeProfiles([], allow_controlled=True)
    initialization = {
        "schema": "lw.reference-actor-init/1", "sha256": "a" * 64, "taskContentHash": "b" * 64,
        "referenceMotionSha256": "c" * 64, "observationSha256": "d" * 64, "initialStd": .1,
        "shift": 8.5, "fitActionAbsMax": .5, "normalization": {"mean": [0.0], "variance": [1.0], "count": 3},
    }
    model = initialized_export("external_weight")
    weights = struct.pack("<256f", *([0.5] * 256))
    with TestClient(create_app(Settings(node_token=TOKEN), observation_profiles=registry)) as client:
        policy, route = native_inputs(
            client, tmp_path, monkeypatch, registry, initialization=initialization,
            normalizer_state={"_mean": [[0.0]], "_std": [[1.0]], "_var": [[1.0]], "count": 3},
            model=model, external={"weights.bin": weights},
        )
        check = client.post(route, json={"artifact": "observations/native-samples.json"})
        assert check.status_code == 201, check.text
        verify = f"{route}/{check.json()['id']}/verify-source"
        capture = {"artifact": "observations/native-capture.json"}
        retained = evidence.implementation_identity()
        retained["files"]["packages/policies/qualification.py"] = INITIALIZATION_R3_VERIFIER

        def retained_r3_graph(graph_model, normalization, widths):
            # This finite, signed external tensor was allowed by R3's graph verifier.
            graph_model.graph.initializer[2].CopyFrom(
                onnx.load_model_from_string(initialized_export(None)).graph.initializer[2]
            )
            verify_graph(graph_model, normalization, widths)

        verify_graph = qualification._verify_exported_normalizer
        with monkeypatch.context() as old:
            old.setattr(evidence, "implementation_identity", lambda: retained)
            old.setattr(qualification, "_verify_exported_normalizer", retained_r3_graph)
            previous = client.post(verify, json=capture)
            assert previous.status_code == 200, previous.text
            assert previous.json()["sourceVerification"]["qualified"]

        fresh = client.post(verify, json=capture)
        assert fresh.status_code == 422 and fresh.json()["detail"]["code"] == "observation_evidence_invalid"
        exported = client.get("/api/v1/policies/training-probe/1/export")
        assert exported.status_code == 200, exported.text
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["qualification"] == {"status": "unverified"}
            assert manifest["normalization"] == "as-frozen-in-model"
            assert manifest["policyContentHash"] == policy["contentHash"]
            assert archive.read(manifest["model"]["path"]) == model
            assert archive.read("model/weights.bin") == weights

        def invalid_read(*args):
            raise ValueError("unexpected graph reader failure")

        monkeypatch.setattr(qualification, "_verify_exported_normalizer", invalid_read)
        broken = client.get("/api/v1/policies/training-probe/1/export")
        assert broken.status_code == 409 and broken.json()["detail"]["code"] == "policy_export_unavailable"


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
