import shutil

import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.library.common import BUILTIN_ROOT
from packages.training.inputs import pack_package
from tests.fixtures.training_task import audit_task, task_directory


def test_declarative_task_publishes_data_and_host_audit_gates_python(tmp_path, monkeypatch):
    monkeypatch.delenv("LW_TASK_AUDIT_FILE")
    data = tmp_path / "data"
    data.mkdir()
    reference = BUILTIN_ROOT / "tasks/microduck-stand-up/1/package"
    spec = yaml.safe_load((reference / "obs_spec.yaml").read_bytes())
    spec["schema"] = "lw.obs-spec/2"
    (data / "obs_spec.yaml").write_text(yaml.safe_dump(spec))
    task = yaml.safe_load((reference / "task.yaml").read_bytes())
    task.update(
        name="layer-a-stand",
        declarative={
            "baseBody": "/World/Lab/Microduck/trunk_base",
            "episodeSeconds": 3.0,
            "rewards": [{"kind": "upright", "weight": 1.0}],
            "terminations": [{"kind": "fall_height", "threshold": 0.025}],
        },
    )
    (data / "task.yaml").write_text(yaml.safe_dump(task))
    python = task_directory(tmp_path / "legacy", "reviewed-probe")
    with TestClient(create_app()) as http:

        def publish(path):
            return http.post(
                "/api/v1/tasks", content=pack_package(path), headers={"Content-Type": "application/x-tar"}
            )

        first = publish(data)
        assert first.status_code == 201, first.text
        assert first.json()["task"]["declarative"]["schema"] == "lw.mjlab-task/1"
        assert publish(data).json()["contentHash"] == first.json()["contentHash"]
        shutil.copyfile(python / "probe.py", data / "probe.py")
        assert publish(data).status_code == 422
        assert http.get("/api/v1/tasks/layer-a-stand/1").json()["contentHash"] == first.json()["contentHash"]
        refused = publish(python)
        assert refused.status_code == 403 and refused.json()["detail"]["code"] == "task_audit_required"
        audit = tmp_path / "audits.json"
        monkeypatch.setenv("LW_TASK_AUDIT_FILE", str(audit))
        missing = publish(python)
        assert missing.status_code == 403 and missing.json()["detail"]["code"] == "task_audit_required"
        assert not audit.exists()
        audit.write_text("{")
        malformed = publish(python)
        assert malformed.status_code == 409 and malformed.json()["detail"]["code"] == "task_audit_invalid"
        audit.unlink()
        audit_task(python)
        admitted = publish(python)
        assert admitted.status_code == 201, admitted.text
        (python / "probe.py").write_text("raise RuntimeError('changed reviewed bytes')")
        assert publish(python).status_code == 403
        assert (
            http.get("/api/v1/tasks/reviewed-probe/1").json()["contentHash"] == admitted.json()["contentHash"]
        )


def test_managed_dispatch_selects_compiler_profile_and_rejects_forged_witnesses(tmp_path):
    import hashlib
    import io
    import json
    from copy import deepcopy

    import onnx

    from apps.api.app.settings import Settings
    from apps.node.mjlab_launcher import compiler_hash
    from apps.node.observation_capture import canonical_hash
    from packages.library.archive import extract_package
    from packages.training.runtime import PINS, runtime_profile_key, training_runtime
    from tests.fixtures.policy import artifact_archive, model_bytes, observation_spec
    from tests.fixtures.training_task import training_plan
    from tests.gateway.test_training_jobs import HEARTBEAT, NODE, TOKEN, approve

    data = tmp_path / "managed"
    data.mkdir()
    prototype = BUILTIN_ROOT / "tasks/microduck-stand-up/1/package/task.yaml"
    task = yaml.safe_load(prototype.read_bytes())
    task.update(
        name="managed-witness",
        declarative={
            "baseBody": task["robot"] + "/trunk_base",
            "episodeSeconds": 3.0,
            "rewards": [{"kind": "upright", "weight": 1.0}],
        },
    )
    spec = observation_spec()
    joint = task["robot"] + "/joints/left_hip_yaw"
    spec["schema"] = "lw.obs-spec/2"
    spec["observations"][0]["keys"] = spec["action"]["jointKeys"] = [joint]
    spec["action"]["space"] = task["control"]["action"] = "joint_position_delta"
    (data / "task.yaml").write_text(yaml.safe_dump(task))
    (data / "obs_spec.yaml").write_text(yaml.safe_dump(spec))
    with TestClient(create_app(Settings(node_token=TOKEN))) as http:
        response = http.post(
            "/api/v1/tasks", content=pack_package(data), headers={"Content-Type": "application/x-tar"}
        )
        assert response.status_code == 201, response.text
        task_id, task_hash = (response.json()[key] for key in ("id", "contentHash"))
        response = http.post(
            "/api/v1/plans",
            json=training_plan(
                task_id,
                algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"},
                hyperparameters={"num_envs": 1, "max_iterations": 10, "device": "cpu"},
                evaluation={"scene": task["scene"], "episodes": 1, "threshold": 1},
            ),
        )
        assert response.status_code == 201, response.text
        plan = response.json()
        approve(http, plan["approvalId"])
        versions = PINS["mjlab"] | {"rsl_rl": "5.0.1"}
        heartbeat = {
            "instanceId": "worker-one",
            "hardware": "node-hardware/2:fixture",
            "cpuAvailable": True,
            "gpus": [],
            "trainerVersions": versions,
            "compilerHash": compiler_hash(),
            "trainingEntries": {"mjlab": "mjlab:managed"},
        }
        identity = training_runtime(heartbeat | {"id": "local"}, {"plan": plan}, "mjlab")
        key = runtime_profile_key(plan, task_hash, heartbeat, {"id": "cpu"}, identity)
        heartbeat["profiles"] = [
            {"key": key, "iterationSeconds": 1, "samples": 3, "measuredAt": 50, "evidence": "fixture"}
        ]
        assert http.post(HEARTBEAT, headers=NODE, json=heartbeat).status_code == 200
        response = http.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42})
        assert response.status_code == 202, response.text
        job = response.json()
        route = f"/api/v1/jobs/{job['jobId']}"
        headers = NODE | {"X-Lease-Id": job["leaseId"]}
        inputs = http.get(route + "/inputs", headers=headers)
        assert inputs.status_code == 200, inputs.text
        assert inputs.json()["runtimeIdentity"] == identity
        profile = inputs.json()["nativeObservationProfile"]
        assert profile["compilerHash"] == compiler_hash() and profile["taskContentHash"] == task_hash
        assert profile["stateProvider"]["module"] == "lw_declarative_mjlab"
        assert profile["inputWidth"] == 1
        # Authenticated fixture bytes intentionally model a dishonest sample.
        output = tmp_path / "job-artifacts"
        output.mkdir()
        trained = onnx.load_model_from_string(model_bytes())
        trained.graph.input[0].name = trained.graph.node[0].input[0] = "obs"
        extract_package(io.BytesIO(artifact_archive(job, model=trained.SerializeToString())), output)
        runtime = json.loads((output / "versions.json").read_bytes())
        runtime.update(versions=versions, runtimeIdentity=identity, hardware={"device": "cpu", "gpu": None})
        (output / "versions.json").write_text(json.dumps(runtime))
        weights = json.loads((output / "export.json").read_bytes())
        source = {
            key: job[key] for key in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed")
        }
        source["attemptId"] = (
            "attempt-" + hashlib.sha256(f"{job['jobId']}:{job['leaseId']}".encode()).hexdigest()
        )
        source["versions"] = runtime
        from packages.policies.native_evidence import NativeProfile

        capture = {
            "producer": "native-onnx-input/1",
            "profile": profile["id"],
            "profileSha256": canonical_hash(profile),
            "runtime": NativeProfile.model_validate(profile).runtime(),
            "compilerHash": compiler_hash(),
            "capturedAt": "2026-11-20T00:00:00Z",
        }
        envelope = {
            "schema": "lw.policy-native-observation-samples/2",
            "source": source,
            "capture": capture,
            "policy": {
                "checkpointSha256": weights["checkpointSha256"],
                "onnxSha256": weights["policySha256"],
                "observationSha256": hashlib.sha256((data / "obs_spec.yaml").read_bytes()).hexdigest(),
            },
            "samples": [],
        }
        witnesses = []
        for index in range(1, 257):
            source_id = f"env-0/epoch-0/step-{index * 10}"
            # Vary previous_action, leaving every physical state identical.
            state = {
                "joints": {joint: {"q": 0.0, "qd": 0.0}},
                "bodies": {
                    task["declarative"]["baseBody"]: {"p": [0.0, 0.0, 0.0], "q": [0.0, 0.0, 0.0, 1.0]}
                },
                "sites": {},
            }
            sample = {"state": state, "previousAction": [index / 256], "references": {}}
            witnesses.append(
                {
                    "sourceStateId": source_id,
                    "environment": 0,
                    "epoch": 0,
                    "physicsStep": index * 10,
                    "stateSha256": canonical_hash(sample),
                    "nativeInputSha256": canonical_hash([0.0]),
                }
            )
            envelope["samples"].append(sample | {"sourceStateId": source_id, "trainingObservation": [0.0]})
        filename = output / "native-samples.json"
        filename.write_text(json.dumps(envelope))
        receipt = {
            "schema": "lw.policy-native-observation-capture/2",
            "source": source,
            "capture": capture,
            "policy": envelope["policy"],
            "samplesArtifact": filename.name,
            "samplesSha256": hashlib.sha256(filename.read_bytes()).hexdigest(),
            "sampleCount": 256,
            "witnesses": witnesses,
            "normalizerStateSha256": canonical_hash({}),
            "normalizerState": {},
            "afterLearn": True,
            "iteration": weights["iteration"],
        }
        (output / "native-capture.json").write_text(json.dumps(receipt))
        forged = deepcopy(receipt)
        forged["capture"]["compilerHash"] = "0" * 64
        (output / "forged.json").write_text(json.dumps(forged))
        positive = deepcopy(envelope)
        complete = deepcopy(receipt)
        for index, sample in enumerate(positive["samples"]):
            q = (index + 1) / 2560
            sample["state"]["joints"][joint]["q"] = q
            sample["trainingObservation"] = [q * 2.0]
            complete["witnesses"][index].update(
                stateSha256=canonical_hash(
                    {key: sample[key] for key in ("state", "previousAction", "references")}
                ),
                nativeInputSha256=canonical_hash(sample["trainingObservation"]),
            )
        complete["samplesArtifact"] = "distinct-samples.json"
        distinct = output / complete["samplesArtifact"]
        distinct.write_text(json.dumps(positive))
        complete["samplesSha256"] = hashlib.sha256(distinct.read_bytes()).hexdigest()
        (output / "distinct-capture.json").write_text(json.dumps(complete))
        artifacts = http.post(route + "/artifacts", content=pack_package(output), headers=headers)
        assert artifacts.status_code == 200, artifacts.text
        done = {
            "jobId": job["jobId"],
            "leaseId": job["leaseId"],
            "status": "succeeded",
            "elapsedSeconds": 1.0,
        }
        assert http.post(HEARTBEAT, headers=NODE, json=heartbeat | {"reports": [done]}).status_code == 200
        response = http.post(
            "/api/v1/policies", json={"checkpoint": job["jobId"] + "/" + weights["checkpoint"]}
        )
        assert response.status_code == 201, response.text
        check_path = "/api/v1/policies/managed-witness/1/observation-checks"
        check = http.post(check_path, json={"artifact": filename.name})
        assert check.status_code == 201, check.text
        assert check.json()["result"]["numericalPass"]
        for artifact in ("native-capture.json", "forged.json"):
            refused = http.post(
                f"{check_path}/{check.json()['id']}/verify-source", json={"artifact": artifact}
            )
            assert refused.status_code == 422, refused.text

        check = http.post(check_path, json={"artifact": distinct.name})
        assert check.status_code == 201, check.text
        verified = http.post(
            f"{check_path}/{check.json()['id']}/verify-source", json={"artifact": "distinct-capture.json"}
        )
        assert verified.status_code == 200 and verified.json()["sourceVerification"]["qualified"], (
            verified.text
        )
