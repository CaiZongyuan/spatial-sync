"""Controlled train evidence exercises real human publication and current Notebook projections."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api.app.library_validation import program_schemas
from apps.api.app.main import create_app
from packages.info.evaluation_identity import freeze_identity
from packages.info.evaluations import wilson_interval
from packages.programs.script_subjects import resolve_program_subject
from packages.training.inputs import pack_package
from tests.fixtures.controller_promotion_evidence import complete_controlled_tests, seed_controlled_subject
from tests.fixtures.embodied_evaluation import native_suite
from tests.gateway.test_program_plans import prepare
from tests.gateway.test_script_promotion_funding import approve
from tests.unit.test_controller_publication import ControlledPublicationValidator


def link_notebook(manager, record, plan, suite, scene):
    context = manager.notebooks.prepare_evaluation(
        SimpleNamespace(planId=plan["id"], planRevision=plan["revision"]), suite, scene
    )
    record.update(planId=plan["id"], planRevision=plan["revision"], goalId=context["goalId"],
                  notebookContext=context, notebookExecution=deepcopy(context["implementations"]),
                  attemptedEpisodes=len(record["episodes"]), key=deepcopy(record["result"]["key"]))
    manager.evaluations.store.save(record)
    manager.notebooks.observe(record)
    return context["goalId"]


def test_notebook_separates_approval_publication_and_actual_default_without_private_results(tmp_path):
    starts = []

    def forbidden(*args):
        starts.append(args)
        raise AssertionError("Notebook projection test must start no Source")

    app = create_app(source_factory=forbidden,
                     controller_publication_validator=ControlledPublicationValidator(
                         tmp_path / "validation", program_schemas))
    manager = app.state.manager
    manager.evaluations._busy = lambda: True
    with TestClient(app) as client:
        body, program = prepare(client, tmp_path)
        body["budget"]["episodes"] = 8
        plan = approve(client, body)
        scene = manager.library.resolve(body["evaluation"]["scene"], launch=True, source="newton")
        suite = native_suite(manager, scene, name="notebook-controller-controlled", heldout=True)
        manager.evaluations.suites.refresh()
        subject = resolve_program_subject(manager, program["id"], suite, scene, plan_id=plan["id"])
        identifiers = {"reference": "eval-" + "0" * 32, "candidate": "eval-" + "1" * 32}
        seed_controlled_subject(manager, subject, suite, scene, plan_id=plan["id"], episodes=1,
                                successes=1, identifiers=identifiers)
        store = manager.evaluations.store
        for identifier in identifiers.values():
            goal = link_notebook(manager, store.records[identifier], plan, suite, scene)
        route = "/api/v1/loops/" + goal
        assert client.get(route).json()["defaultProgram"]["program"] == "robot_arm.newton"
        response = client.post("/api/v1/controller-promotions", json={
            "subject": program["id"], "scene": scene.id, "evaluationId": identifiers["candidate"],
            "planId": plan["id"], "requestId": "notebook-current-default",
        })
        assert response.status_code == 202, response.text
        service = app.state.controller_promotions
        promotion = response.json()
        complete_controlled_tests(service, promotion)
        progress = client.get("/api/v1/controller-promotions/" + promotion["id"]).json()
        before = client.get(route).json()
        assert before["deployment"]["status"] == "pending"
        assert before["deployment"]["publicationStatus"] == "not_committed"

        def interrupt(effect, _):
            if effect == "scene_published":
                raise OSError("Controlled interruption before Controller commit")

        service.publication.after_effect = interrupt
        decision = "/api/v1/approvals/" + progress["approvalId"] + "/decision"
        with pytest.raises(OSError, match="Controlled interruption"):
            client.post(decision, json={"decision": "approve"})
        approved = client.get(route).json()
        assert approved["deployment"]["status"] == "approved"
        assert approved["deployment"]["publicationStatus"] == "not_committed"
        assert approved["defaultProgram"]["program"] == "robot_arm.newton"

        def interrupt_current(effect, _):
            if effect == "current_written":
                raise OSError("Controlled interruption after current commit point")

        service.publication.after_effect = interrupt_current
        with pytest.raises(OSError, match="after current commit point"):
            client.post(decision, json={"decision": "approve"})
        uncertain = client.get(route).json()
        assert uncertain["deployment"]["status"] == "approved"
        assert uncertain["deployment"]["publicationStatus"] == "unavailable"
        assert uncertain["defaultProgram"] is None
        assert service.publication.read(promotion["id"])["phase"] == "controller_version_written"
        service.publication.after_effect = lambda *_: None
        published = client.post(decision, json={"decision": "approve"})
        assert published.status_code == 200 and published.json()["status"] == "published"
        committed = client.get(route).json()
        [row] = [entry for entry in committed["rounds"] if entry["evaluationId"] == identifiers["candidate"]]
        assert row["deployment"]["status"] == "approved"
        assert row["deployment"]["publicationStatus"] == "committed" and row["deployment"]["current"] is True
        assert row["recordedDeployment"]["status"] == "not_requested"
        assert committed["defaultProgram"]["subject"] == program["id"]
        assert committed["defaultProgram"]["scene"] == published.json()["deployment"]["scene"]["id"]
        for identifier in service.get(promotion["id"])["runs"].values():
            assert identifier not in json.dumps(committed)
        assert "testEvidence" not in json.dumps(committed)

        directory = tmp_path / "program"
        source = directory / "controller.py"
        source.write_bytes(source.read_bytes() + b"\n# A later unpromoted candidate.\n")
        response = client.post("/api/v1/programs", content=pack_package(directory),
                               headers={"Content-Type": "application/x-tar"})
        assert response.status_code == 201, response.text
        other = response.json()
        next_body = deepcopy(body)
        next_body["program"] = {key: other[key] for key in ("id", "contentHash")}
        next_plan = approve(client, next_body)
        next_subject = resolve_program_subject(manager, other["id"], suite, scene, plan_id=next_plan["id"])
        later = deepcopy(store.records[identifiers["candidate"]])
        later_id = "eval-" + "2" * 32
        candidate = next_subject.document()["candidateIdentity"]
        later.update(evaluationId=later_id, subject=other["id"], candidateIdentity=candidate,
                     simulationSubject=next_subject.document(),
                     identitySnapshot=freeze_identity(later_id, suite, scene, other["id"],
                                                      store.root / "identity-inputs", candidate=candidate),
                     at="2026-10-25T01:00:00+00:00", episodes=[{"status": "valid", "success": False,
                                                              "cleanupComplete": True}])
        result = deepcopy(later["result"])
        result.update(evaluationId=later_id, subject=other["id"], candidateIdentity=candidate,
                      decision="rejected", reference=identifiers["candidate"], successes=0, score=0.0,
                      ci95=wilson_interval(0, 1), at=later["at"])
        later["result"] = result
        store.append(result)
        link_notebook(manager, later, next_plan, suite, scene)
        latest = client.get(route).json()
        assert latest["candidate"]["subject"] == other["id"]
        assert latest["candidate"]["reportedDecision"] == "rejected"
        assert latest["deployment"]["status"] == "not_requested"
        assert latest["defaultProgram"] == committed["defaultProgram"]
        failed = deepcopy(later)
        failed_id = "eval-" + "3" * 32
        failed.update(evaluationId=failed_id, status="failed", episodes=[], attemptedEpisodes=0,
                      at="2026-10-25T02:00:00+00:00", error={"code": "program_runtime_invalid"})
        failed.pop("result")
        failed.pop("key")
        store.save(failed)
        manager.notebooks.observe(failed)
        no_ready_source = client.get(route).json()
        assert no_ready_source["candidate"]["score"] is None
        assert no_ready_source["defaultProgram"] == committed["defaultProgram"]
        assert client.post("/api/v1/approvals/" + plan["approvalId"] + "/decision",
                           json={"decision": "revoke"}).status_code == 200
        assert client.get(route).json()["defaultProgram"] == committed["defaultProgram"]

        proof = service.publication._directory(promotion["id"]) / "record.json"
        missing = proof.with_suffix(".missing")
        proof.rename(missing)
        try:
            unknown = client.get(route).json()
            assert unknown["defaultProgram"] is None
            [history] = [entry for entry in unknown["rounds"]
                         if entry["evaluationId"] == identifiers["candidate"]]
            assert history["reportedDecision"] == "accepted" and history["deployment"]["status"] == "approved"
            assert history["deployment"]["promotionStatus"] == "published"
            assert history["deployment"]["publicationStatus"] == "unavailable"
            assert history["deployment"]["current"] is None
        finally:
            missing.rename(proof)
        assert not starts
