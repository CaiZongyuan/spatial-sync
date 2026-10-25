"""Controlled scores/qualification prove the actual promotion transaction, never learned quality."""

import threading
from collections import deque
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from apps.api.app.library_validation import program_schemas
from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.info.evaluation_identity import freeze_identity
from packages.info.evaluations import wilson_interval
from packages.library.transactions import durable_json
from packages.policies.subjects import resolve_policy_subject
from tests.fixtures.policy import observation_spec
from tests.fixtures.policy_evaluation import native_suite
from tests.fixtures.training_task import training_plan
from tests.gateway.test_policies import uploaded_job
from tests.gateway.test_training_jobs import TOKEN
from tests.unit.test_controller_publication import ControlledPublicationValidator


def controlled_qualification(policy, check=None):
    return {"qualified": True, "check": check or "controlled-observation-check",
            "evidenceMode": "controlled", "policy": policy}


@pytest.fixture
def promotion_inputs(tmp_path, monkeypatch, request):
    starts = []

    def no_source(*args, **kwargs):
        starts.append((args, kwargs))
        raise AssertionError("This controlled transaction fixture must start no physical Source")

    app = create_app(
        Settings(node_token=TOKEN), source_factory=no_source,
        controller_promotion_qualification=controlled_qualification,
        controller_publication_validator=ControlledPublicationValidator(
            tmp_path / "validation", program_schemas
        ),
    )
    manager = app.state.manager
    monkeypatch.setattr(manager.evaluations, "_busy", lambda: True)
    with TestClient(app) as client:
        spec = observation_spec()
        spec["observations"][0]["keys"] = spec["action"]["jointKeys"] = [
            "/World/Lab/RobotA/fr3/fr3_joint1"
        ]
        job = uploaded_job(client, tmp_path, spec=spec)
        created = client.post(
            "/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoints/model_0.pt"}
        )
        assert created.status_code == 201, created.text
        policy = created.json()
        scene = manager.library.resolve(policy["scene"], launch=True, source="newton")
        suite = native_suite(manager, scene, name="promotion-controlled", heldout=True)
        manager.evaluations.suites.refresh()
        subject = resolve_policy_subject(manager, policy["id"], suite, scene)
        store = manager.evaluations.store
        identity = {"environment": "controlled-environment", "judge": "controlled-judge"}
        key = {"suite": suite.id, "suiteContentHash": suite.content_hash,
               "scene": scene.id, "sceneContentHash": scene.content_hash, **identity}
        for label, candidate, successes, decision in (
            ("reference", {"kind": "program", "id": "robot_arm.newton", "contentHash": "1" * 64},
             0, "baseline"),
            ("candidate", subject.document()["candidateIdentity"], getattr(request, "param", 10), "accepted"),
        ):
            identifier = "eval-controlled-" + label
            requested = candidate["id"]
            snapshot = freeze_identity(identifier, suite, scene, requested, store.root / "identity-inputs",
                                       candidate=candidate if label == "candidate" else None)
            candidate = snapshot["candidateIdentity"]
            row = {"evaluationId": identifier, "suite": suite.id, "subject": requested,
                   "candidateIdentity": candidate, "key": deepcopy(key), "split": "train",
                   "comparisonIdentity": deepcopy(identity), "decision": decision,
                   "reference": "eval-controlled-reference" if label == "candidate" else None,
                   "episodes": 10, "successes": successes, "score": successes / 10,
                   "ci95": wilson_interval(successes, 10), "at": "2026-10-25T00:00:00+00:00"}
            record = {"evaluationId": identifier, "suite": suite.id, "suiteContentHash": suite.content_hash,
                      "subject": requested, "candidateIdentity": candidate,
                      "split": "train", "status": "completed", "result": row,
                      "identitySnapshot": snapshot, "runtimeHash": manager.evaluations._runtime_hash,
                      "episodes": [{"status": "valid", "success": index < successes,
                                    "cleanupComplete": True} for index in range(10)], "at": row["at"]}
            if label == "candidate":
                record.update(policySubject=subject.document(), planId=job["planId"])
            store.append(row)
            store.save(record)
        yield client, app.state.controller_promotions, policy, scene, starts


def request(client, policy, scene, request_id="controlled-request"):
    response = client.post("/api/v1/controller-promotions", json={
        "subject": policy["id"], "scene": scene.id, "requestId": request_id,
    })
    assert response.status_code == 202, response.text
    return response.json()


def complete_controlled_tests(service, promotion):
    value = service.get(promotion["id"])
    store = service.manager.evaluations.store
    for side, identifier in value["runs"].items():
        record = deepcopy(store.records[identifier])
        train = value["context"]["currentAccepted" if side == "candidate" else "priorReference"]
        success = side == "reference"
        row = train | {"evaluationId": identifier, "split": "test", "decision": "test",
                       "episodes": 1, "successes": int(success), "score": float(success),
                       "ci95": wilson_interval(int(success), 1)}
        record.update(status="completed", result=row, attemptedEpisodes=1,
                      episodes=[{"caseId": "test-one", "status": "valid", "success": success,
                                 "cleanupComplete": True}])
        store.append(row)
        store.save(record)
    service.manager.evaluations._queue = deque(
        entry for entry in service.manager.evaluations._queue if entry[1] not in value["runs"].values()
    )


def test_refuses_unqualified_or_noncurrent_train_before_any_private_work(promotion_inputs):
    client, service, policy, scene, starts = promotion_inputs
    service.qualify = service.manager.policy_evidence.qualification
    before = set(service.manager.evaluations.store.records)
    response = client.post("/api/v1/controller-promotions", json={"subject": policy["id"], "scene": scene.id})
    assert response.status_code == 409, response.text
    assert set(service.manager.evaluations.store.records) == before and not starts
    assert not list(service.root.glob("promotion-*.json"))


def test_private_review_warning_human_decision_and_exact_committed_authority(promotion_inputs):
    client, service, policy, scene, starts = promotion_inputs
    promotion = request(client, policy, scene)
    assert request(client, policy, scene) == promotion
    value = service.get(promotion["id"])
    assert len(value["runs"]) == 2 and not starts
    for identity in value["runs"].values():
        record = service.manager.evaluations.store.records[identity]
        assert record["split"] == "test" and record["allocatedEpisodes"] == 1
        assert client.get("/api/v1/evaluations/" + identity).status_code == 404
    complete_controlled_tests(service, promotion)
    route = "/api/v1/controller-promotions/" + promotion["id"]
    assert client.get(route + "/review", headers={"X-Actor": "agent:optimizer"}).status_code == 403
    review = client.get(route + "/review")
    assert review.status_code == 200, review.text
    review = review.json()
    assert review["candidateBelowReference"] and review["qualification"]["evidenceMode"] == "controlled"
    public = client.get(route).json()
    assert "test" not in public and "candidateBelowReference" not in public
    approval = client.get("/api/v1/approvals/" + review["approvalId"]).json()
    assert set(approval["details"]) == {"controllerPromotionId", "contextHash", "reviewUrl"}
    decision = "/api/v1/approvals/" + approval["approvalId"] + "/decision"
    assert client.post(
        decision, headers={"X-Actor": "agent:optimizer"}, json={"decision": "approve"}
    ).status_code == 403
    published = client.post(decision, json={"decision": "approve"})
    assert published.status_code == 200, published.text
    published = published.json()
    assert published["status"] == "published", published
    deployed = service.manager.library.resolve(
        published["deployment"]["scene"]["id"], launch=True, source="newton"
    )
    permit = service.authorize_scene(deployed)
    assert permit["candidateIdentity"]["id"] == policy["id"]
    assert permit["controller"]["document"]["evaluationId"] == "eval-controlled-candidate"
    assert permit["controller"]["document"]["task"] == policy["task"]
    assert permit["controller"]["document"]["action"]["hz"] == 30.0
    assert permit["controller"]["document"]["action"]["hzSource"] == policy["id"] + "/task.json#/control/hz"
    assert service.resources.controller("fr3-arm")["current"]["scene"] == deployed.id
    assert client.post(decision, json={"decision": "revoke"}).status_code == 409
    funding = service.get(promotion["id"])["context"]["funding"]
    revoke = client.post("/api/v1/approvals/" + funding["planApprovalId"] + "/decision",
                         json={"decision": "revoke"})
    assert revoke.status_code == 200, revoke.text
    assert service.authorize_scene(deployed) == permit
    assert not starts and policy["qualified"] is False


@pytest.mark.parametrize("failure_point", ["scene_published", "current_written"])
def test_publication_interruption_resumes_same_scene_and_controller_revision(
    promotion_inputs, tmp_path, failure_point
):
    client, service, policy, scene, starts = promotion_inputs
    promotion = request(client, policy, scene)
    complete_controlled_tests(service, promotion)
    review = client.get(f"/api/v1/controller-promotions/{promotion['id']}/review").json()

    def interrupt(effect, _):
        if effect == failure_point:
            raise OSError("Controlled interruption after actual Scene import")

    service.publication.after_effect = interrupt
    route = "/api/v1/approvals/" + review["approvalId"] + "/decision"
    with pytest.raises(OSError, match="Controlled interruption"):
        client.post(route, json={"decision": "approve"})
    interrupted = service.publication.read(promotion["id"])
    assert interrupted["phase"] == ("scene_published" if failure_point == "scene_published"
                                    else "controller_version_written")
    if failure_point == "current_written":
        assert service.resources.controller("fr3-arm")["currentVersion"] == interrupted["controllerVersion"]
        can_revoke = client.get("/api/v1/approvals/" + review["approvalId"]).json()["canRevoke"]
        revoked = client.post(route, json={"decision": "revoke"})
        assert revoked.status_code == 409, revoked.text
        assert not can_revoke
        assert client.get("/api/v1/approvals/" + review["approvalId"]).json()["status"] == "approved"
        retried = client.post(route, json={"decision": "approve"})
        assert retried.status_code == 200 and retried.json()["status"] == "published"
    else:
        assert service.resources.controller("fr3-arm")["currentVersion"] is None
    settings, factory = service.manager.settings, service.manager.factory
    client.__exit__(None, None, None)  # End this owned gateway before reopening its durable roots.
    app = create_app(settings, source_factory=factory,
                     controller_promotion_qualification=controlled_qualification,
                     controller_publication_validator=ControlledPublicationValidator(
                         tmp_path / "restart-validation", program_schemas
                     ))
    with TestClient(app) as restarted:
        recovered = restarted.get("/api/v1/controller-promotions/" + promotion["id"]).json()
        assert recovered["status"] == "published", recovered
        assert recovered["deployment"]["scene"] == interrupted["scene"]
        assert recovered["deployment"]["controller"]["document"]["version"] == (
            interrupted["controllerVersion"])
        assert len(app.state.robot_resources.controller("fr3-arm")["versions"]) == 1 and not starts
        deployed = app.state.manager.library.resolve(recovered["deployment"]["scene"]["id"],
                                                     launch=True, source="newton")
        assert app.state.controller_promotions.authorize_scene(deployed)["approvalId"] == review["approvalId"]


def test_new_reference_before_human_decision_blocks_old_intent(promotion_inputs):
    client, service, policy, scene, starts = promotion_inputs
    promotion = request(client, policy, scene)
    complete_controlled_tests(service, promotion)
    review = client.get(f"/api/v1/controller-promotions/{promotion['id']}/review").json()
    store = service.manager.evaluations.store
    later = deepcopy(store.rows[1])
    later.update(evaluationId="eval-controlled-later", reference=later["evaluationId"])
    store.append(later)
    response = client.post("/api/v1/approvals/" + review["approvalId"] + "/decision",
                           json={"decision": "approve"})
    assert response.status_code == 409, response.text
    assert service.resources.controller("fr3-arm")["currentVersion"] is None
    assert not starts and not list(service.publication.root.glob("*/record.json"))


def test_denial_and_revoke_preserve_current_without_publication(promotion_inputs):
    client, service, policy, scene, starts = promotion_inputs
    promotion = request(client, policy, scene)
    complete_controlled_tests(service, promotion)
    review = client.get(f"/api/v1/controller-promotions/{promotion['id']}/review").json()
    route = "/api/v1/approvals/" + review["approvalId"]
    denied = client.post(route + "/decision", json={"decision": "deny"})
    assert denied.status_code == 200 and denied.json()["status"] == "denied"
    assert client.get(route).json()["canRevoke"]
    revoked = client.post(route + "/decision", json={"decision": "revoke"})
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    assert service.resources.controller("fr3-arm")["currentVersion"] is None and not starts
    assert not list(service.publication.root.glob("*/record.json"))


def test_both_private_sides_require_the_existing_total_budget_before_either_is_queued(promotion_inputs):
    client, service, policy, scene, starts = promotion_inputs
    store = service.manager.evaluations.store
    current = store.records["eval-controlled-candidate"]
    revised = client.post("/api/v1/plans/" + current["planId"] + "/revisions",
                          json=training_plan(policy["task"], budget={"gpuHours": 1, "episodes": 11}))
    assert revised.status_code == 201, revised.text
    approved = client.post("/api/v1/approvals/" + revised.json()["approvalId"] + "/decision",
                           json={"decision": "approve"})
    assert approved.status_code == 200, approved.text
    before = set(store.records)
    response = client.post("/api/v1/controller-promotions", json={
        "subject": policy["id"], "scene": scene.id, "requestId": "controlled-insufficient-budget",
    })
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "plan_evaluation_budget_exhausted"
    assert set(store.records) == before and not starts
    saved = [service.get(path.stem) for path in service.root.glob("promotion-*.json")]
    assert len(saved) == 1 and saved[0]["status"] == "blocked"


def test_competing_controller_before_current_commit_is_preserved(promotion_inputs):
    client, service, policy, scene, starts = promotion_inputs
    promotion = request(client, policy, scene)
    complete_controlled_tests(service, promotion)
    review = client.get(f"/api/v1/controller-promotions/{promotion['id']}/review").json()

    def compete(effect, record):
        if effect == "scene_published":
            user = service.manager.library_assets.roots["user"]
            current = record["newCurrent"] | {"currentVersion": 99}
            version = record["controller"]["document"] | {"version": 99, "scene": scene.id}
            durable_json(user / "controllers/fr3-arm/99.yaml", version)
            durable_json(user / "controllers/fr3-arm/controller.yaml", current)

    service.publication.after_effect = compete
    response = client.post("/api/v1/approvals/" + review["approvalId"] + "/decision",
                           json={"decision": "approve"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"
    assert service.resources.controller("fr3-arm")["currentVersion"] == 99 and not starts
    assert service.publication.read(promotion["id"])["phase"] == "blocked"


def test_reference_append_waits_for_the_actual_controller_current_commit(promotion_inputs):
    client, service, policy, scene, starts = promotion_inputs
    promotion = request(client, policy, scene)
    complete_controlled_tests(service, promotion)
    review = client.get(f"/api/v1/controller-promotions/{promotion['id']}/review").json()
    store = service.manager.evaluations.store
    later = deepcopy(store.rows[1]) | {"evaluationId": "eval-after-commit"}
    started, appended = threading.Event(), threading.Event()

    def append_after_current():
        started.set()
        store.append(later)
        appended.set()

    thread = threading.Thread(target=append_after_current, name="owned-reference-append")

    def concurrent(effect, _):
        if effect == "current_written":
            thread.start()
            assert started.wait(1)
            assert not appended.wait(0.05), "Reference advanced inside the Controller commit guard"

    service.publication.after_effect = concurrent
    try:
        response = client.post("/api/v1/approvals/" + review["approvalId"] + "/decision",
                               json={"decision": "approve"})
        assert response.status_code == 200 and response.json()["status"] == "published", response.text
    finally:
        if thread.ident is not None:
            thread.join(timeout=2)
    assert appended.is_set() and not thread.is_alive() and not starts
    deployed = service.manager.library.resolve(response.json()["deployment"]["scene"]["id"],
                                               launch=True, source="newton")
    assert service.authorize_scene(deployed)["candidateIdentity"]["id"] == policy["id"]


@pytest.mark.parametrize("promotion_inputs", [6], indirect=True)
def test_orphan_model_cannot_poison_a_later_accepted_context_for_the_same_candidate(promotion_inputs):
    client, service, policy, scene, starts = promotion_inputs
    first = request(client, policy, scene, "controlled-orphan-model")
    complete_controlled_tests(service, first)
    review = client.get(f"/api/v1/controller-promotions/{first['id']}/review").json()

    def interrupt(effect, _):
        if effect == "scene_published":
            raise OSError("Controlled failure after immutable model and Scene publication")

    service.publication.after_effect = interrupt
    with pytest.raises(OSError, match="Controlled failure"):
        client.post("/api/v1/approvals/" + review["approvalId"] + "/decision", json={"decision": "approve"})
    first_model = service.get(first["id"])["context"]["robotModel"]
    user = service.manager.library_assets.roots["user"]
    old_path = user / "robot-models" / f"{first_model['id']}.yaml"
    old_bytes = old_path.read_bytes()
    store = service.manager.evaluations.store
    previous = deepcopy(store.records["eval-controlled-candidate"])
    later = deepcopy(previous["result"]) | {
        "evaluationId": "eval-controlled-same-material-later", "reference": "eval-controlled-candidate",
        "successes": 10, "score": 1.0, "ci95": wilson_interval(10, 10), "at": "2026-10-26T00:00:00+00:00",
    }
    previous.update(evaluationId=later["evaluationId"], result=later, at=later["at"],
                    episodes=[{"status": "valid", "success": True, "cleanupComplete": True}
                              for _ in range(10)])
    store.append(later)
    store.save(previous)
    service.publication.after_effect = lambda *_: None
    second = request(client, policy, scene, "controlled-new-context")
    complete_controlled_tests(service, second)
    review = client.get(f"/api/v1/controller-promotions/{second['id']}/review").json()
    published = client.post("/api/v1/approvals/" + review["approvalId"] + "/decision",
                            json={"decision": "approve"})
    assert published.status_code == 200 and published.json()["status"] == "published", published.text
    second_model = service.get(second["id"])["context"]["robotModel"]
    assert first_model["id"] != second_model["id"] and old_path.read_bytes() == old_bytes
    assert second_model["quality"]["evaluationId"] == later["evaluationId"]
    assert "testEvidence" not in second_model and not starts


@pytest.mark.parametrize("scope", ["candidate-train", "reference-train", "candidate-test", "reference-test"])
def test_missing_recoverable_identity_blob_blocks_new_review_and_approval(promotion_inputs, scope):
    client, service, policy, scene, starts = promotion_inputs
    promotion = request(client, policy, scene)
    complete_controlled_tests(service, promotion)
    route = f"/api/v1/controller-promotions/{promotion['id']}/review"
    review = client.get(route).json()
    frozen = service.get(promotion["id"])
    side, split = scope.split("-")
    identifier = (frozen["runs"][side] if split == "test" else frozen["context"][
        "currentAccepted" if side == "candidate" else "priorReference"]["evaluationId"])
    store = service.manager.evaluations.store
    record = deepcopy(store.records[identifier])
    path = store.root / "identity-inputs" / record["identitySnapshot"]["recovery"]["file"]
    original = path.read_bytes()
    path.write_bytes(original + b"controlled evidence corruption")
    try:
        failed = client.get(route)
        assert failed.status_code == 409 and failed.json()["detail"]["code"] == "not_comparable", failed.text
        decision = client.post("/api/v1/approvals/" + review["approvalId"] + "/decision",
                               json={"decision": "approve"})
        assert decision.status_code == 409 and decision.json()["detail"]["code"] == "not_comparable"
        assert store.records[identifier] == record
        assert service.resources.controller("fr3-arm")["currentVersion"] is None and not starts
        assert not list(service.publication.root.glob("*/record.json"))
    finally:
        path.write_bytes(original)
