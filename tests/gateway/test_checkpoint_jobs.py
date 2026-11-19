"""GPU-free public journey through portable checkpoints and two lease epochs."""

import hashlib
import io
import json
import struct
import tarfile
from copy import deepcopy

import onnx
import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.node.mjlab_launcher import compiler_hash
from apps.node.observation_capture import canonical_hash
from packages.library.common import BUILTIN_ROOT
from packages.policies.native_evidence import NativeProfile
from packages.training.inputs import pack_package
from tests.fixtures.policy import observation_spec
from tests.fixtures.training_task import task_archive, training_plan
from tests.gateway.test_mjlab_node_jobs import _build_mjlab_task_package
from tests.gateway.test_remote_training_nodes import (
    COMPILER,
    MJLAB_VERSIONS,
    OPERATOR,
    beat,
    node_profile_key,
    register,
    remote_settings,
)


def checkpoint_archive(dispatch, *, corrupt=False):
    metadata = dispatch["checkpointIdentity"] | {
        "iteration": 4,
        "environmentSteps": 16,
        "learningRate": 0.001,
        "state": {
            "actor_state_dict": {"weight": {"tensor": "t0"}},
            "critic_state_dict": {"weight": {"tensor": "t1"}},
            "optimizer_state_dict": {"state": {}, "param_groups": []},
        },
    }
    header = (
        b'{"t0":{"dtype":"F32","shape":[1],"data_offsets":[0,4]},'
        b'"t1":{"dtype":"F32","shape":[1],"data_offsets":[4,8]}}'
    )
    tensors = struct.pack("<Q", len(header)) + header + struct.pack("<ff", 2, -2)
    metadata["tensorSha256"] = hashlib.sha256(tensors).hexdigest()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, data in (
            ("checkpoint.json", json.dumps(metadata).encode()),
            ("checkpoint.safetensors", tensors[:-1] if corrupt else tensors),
        ):
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    return output.getvalue()


def declarative_archive(directory):
    directory.mkdir()
    task = yaml.safe_load((BUILTIN_ROOT / "tasks/microduck-stand-up/1/package/task.yaml").read_bytes())
    task.update(
        name="checkpoint-motion",
        declarative={
            "baseBody": task["robot"] + "/trunk_base",
            "episodeSeconds": 8.0,
            "rewards": [{"kind": "upright", "weight": 1.0}],
        },
    )
    spec = observation_spec()
    spec["schema"] = "lw.obs-spec/2"
    joint = task["robot"] + "/joints/left_hip_yaw"
    spec["observations"][0]["keys"] = spec["action"]["jointKeys"] = [joint]
    task["control"]["action"] = spec["action"]["space"]
    (directory / "task.yaml").write_text(yaml.safe_dump(task))
    (directory / "obs_spec.yaml").write_text(yaml.safe_dump(spec))
    return pack_package(directory), task, joint


@pytest.mark.parametrize("managed", [False, True], ids=["audited-python", "declarative-samples"])
def test_fenced_checkpoint_resume_reprices_remaining_work_and_sums_attempts(tmp_path, managed):
    settings = remote_settings()
    clock = [1000.0]

    def app():
        server = create_app(settings)
        server.state.jobs.clock = lambda: clock[0]
        return server

    runtime = {
        "trainingEntries": {"mjlab": "mjlab:managed"},
        "trainerVersions": MJLAB_VERSIONS,
        "compilerHash": compiler_hash() if managed else COMPILER,
    }
    directory = tmp_path / "task"
    if managed:
        task_bytes, task_doc, joint = declarative_archive(directory)
        observation = (directory / "obs_spec.yaml").read_bytes()
        evaluation = {"evaluation": {"scene": task_doc["scene"], "episodes": 1, "threshold": 1}}
    else:
        task_bytes = task_archive(_build_mjlab_task_package(directory))
        task_doc, joint, observation, evaluation = None, None, None, {}
    with TestClient(app()) as client:
        task = client.post(
            "/api/v1/tasks",
            headers=OPERATOR | {"Content-Type": "application/x-tar"},
            content=task_bytes,
        ).json()
        plan = client.post(
            "/api/v1/plans",
            headers=OPERATOR,
            json=training_plan(
                task["id"],
                algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"},
                budget={"gpuHours": 0.1},
                **evaluation,
            ),
        ).json()

        def approve(client, identity):
            reply = client.post(
                f"/api/v1/approvals/{identity}/decision", headers=OPERATOR, json={"decision": "approve"}
            )
            assert reply.is_success, reply.text

        def caps(node, seconds):
            gpu = {"id": f"GPU-{node}", "name": f"GPU model {node}"}
            key = node_profile_key(
                plan,
                task["contentHash"],
                runtime
                | {
                    "id": node,
                    "hardware": "fixture",
                },
                gpu,
                backend="mjlab",
            )
            return runtime | {
                "checkpointVersion": 1,
                "gpus": [gpu],
                "profiles": [
                    {
                        "key": key,
                        "iterationSeconds": seconds,
                        "samples": 2,
                        "measuredAt": 999,
                        "evidence": "fixture",
                    }
                ],
            }

        approve(client, plan["approvalId"])
        a, b = register(client, "a"), register(client, "b")
        first, second = caps("a", 20), caps("b", 50)
        reply = beat(client, a, **first)
        assert reply.is_success, reply.text
        start = client.post(
            "/api/v1/jobs",
            headers=OPERATOR,
            json={
                "planId": plan["id"],
                "seed": 42,
                "targetNodeId": "a",
            },
        )
        assert start.is_success, start.text
        job = start.json()
        url = f"/api/v1/jobs/{job['jobId']}"
        a_lease = a | {"X-Lease-Id": job["leaseId"]}
        dispatch = client.get(f"{url}/inputs", headers=a_lease).json()
        assert dispatch["schemaVersion"] == 2
        if managed:
            profile = dispatch["nativeObservationProfile"]
            assert profile["compilerHash"] == runtime["compilerHash"]
            assert profile["taskContentHash"] == task["contentHash"]
        clock[0] = 1059
        report = {
            "leaseId": job["leaseId"],
            "jobId": job["jobId"],
            "status": "running",
            "elapsedSeconds": 59,
            "iteration": 4,
            "logs": ["first"],
        }
        assert beat(client, a, **first, reports=[report]).is_success
        archive = checkpoint_archive(dispatch)
        assert (
            client.post(
                f"{url}/checkpoints", headers=a_lease, content=checkpoint_archive(dispatch, corrupt=True)
            ).status_code
            == 422
        )
        accepted = client.post(f"{url}/checkpoints", headers=a_lease, content=archive)
        assert accepted.is_success, accepted.text
        checkpoint = accepted.json()
        assert checkpoint["iteration"] == 4
        assert client.post(f"{url}/checkpoints", headers=a_lease, content=archive).json() == checkpoint
        report = report | {"iteration": 5, "logAfter": 1, "logs": []}
        assert beat(client, a, **first, reports=[report]).is_success
        clock[0] = 1120
        assert beat(client, a, **first, reports=[report]).status_code == 403
        assert client.post(f"{url}/checkpoints", headers=a_lease, content=archive).status_code == 403
        suspended = client.get(url, headers=OPERATOR).json()
        assert suspended["status"] == "suspended"
        assert suspended["attempts"][0]["gpuSeconds"] == 120
        assert suspended["attempts"][0]["endIteration"] == 5
        assert suspended["leaseId"] is None

    with TestClient(app()) as client:
        old = second.copy()
        del old["checkpointVersion"]
        assert beat(client, b, **old).json()["assignments"] == []
        incompatible = second | {"compilerHash": "b" * 64}
        assert beat(client, b, **incompatible).json()["assignments"] == []
        assert beat(client, b, **second).json()["assignments"] == []
        waiting = client.get(url, headers=OPERATOR).json()
        assert waiting["status"] == "suspended" and waiting["reason"] == "pending_approval"
        decision = client.get(f"/api/v1/approvals/{waiting['approvalId']}", headers=OPERATOR).json()
        assert decision["details"]["remainingGpuHours"] == pytest.approx(240 / 3600)
        assert decision["details"]["estimatedGpuHours"] == pytest.approx(300 / 3600)
        assert client.get(url, headers=OPERATOR).json()["approvalId"] == waiting["approvalId"]
        selected = client.post(f"{url}/resume", headers=OPERATOR, json={"targetNodeId": "b"})
        assert selected.status_code == 202
        assert selected.json()["approvalId"] == waiting["approvalId"]
        if managed:
            approve(client, waiting["approvalId"])
            destination, capabilities, node_name = b, second, "b"
        else:
            approval_url = f"/api/v1/approvals/{waiting['approvalId']}"
            assert client.post(
                f"{approval_url}/decision",
                headers=OPERATOR,
                json={"decision": "deny"},
            ).is_success
            for _ in range(2):
                retry = client.post(f"{url}/resume", headers=OPERATOR, json={"targetNodeId": "b"}).json()
                assert retry["reason"] == "approval_denied" and retry["leaseId"] is None
                assert retry["approvalId"] == waiting["approvalId"]
            assert beat(client, b, **second).json()["assignments"] == []
            same_scope = caps("b", 20)
            same_scope["profiles"][0]["measuredAt"] = 1000
            assert beat(client, b, **same_scope).json()["assignments"] == []
            denied = client.get(url, headers=OPERATOR).json()
            assert denied["estimatedWallSeconds"] == 120 and denied["reason"] == "approval_denied"
            destination, capabilities, node_name = register(client, "c"), caps("c", 20), "c"
            assert beat(client, destination, **capabilities).json()["assignments"] == []
            alternate = client.post(f"{url}/resume", headers=OPERATOR, json={"targetNodeId": "c"})
            assert alternate.status_code == 202 and alternate.json()["estimatedWallSeconds"] == 120
            assert alternate.json()["approvalId"] is None and alternate.json()["leaseId"]
            assert client.get(approval_url, headers=OPERATOR).json()["status"] == "denied"
            assert client.get(url, headers=OPERATOR).json()["leaseId"] == alternate.json()["leaseId"]
        (assignment,) = beat(client, destination, **capabilities).json()["assignments"]
        assert int(assignment["leaseId"]) > int(job["leaseId"])
        assert assignment["logAfter"] == 1
        b_lease = destination | {"X-Lease-Id": assignment["leaseId"]}
        resumed = client.get(f"{url}/inputs", headers=b_lease).json()
        restored = resumed["resumeCheckpoint"]
        if managed:
            assert resumed["nativeObservationProfile"] == profile
            assert resumed["checkpointIdentity"]["leaseId"] == b_lease["X-Lease-Id"]
        assert restored["sha256"] == checkpoint["sha256"]
        assert client.get(restored["url"], headers=b_lease).content == archive
        assert client.get(restored["url"], headers=a_lease).status_code == 403
        assert beat(client, a, **first, reports=[report]).status_code == 403
        obsolete_cursor = {
            "leaseId": assignment["leaseId"],
            "jobId": job["jobId"],
            "status": "running",
            "elapsedSeconds": 0,
            "iteration": 4,
            "logAfter": 0,
            "logs": ["first"],
        }
        assert beat(client, destination, **capabilities, reports=[obsolete_cursor]).status_code == 422
        for now, seconds, status, lines in ((1170, 50, "running", ["second"]), (1200, 80, "failed", [])):
            clock[0] = now
            report_b = report | {
                "leaseId": assignment["leaseId"],
                "status": status,
                "elapsedSeconds": seconds,
                "iteration": 6,
                "logAfter": 1 if lines else 2,
                "logs": lines,
            }
            for _ in range(2):
                response = beat(client, destination, **capabilities, reports=[report_b])
                assert response.is_success, response.text
        ended = client.get(url, headers=OPERATOR).json()
        assert ended["chargedGpuHours"] == pytest.approx(200 / 3600)
        assert [attempt["gpuSeconds"] for attempt in ended["attempts"]] == [120, 80]
        assert ended["attempts"][1]["startIteration"] == 4
        assert client.get(f"{url}/logs", headers=OPERATOR).json()["lines"] == ["first", "second"]
        costs = client.get("/api/v1/cost/breakdown?range=all&by=node", headers=OPERATOR).json()
        assert {row["key"]: row["gpuSeconds"] for row in costs["rows"]} == {"a": 120, node_name: 80}

        totals = client.get("/api/v1/cost/summary?range=all", headers=OPERATOR).json()["totals"]
        assert totals["reportedGpuSeconds"] == 139
        assert totals["estimatedGpuSeconds"] == 61

        final = client.post(
            "/api/v1/jobs",
            headers=OPERATOR,
            json={
                "kind": "checkpoint_finalize",
                "planId": plan["id"],
                "checkpoint": job["jobId"] + "/" + checkpoint["sha256"],
                "targetNodeId": node_name,
                "requestId": "finalize-reviewed-checkpoint",
            },
        )
        if not managed:
            assert final.status_code == 409, final.text
            assert final.json()["detail"]["code"] == "observation_source_unverified"
            assert client.get("/api/v1/jobs?kind=checkpoint_finalize", headers=OPERATOR).json()["jobs"] == []
            assert beat(client, destination, **capabilities).json()["assignments"] == []
            return
        assert final.status_code == 202, final.text
        final = final.json()
        final_url = f"/api/v1/jobs/{final['jobId']}"
        final_lease = b | {"X-Lease-Id": final["leaseId"]}
        final_inputs = client.get(f"{final_url}/inputs", headers=final_lease).json()
        assert final_inputs["resumeCheckpoint"]["sha256"] == checkpoint["sha256"]
        if managed:
            assert final_inputs["nativeObservationProfile"] == profile
            assert final_inputs["checkpointIdentity"]["leaseId"] == final_lease["X-Lease-Id"]
        artifact = tmp_path / "final-artifacts"
        witness_artifacts(
            final,
            archive,
            checkpoint,
            artifact,
            dispatch=final_inputs if managed else None,
            task_doc=task_doc,
            joint=joint,
            observation=observation,
        )

        receipt = artifact / "observations/native-capture.json"
        good = json.loads(receipt.read_bytes())
        short = deepcopy(good)
        short["sampleCount"] = 255
        short["witnesses"].pop()
        receipt.write_text(json.dumps(short))
        assert (
            client.post(
                f"{final_url}/artifacts", headers=final_lease, content=pack_package(artifact)
            ).status_code
            == 422
        )
        bad_source = good | {"finalizedCheckpointSha256": "f" * 64}
        receipt.write_text(json.dumps(bad_source))
        assert (
            client.post(
                f"{final_url}/artifacts", headers=final_lease, content=pack_package(artifact)
            ).status_code
            == 422
        )
        if managed:
            sample_file = artifact / "observations/native-samples.json"
            sample_bytes = sample_file.read_bytes()
            downgraded = json.loads(sample_bytes)
            downgraded["schema"] = "lw.policy-native-observation-samples/1"
            sample_file.write_text(json.dumps(downgraded))
            good_digest = hashlib.sha256(sample_file.read_bytes()).hexdigest()
            receipt.write_text(json.dumps(good | {"samplesSha256": good_digest}))
            rejected = client.post(
                f"{final_url}/artifacts", headers=final_lease, content=pack_package(artifact)
            )
            assert rejected.status_code == 422 and "source authority" in rejected.text
            sample_file.write_bytes(sample_bytes)
            wrong_generation = good | {"source": good["source"] | {"attemptId": "attempt-" + "f" * 64}}
            receipt.write_text(json.dumps(wrong_generation))
            assert (
                client.post(
                    f"{final_url}/artifacts",
                    headers=final_lease,
                    content=pack_package(artifact),
                ).status_code
                == 422
            )
        receipt.write_text(json.dumps(good))
        published = client.post(f"{final_url}/artifacts", headers=final_lease, content=pack_package(artifact))
        assert published.is_success, published.text
        final_report = {
            "jobId": final["jobId"],
            "leaseId": final["leaseId"],
            "iteration": 4,
            "status": "succeeded",
            "elapsedSeconds": 2,
        }
        assert beat(client, b, **second, reports=[final_report]).is_success
        final = client.get(final_url, headers=OPERATOR).json()
        assert final["status"] == "succeeded"
        assert [(row["startIteration"], row["endIteration"]) for row in final["attempts"]] == [(4, 4)]
        if managed:
            candidate = client.post(
                "/api/v1/policies",
                headers=OPERATOR,
                json={
                    "checkpoint": final["jobId"] + "/checkpoint.safetensors",
                },
            )
            assert candidate.status_code == 201, candidate.text
            path = "/api/v1/policies/checkpoint-motion/1/observation-checks"
            checked = client.post(path, headers=OPERATOR, json={"artifact": good["samplesArtifact"]})
            assert checked.status_code == 201, checked.text
            verified = client.post(
                f"{path}/{checked.json()['id']}/verify-source",
                headers=OPERATOR,
                json={"artifact": "observations/native-capture.json"},
            )
            assert verified.status_code == 200, verified.text
            assert verified.json()["sourceVerification"]["qualified"] is True


@pytest.mark.parametrize("num_envs", [2, 4], ids=["wrong-layout", "reviewed-layout"])
def test_retained_native_profile_must_fit_the_finalization_dispatch(num_envs):
    settings = remote_settings()
    with TestClient(create_app(settings)) as client:
        native = client.get("/api/v1/tasks/microduck-stand-up/1", headers=OPERATOR).json()
        plan_row = client.post(
            "/api/v1/plans",
            headers=OPERATOR,
            json=training_plan(
                native["id"],
                algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"},
                hyperparameters={"num_envs": num_envs, "max_iterations": 10},
                evaluation={"scene": native["task"]["scene"], "episodes": 1, "threshold": 1},
            ),
        )
        assert plan_row.status_code == 201, plan_row.text
        plan = plan_row.json()
        assert client.post(
            f"/api/v1/approvals/{plan['approvalId']}/decision",
            headers=OPERATOR,
            json={"decision": "approve"},
        ).is_success
        node = register(client, "reviewed")
        gpu = {"id": "GPU-reviewed", "name": "Fixture GPU"}
        runtime = {
            "trainingEntries": {"mjlab": "mjlab:managed"},
            "trainerVersions": MJLAB_VERSIONS,
            "compilerHash": compiler_hash(),
        }
        key = node_profile_key(
            plan,
            native["contentHash"],
            runtime | {"id": "reviewed", "hardware": "fixture"},
            gpu,
            backend="mjlab",
        )
        caps = runtime | {
            "checkpointVersion": 1,
            "gpus": [gpu],
            "profiles": [
                {
                    "key": key,
                    "iterationSeconds": 1,
                    "samples": 2,
                    "measuredAt": 999,
                    "evidence": "fixture",
                }
            ],
        }
        assert beat(client, node, **caps).is_success
        started = client.post(
            "/api/v1/jobs",
            headers=OPERATOR,
            json={"planId": plan["id"], "seed": 42},
        )
        assert started.status_code == 202, started.text
        job = started.json()
        url = f"/api/v1/jobs/{job['jobId']}"
        lease = node | {"X-Lease-Id": job["leaseId"]}
        inputs = client.get(url + "/inputs", headers=lease).json()
        accepted = client.post(url + "/checkpoints", headers=lease, content=checkpoint_archive(inputs))
        assert accepted.status_code == 201, accepted.text
        failed = {"jobId": job["jobId"], "leaseId": job["leaseId"], "status": "failed", "iteration": 4}
        assert beat(client, node, **caps, reports=[failed]).is_success
        payload = {
            "kind": "checkpoint_finalize",
            "planId": plan["id"],
            "checkpoint": job["jobId"] + "/" + accepted.json()["sha256"],
            "requestId": "reviewed-retained-finalization",
        }
        final = client.post("/api/v1/jobs", headers=OPERATOR, json=payload)
        if num_envs == 2:
            assert final.status_code == 409, final.text
            assert final.json()["detail"]["code"] == "observation_source_unverified"
            assert beat(client, node, **caps).json()["assignments"] == []
        else:
            assert final.status_code == 202, final.text
            row = final.json()
            retry = client.post("/api/v1/jobs", headers=OPERATOR, json=payload)
            assert retry.status_code == 202 and retry.json()["jobId"] == row["jobId"]
            final_inputs = client.get(
                f"/api/v1/jobs/{row['jobId']}/inputs",
                headers=node | {"X-Lease-Id": row["leaseId"]},
            ).json()
            assert final_inputs["nativeObservationProfile"] == inputs["nativeObservationProfile"]
            assert final_inputs["nativeObservationProfile"]["evidenceMode"] == "native"
            assert final_inputs["nativeObservationProfile"]["environments"] == [0, 1, 2, 3]


def witness_artifacts(
    job, archive, checkpoint, directory, *, dispatch=None, task_doc=None, joint=None, observation=None
):
    """Independently assemble an authenticated fake node's finalizer response."""
    from tests.fixtures.policy import model_bytes

    directory.mkdir()
    evidence = directory / "observations"
    evidence.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as original:
        for name in ("checkpoint.json", "checkpoint.safetensors"):
            (directory / name).write_bytes(original.extractfile(name).read())
    model_file = directory / "policy.onnx"
    model = onnx.load_model_from_string(model_bytes())
    if dispatch:
        model.graph.input[0].name = model.graph.node[0].input[0] = "obs"
    model_file.write_bytes(model.SerializeToString())
    hash_onnx = hashlib.sha256(model_file.read_bytes()).hexdigest()
    export = {
        "schemaVersion": 2,
        "checkpointArchiveSha256": checkpoint["sha256"],
        "operation": "checkpoint_finalize",
        "afterLearn": False,
        "iteration": 4,
        "checkpoint": "checkpoint.safetensors",
        "checkpointSha256": checkpoint["tensorSha256"],
        "policy": "policy.onnx",
        "policySha256": hash_onnx,
        "externalData": [],
    }
    versions = {
        key: job[key] for key in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed")
    }
    versions.update(
        versions=MJLAB_VERSIONS,
        hardware={
            "device": "cuda",
            "gpu": {"uuid": "GPU-b", "name": "GPU model b"},
        },
    )
    if dispatch:
        versions["runtimeIdentity"] = dispatch["runtimeIdentity"]
    (directory / "stdout.log").write_text("")
    (directory / "stderr.log").write_text("")
    (directory / "versions.json").write_text(json.dumps(versions))
    (directory / "export.json").write_text(json.dumps(export))
    source = {
        key: versions[key]
        for key in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed")
    }
    attempt_id = hashlib.sha256(f"{job['jobId']}:{job['leaseId']}".encode()).hexdigest()
    source.update(attemptId=f"attempt-{attempt_id}", versions=versions)
    weights = {
        "checkpointSha256": checkpoint["tensorSha256"],
        "onnxSha256": hash_onnx,
        "observationSha256": hashlib.sha256(observation).hexdigest() if observation else "a" * 64,
    }
    identity = {"module": "fake", "qualname": "fixture", "sha256": "a" * 64}
    capture = {
        "producer": "native-onnx-input/1",
        "profile": "fixture",
        "profileSha256": "b" * 64,
        "capturedAt": "2026-11-19T00:00:00Z",
        "runtime": {
            **dict.fromkeys(("runner", "actor", "act", "exporter", "stateProvider"), identity),
            "collectorSha256": "c" * 64,
            "observerSha256": "d" * 64,
            "observationGroups": ["actor"],
            "environments": [0],
            "boundary": "rsl-mlp-pre-normalizer",
            "normalization": "inside-exported-graph",
            "previousAction": "raw-native-algorithm-output",
            "previousActionReset": "zero-on-environment-epoch",
        },
    }

    if dispatch:
        profile = dispatch["nativeObservationProfile"]
        capture = capture | {
            "profile": profile["id"],
            "profileSha256": canonical_hash(profile),
            "runtime": NativeProfile.model_validate(profile).runtime(),
            "compilerHash": profile["compilerHash"],
        }

    samples, witnesses = [], []
    for number in range(1, 257):
        source_id = f"env-0/epoch-0/step-{number}"
        state = {"step": number}
        vector = [float(number)]
        if dispatch:
            q = number / 256
            state = {
                "joints": {joint: {"q": q, "qd": 0.0}},
                "bodies": {
                    task_doc["declarative"]["baseBody"]: {"p": [0.0, 0.0, 0.0], "q": [0.0, 0.0, 0.0, 1.0]}
                },
                "sites": {},
            }
            vector = [2 * q]
        samples.append(
            {
                "sourceStateId": source_id,
                "state": state,
                "trainingObservation": vector,
                "previousAction": [0.0],
            }
        )
        witnesses.append(
            {
                "sourceStateId": source_id,
                "environment": 0,
                "epoch": 0,
                "physicsStep": number,
                "stateSha256": canonical_hash({"state": state, "references": {}, "previousAction": [0.0]}),
                "nativeInputSha256": canonical_hash(vector),
            }
        )
    common = {"source": source, "policy": weights, "capture": capture}
    raw_samples = json.dumps(
        common
        | {"schema": f"lw.policy-native-observation-samples/{2 if dispatch else 1}", "samples": samples}
    )
    (evidence / "native-samples.json").write_text(raw_samples)
    receipt = common | {
        "schema": "lw.policy-native-observation-capture/2",
        "sampleCount": 256,
        "samplesArtifact": "observations/native-samples.json",
        "samplesSha256": hashlib.sha256(raw_samples.encode()).hexdigest(),
        "witnesses": witnesses,
        "normalizerState": {},
        "normalizerStateSha256": canonical_hash({}),
        "afterLearn": False,
        "iteration": 4,
        "finalizedCheckpointSha256": checkpoint["sha256"],
    }
    (evidence / "native-capture.json").write_text(json.dumps(receipt))


def test_automatic_resume_accounts_for_the_closed_attempt_in_the_same_heartbeat(tmp_path):
    settings = remote_settings()
    clock = [1000.0]

    def app():
        server = create_app(settings)
        server.state.jobs.clock = lambda: clock[0]
        return server

    with TestClient(app()) as client:
        task = client.post(
            "/api/v1/tasks",
            headers=OPERATOR | {"Content-Type": "application/x-tar"},
            content=task_archive(_build_mjlab_task_package(tmp_path / "billing-task")),
        ).json()
        submitted = client.post(
            "/api/v1/plans",
            headers=OPERATOR,
            json=training_plan(
                task["id"], algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"}
            ),
        )
        assert submitted.status_code == 201, submitted.text
        plan = submitted.json()
        approved = client.post(
            f"/api/v1/approvals/{plan['approvalId']}/decision",
            headers=OPERATOR,
            json={"decision": "approve"},
        )
        assert approved.is_success, approved.text
        nodes = {identity: register(client, identity) for identity in ("a", "b")}
        priced = client.put(
            "/api/v1/prices",
            headers=OPERATOR,
            json={
                "revision": 0,
                "gpus": [{"nodeId": "a", "usdPerHour": 3.6}, {"nodeId": "b", "usdPerHour": 7.2}],
            },
        )
        assert priced.is_success, priced.text

        def capabilities(identity):
            gpu = {"id": f"GPU-{identity}", "name": identity}
            runtime = {
                "trainingEntries": {"mjlab": "mjlab:managed"},
                "trainerVersions": MJLAB_VERSIONS,
                "compilerHash": COMPILER,
            }
            key = node_profile_key(
                plan,
                task["contentHash"],
                runtime | {"id": identity, "hardware": "fixture"},
                gpu,
                backend="mjlab",
            )
            return runtime | {
                "gpus": [gpu],
                "checkpointVersion": 1,
                "profiles": [
                    {
                        "key": key,
                        "iterationSeconds": 20,
                        "samples": 2,
                        "measuredAt": 999,
                        "evidence": "fixture",
                    }
                ],
            }

        a, b = nodes["a"], nodes["b"]
        first, second = capabilities("a"), capabilities("b")
        assert beat(client, a, **first).is_success
        started = client.post("/api/v1/jobs", headers=OPERATOR, json={"planId": plan["id"], "seed": 42})
        assert started.status_code == 202, started.text
        job = started.json()
        url = f"/api/v1/jobs/{job['jobId']}"
        lease = a | {"X-Lease-Id": job["leaseId"]}
        dispatch = client.get(url + "/inputs", headers=lease).json()
        clock[0] = 1059
        report = {
            "jobId": job["jobId"],
            "leaseId": job["leaseId"],
            "status": "running",
            "iteration": 4,
            "elapsedSeconds": 59,
        }
        assert beat(client, a, **first, reports=[report]).is_success
        saved = client.post(url + "/checkpoints", headers=lease, content=checkpoint_archive(dispatch))
        assert saved.status_code == 201, saved.text

        clock[0] = 1120
        # This single B heartbeat expires A and leases B before the Job and ledger are saved.
        (assignment,) = beat(client, b, **second).json()["assignments"]
        assert assignment["jobId"] == job["jobId"] and assignment["leaseId"] != job["leaseId"]
        charged_a = client.get("/api/v1/cost/summary?range=all&node=a", headers=OPERATOR).json()["totals"]
        assert charged_a["gpuSeconds"] == 120
        assert charged_a["estimatedGpuSeconds"] == 61

        clock[0] = 1140
        settled = report | {"leaseId": assignment["leaseId"], "status": "failed", "elapsedSeconds": 20}
        assert beat(client, b, **second, reports=[settled]).is_success

    def verify_charges(client):
        public = client.get(url, headers=OPERATOR).json()
        assert public["status"] == "failed"
        assert [row["gpuSeconds"] for row in public["attempts"]] == [120, 20]
        assert public["chargedGpuHours"] == pytest.approx(140 / 3600)
        total = client.get("/api/v1/cost/summary?range=all", headers=OPERATOR).json()["totals"]
        assert total["gpuSeconds"] == 140 and total["reportedGpuSeconds"] == 79
        assert total["estimatedGpuSeconds"] == 61 and total["gpuUsd"] == pytest.approx(0.16)
        rows = client.get("/api/v1/cost/breakdown?range=all&by=node", headers=OPERATOR).json()["rows"]
        assert {row["key"]: row["gpuSeconds"] for row in rows} == {"a": 120, "b": 20}
        for start, end, reported, estimated in (
            ("00:16:40", "00:17:39", 59, 0),
            ("00:17:39", "00:18:40", 0, 61),
        ):
            ranged = client.get(
                "/api/v1/cost/summary",
                headers=OPERATOR,
                params={
                    "range": "custom",
                    "start": f"1970-01-01T{start}Z",
                    "end": f"1970-01-01T{end}Z",
                    "node": "a",
                },
            ).json()["totals"]
            assert (ranged["reportedGpuSeconds"], ranged["estimatedGpuSeconds"]) == (reported, estimated)

    with TestClient(app()) as client:
        verify_charges(client)
        for _ in range(2):
            assert beat(client, b, **second, reports=[settled]).is_success
        assert beat(client, a, **first, reports=[report]).status_code == 403
        verify_charges(client)
