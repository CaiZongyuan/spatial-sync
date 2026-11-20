"""Real HTTP publication and recovery with controlled episode scores."""

import hashlib
import io
import json
import tarfile
from collections import deque

import yaml
from fastapi.testclient import TestClient

from apps.api.app.library_validation import program_schemas
from apps.api.app.main import create_app
from apps.node.mjlab_launcher import compiler_hash
from packages.info.evaluations import wilson_interval
from packages.library.common import BUILTIN_ROOT, document
from packages.training.inputs import pack_package
from tests.fixtures.controller_promotion_evidence import controlled_qualification
from tests.fixtures.training_task import training_plan
from tests.gateway.test_checkpoint_jobs import checkpoint_archive
from tests.gateway.test_remote_training_nodes import (
    MJLAB_VERSIONS,
    OPERATOR,
    beat,
    node_profile_key,
    register,
    remote_settings,
)
from tests.unit.test_controller_publication import ControlledPublicationValidator

GOAL = "goal/first-reference-probe"
AGENT = OPERATOR | {"X-Actor": "agent:optimizer"}
EPISODES = 30
CONTROLLER = "/api/v1/controllers/microduck-biped"
EMBODIMENT = "/api/v1/embodiments/microduck-sim"


def checked(response, status):
    assert response.status_code == status, (response.request.url.path, response.text)
    return response.json()


def publish_goal_policy(client, root):
    original = BUILTIN_ROOT / "tasks/microduck-stand-up/1/package"
    task = document(original / "task.yaml")
    task.update(name="first-reference-probe", declarative={
        "baseBody": task["robot"] + "/trunk_base",
        "episodeSeconds": 8.0,
        "rewards": [{"kind": "upright", "weight": 1.0}],
    })
    task["loopBudget"]["episodes"] = 2 * EPISODES
    scene = task["scene"]
    package = root / "task"
    package.mkdir()
    (package / "task.yaml").write_text(yaml.safe_dump(task))
    observation = document(original / task["observation"]) | {"schema": "lw.obs-spec/2"}
    (package / task["observation"]).write_text(yaml.safe_dump(observation))
    upload = OPERATOR | {"Content-Type": "application/x-tar"}
    task_record = checked(client.post("/api/v1/tasks", headers=upload, content=pack_package(package)), 201)
    plan = checked(client.post("/api/v1/plans", headers=AGENT, json=training_plan(
        task_record["id"], goalId=GOAL,
        algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"},
        evaluation={"scene": scene, "episodes": EPISODES, "threshold": 26},
        budget={"gpuHours": 0, "episodes": 2 * EPISODES},
        hyperparameters={"num_envs": 4, "max_iterations": 4, "device": "cpu"},
    )), 201)
    approval = checked(client.post(
        f"/api/v1/approvals/{plan['approvalId']}/decision", headers=OPERATOR, json={"decision": "approve"},
    ), 200)

    node_id = "goal-reference-node"
    node = register(client, node_id)
    caps = {
        "cpuAvailable": True, "trainingEntries": {"mjlab": "mjlab:managed"},
        "trainerVersions": MJLAB_VERSIONS, "compilerHash": compiler_hash(), "checkpointVersion": 1,
    }
    signature = node_profile_key(
        plan, task_record["contentHash"], caps | {"id": node_id, "hardware": "fixture"},
        {"id": "cpu"}, backend="mjlab",
    )
    caps["profiles"] = [{"key": signature, "iterationSeconds": 1, "samples": 2,
                         "measuredAt": 999, "evidence": "controlled CPU Job"}]
    checked(beat(client, node, **caps), 200)
    job = checked(client.post("/api/v1/jobs", headers=OPERATOR, json={
        "planId": plan["id"], "seed": 42, "targetNodeId": node_id,
    }), 202)
    route = f"/api/v1/jobs/{job['jobId']}"
    lease = node | {"X-Lease-Id": job["leaseId"]}
    dispatch = checked(client.get(f"{route}/inputs", headers=lease), 200)
    heartbeat = {"jobId": job["jobId"], "leaseId": job["leaseId"], "iteration": 4, "elapsedSeconds": 4}
    checked(beat(client, node, **caps, reports=[heartbeat | {"status": "running"}]), 200)
    checkpoint = checkpoint_archive(dispatch)
    descriptor = checked(client.post(f"{route}/checkpoints", headers=lease, content=checkpoint), 201)
    artifacts = root / "artifacts"
    artifacts.mkdir()
    with tarfile.open(fileobj=io.BytesIO(checkpoint)) as archive:
        for name in ("checkpoint.json", "checkpoint.safetensors"):
            (artifacts / name).write_bytes(archive.extractfile(name).read())
    model = (BUILTIN_ROOT / "policies/microduck-stand-up/1/package/artifacts/policy.onnx").read_bytes()
    (artifacts / "policy.onnx").write_bytes(model)
    export = {
        "schemaVersion": 2, "afterLearn": True, "iteration": 4,
        "checkpointArchiveSha256": descriptor["sha256"], "checkpoint": "checkpoint.safetensors",
        "checkpointSha256": descriptor["tensorSha256"], "policy": "policy.onnx",
        "policySha256": hashlib.sha256(model).hexdigest(), "externalData": [],
    }
    identity = ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed")
    versions = {key: job[key] for key in identity} | {
        "versions": MJLAB_VERSIONS, "runtimeIdentity": dispatch["runtimeIdentity"],
        "hardware": {"device": "cpu", "gpu": None},
    }
    for name, content in (("versions.json", json.dumps(versions)), ("export.json", json.dumps(export)),
                          ("stdout.log", ""), ("stderr.log", "")):
        (artifacts / name).write_text(content)
    checked(client.post(f"{route}/artifacts", headers=lease, content=pack_package(artifacts)), 200)
    checked(beat(client, node, **caps, reports=[heartbeat | {"status": "succeeded"}]), 200)
    policy = checked(client.post("/api/v1/policies", headers=OPERATOR, json={
        "checkpoint": job["jobId"] + "/checkpoint.safetensors",
    }), 201)
    return policy, approval["details"]["goalSuite"]["id"]


def finish_controlled_run(manager, suite, identifier, *, train_row=None):
    service = manager.evaluations
    store = service.store
    record = store.records[identifier]
    split = record["split"]
    cases = [case for case in suite.metadata["episodes"] if case["split"] == split]
    assert len(cases) == EPISODES
    if train_row is None:
        comparison = {"environment": "controlled-environment", "judge": "controlled-judge"}
        key = {"suite": suite.id, "suiteContentHash": suite.content_hash, "scene": suite.metadata["scene"],
               "sceneContentHash": suite.metadata["sceneContentHash"], "source": "newton", **comparison}
        train_row = {
            "suite": suite.id, "subject": record["subject"], "candidateIdentity": record["candidateIdentity"],
            "key": key, "comparisonIdentity": comparison, "reference": None,
        }
    row = train_row | {
        "evaluationId": identifier, "split": split, "at": record["at"],
        "decision": "baseline_candidate" if split == "train" else "test",
        "score": 1.0, "successes": EPISODES, "episodes": EPISODES,
        "ci95": wilson_interval(EPISODES, EPISODES),
    }
    record = record | {
        "status": "completed", "result": row, "attemptedEpisodes": EPISODES, "key": row["key"],
        "comparisonIdentity": row["comparisonIdentity"], "evidenceMode": "controlled",
        "episodes": [{"caseId": case["id"], "status": "valid", "success": True, "cleanupComplete": True}
                     for case in cases],
    }
    manager.notebooks.complete_evaluation(record)
    service._queue = deque(item for item in service._queue if item[1] != identifier)
    store.append(row)
    store.save(record)
    return row


def assert_public_readback(client, identifier, suite, policy, seeds, *, committed, hidden_run=None):
    suite_route = "/api/v1/" + suite.replace("@", "/").replace("suite/", "suites/", 1)
    policy_route = "/api/v1/" + policy.replace("@", "/").replace("policy/", "policies/", 1)
    decision = "baseline" if committed else "baseline_candidate"
    state = "baseline" if committed else "unknown"
    paths = {
        "suite": suite_route, "suites": "/api/v1/suites", "scoreboard": suite_route + "/scoreboard",
        "evaluation": f"/api/v1/evaluations/{identifier}", "evaluations": "/api/v1/evaluations",
        "policy": policy_route, "controller": CONTROLLER, "versions": CONTROLLER + "/versions",
        "embodiment": EMBODIMENT, "embodiments": "/api/v1/embodiments",
        "notebook": f"/api/v1/notebooks/{GOAL}", "loop": f"/api/v1/loops/{GOAL}",
        "approvals": "/api/v1/approvals", "recordings": "/api/v1/recordings", "cost": "/api/v1/cost/summary",
    }
    documents = {}
    for key, route in paths.items():
        response = client.get(route, headers=AGENT)
        documents[key] = checked(response, 200)
        assert not any(str(seed) in response.text for seed in seeds), route
        if hidden_run is not None:
            assert hidden_run not in response.text, route
    assert len(documents["suite"]["episodes"]) == EPISODES
    [score] = documents["scoreboard"]["rows"]
    assert (score["evaluationId"], score["decision"]) == (identifier, decision)
    assert documents["evaluation"]["result"] == score
    assert documents["evaluations"]["evaluations"][0]["result"] == score
    assert documents["policy"]["evidence"]["evaluations"][0]["result"] == score
    [entry] = documents["notebook"]["entries"]
    [loop_entry] = documents["loop"]["rounds"]
    assert entry["reportedDecision"] == loop_entry["reportedDecision"] == decision
    assert entry["comparison"] == loop_entry["comparison"]
    assert entry["comparison"]["state"] == state
    current = documents["controller"]["current"]["evaluationEvidence"]
    if committed:
        [listed] = [item for item in documents["embodiments"]["embodiments"] if item["id"] == "microduck-sim"]
        [version] = [item for item in documents["versions"]["versions"] if item["evaluationId"] == identifier]
        [history] = [item for item in documents["embodiment"]["controllerHistory"]
                     if item["evaluationId"] == identifier]
        evidence = [
            current,
            version["evaluationEvidence"],
            documents["embodiment"]["currentController"]["evaluationEvidence"],
            history["evaluationEvidence"],
            listed["currentController"]["evaluationEvidence"],
        ]
        for item in evidence:
            assert (item["evaluationId"], item["decision"], item["currentEvidence"]) == (
                identifier, "baseline", "verified",
            )
    else:
        assert current.get("evaluationId") != identifier
    return entry


def test_first_goal_reference_commits_via_http_and_reopens_with_public_projections(tmp_path):
    def no_source(*_):
        raise AssertionError("Controlled score fixture starts no simulation")

    def application():
        app = create_app(
            remote_settings(), source_factory=no_source,
            controller_promotion_qualification=controlled_qualification,
            controller_publication_validator=ControlledPublicationValidator(
                tmp_path / "validation", program_schemas,
            ),
        )
        app.state.manager.evaluations._busy = lambda: True
        app.state.jobs.clock = lambda: 1000.0
        return app

    app = application()
    with TestClient(app) as client:
        policy, suite_id = publish_goal_policy(client, tmp_path)
        request = {"suite": suite_id, "subject": policy["id"]}
        checked(client.post("/api/v1/evaluations", headers=AGENT, json=request | {"split": "test"}), 403)
        train = checked(client.post("/api/v1/evaluations", headers=AGENT, json=request), 202)
        identifier = train["evaluationId"]
        manager = app.state.manager
        suite = manager.evaluations.suites.resolve(suite_id)
        seeds = {suite.seeds[case["id"]] for case in suite.metadata["episodes"] if case["split"] == "test"}
        train_seeds = {
            suite.seeds[case["id"]] for case in suite.metadata["episodes"] if case["split"] == "train"
        }
        assert len(seeds) == len(train_seeds) == EPISODES and seeds.isdisjoint(train_seeds)
        train_row = finish_controlled_run(manager, suite, identifier)
        before = assert_public_readback(client, identifier, suite_id, policy["id"], seeds, committed=False)
        assert manager.evaluations.store.reference(train_row["key"]) is None
        assert "recordedComparison" not in before
        markdown = manager.library_assets.roots["user"] / "notebooks/first-reference-probe" / (
            before["entryId"] + ".md"
        )
        promotion_request = {"subject": policy["id"], "scene": policy["scene"], "evaluationId": identifier}
        checked(client.post("/api/v1/controller-promotions", headers=AGENT, json=promotion_request), 403)
        promotion = checked(client.post(
            "/api/v1/controller-promotions", headers=OPERATOR, json=promotion_request,
        ), 202)
        private_id = manager.controller_promotions.get(promotion["id"])["runs"]["candidate"]
        finish_controlled_run(manager, suite, private_id, train_row=train_row)
        review_route = f"/api/v1/controller-promotions/{promotion['id']}/review"
        assert client.get(review_route, headers=AGENT).status_code == 403
        assert client.get(f"/api/v1/evaluations/{private_id}", headers=AGENT).status_code == 404
        review = checked(client.get(review_route, headers=OPERATOR), 200)
        assert review["train"]["reference"] is None and review["test"]["candidate"]["episodes"] == EPISODES
        assert not any(str(seed) in json.dumps(review) for seed in seeds)
        approve = f"/api/v1/approvals/{review['approvalId']}/decision"
        assert client.post(approve, headers=AGENT, json={"decision": "approve"}).status_code == 403
        assert manager.evaluations.store.reference(train_row["key"]) is None
        published = checked(client.post(approve, headers=OPERATOR, json={"decision": "approve"}), 200)
        assert published["status"] == "published"
        archived_on_commit = yaml.safe_load(markdown.read_text().split("---", 2)[1])
        controller = checked(client.get(CONTROLLER, headers=AGENT), 200)
        assert controller["current"]["evaluationEvidence"]["decision"] == "baseline"

    with TestClient(application()) as client:
        entry = assert_public_readback(client, identifier, suite_id, policy["id"], seeds,
                                       committed=True, hidden_run=private_id)
        assert entry["recordedComparison"] == before["comparison"]
        archived_after_restart = yaml.safe_load(markdown.read_text().split("---", 2)[1])
        for archived in (archived_on_commit, archived_after_restart):
            assert archived["reportedDecision"] == "baseline"
            assert archived["comparison"] == entry["comparison"]
            assert archived["recordedComparison"] == before["comparison"]
            assert not any(str(seed) in json.dumps(archived) for seed in seeds)
        store = client.app.state.manager.evaluations.store
        assert store.records[identifier]["result"]["decision"] == "baseline_candidate"
        assert store.reference(train_row["key"])["evaluationId"] == identifier
        assert client.get(f"/api/v1/evaluations/{private_id}", headers=AGENT).status_code == 404
