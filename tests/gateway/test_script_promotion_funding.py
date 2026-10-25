"""Controlled train scores isolate exact Script funding intent before private work."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.programs.script_subjects import resolve_program_subject
from packages.training.inputs import pack_package
from tests.fixtures.controller_promotion_evidence import seed_controlled_subject
from tests.fixtures.embodied_evaluation import native_suite
from tests.gateway.test_program_plans import prepare


def approve(client, body):
    response = client.post("/api/v1/plans", json=body)
    assert response.status_code == 201, response.text
    plan = response.json()
    response = client.post("/api/v1/approvals/" + plan["approvalId"] + "/decision",
                           json={"decision": "approve"})
    assert response.status_code == 200, response.text
    return plan


@pytest.mark.parametrize("same_material", [False, True])
def test_script_private_tests_require_the_funding_plans_exact_program_intent(tmp_path, same_material):
    starts = []

    def forbidden(*args):
        starts.append(args)
        raise AssertionError("Funding metadata test must start no Source")

    app = create_app(source_factory=forbidden)
    manager = app.state.manager
    manager.evaluations._busy = lambda: True
    with TestClient(app) as client:
        body, program = prepare(client, tmp_path)
        body["budget"]["episodes"] = 32
        code_plan = approve(client, body)
        scene = manager.library.resolve(body["evaluation"]["scene"], launch=True, source="newton")
        suite = native_suite(manager, scene, name="script-funding-controlled", heldout=True)
        manager.evaluations.suites.refresh()
        subject = resolve_program_subject(manager, program["id"], suite, scene, plan_id=code_plan["id"])
        seed_controlled_subject(manager, subject, suite, scene, plan_id=code_plan["id"])
        funding_body = deepcopy(body)
        if not same_material:
            directory = tmp_path / "program"
            source = directory / "controller.py"
            source.write_bytes(source.read_bytes() + b"\n# A different immutable controller.\n")
            response = client.post("/api/v1/programs", content=pack_package(directory),
                                   headers={"Content-Type": "application/x-tar"})
            assert response.status_code == 201, response.text
            other = response.json()
            funding_body["program"] = {key: other[key] for key in ("id", "contentHash")}
        funding = approve(client, funding_body)
        assert funding["id"] != code_plan["id"]
        before = set(manager.evaluations.store.records)
        response = client.post("/api/v1/controller-promotions", json={
            "subject": program["id"], "scene": scene.id, "evaluationId": "eval-controlled-candidate",
            "planId": funding["id"], "planRevision": funding["revision"], "requestId": "funding-intent",
        })
        fresh = set(manager.evaluations.store.records) - before
        if same_material:
            assert response.status_code == 202, response.text
            transaction = app.state.controller_promotions.get(response.json()["id"])
            assert transaction["context"]["subject"]["codeApproval"]["planId"] == code_plan["id"]
            assert transaction["context"]["funding"]["planId"] == funding["id"]
            assert len(fresh) == 2
            assert all(manager.evaluations.store.records[key]["planId"] == funding["id"] for key in fresh)
        else:
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["code"] == "program_plan_mismatch"
            assert not fresh and not list(app.state.controller_promotions.root.glob("promotion-*.json"))
        assert not starts
