"""Actual Script promotion permissions on a gripper-only fixture; not the successful tube-transfer demo."""

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.library_validation import program_schemas
from apps.api.app.main import create_app
from apps.api.app.session_manager import default_source_factory
from packages.policies.subjects import fixed_bindings
from packages.training.inputs import pack_package
from tests.fixtures.script_deployment import gripper_promotion_suite
from tests.fixtures.script_program import closing_program_directory
from tests.fixtures.training_task import task_archive, task_directory, training_plan
from tests.sim_runtime.test_policy_evaluation_api import evaluate
from tests.unit.test_controller_publication import ControlledPublicationValidator


async def test_actual_script_human_publication_then_plan_revocation_keeps_committed_scene_loadable(
    tmp_path, record_property
):
    with TestClient(create_app()) as seed:
        manager = seed.app.state.manager
        scene = manager.library.resolve("robot-and-tubes@2", launch=True, source="newton")
        fingers = fixed_bindings(scene, "newton")["/World/Lab/RobotA"]["config"]["fingerKeys"]
        program_dir = closing_program_directory(tmp_path / "script", fingers)
        response = seed.post(
            "/api/v1/programs",
            content=pack_package(program_dir),
            headers={"Content-Type": "application/x-tar"},
        )
        assert response.status_code == 201, response.text
        program = response.json()
        task_dir = task_directory(tmp_path / "task", "script-deployment")
        response = seed.post(
            "/api/v1/tasks", content=task_archive(task_dir), headers={"Content-Type": "application/x-tar"}
        )
        assert response.status_code == 201, response.text
        body = training_plan(
            response.json()["id"],
            title="Review gripper permission engineering",
            goal="Validate frozen code and committed deployment permissions on actual gripper feedback",
            algorithm={},
            model={},
            hyperparameters={},
            jobs={"seeds": []},
            program={"id": program["id"], "contentHash": program["contentHash"]},
            budget={"gpuHours": 0, "episodes": 8},
            evaluation={"scene": scene.id, "episodes": 1, "threshold": 1},
        )
        response = seed.post("/api/v1/plans", json=body)
        assert response.status_code == 201, response.text
        plan = response.json()
        assert (
            seed.post(
                "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "approve"}
            ).status_code
            == 200
        )
        suite = gripper_promotion_suite(manager, scene)
    starts = []

    def counted_source(kind, identifier):
        starts.append((kind, identifier))
        return default_source_factory(kind, identifier)

    # The builtin and derived Scene have the same known 16 P2 free-fall failures.
    # This constructor seam retains real structural checks and marks physics unproved;
    # evaluation, recordings, human authority and the deployed Newton Source are actual.
    app = create_app(
        source_factory=counted_source,
        controller_publication_validator=ControlledPublicationValidator(
            tmp_path / "controlled-publication-validation", program_schemas
        ),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://owned") as http:
            manager = app.state.manager
            controller_before = app.state.robot_resources.controller("fr3-arm")
            refused = await http.post(
                "/api/v1/controller-promotions",
                json={"subject": program["id"], "scene": scene.id, "planId": plan["id"]},
            )
            assert refused.status_code == 409, refused.text
            assert refused.json()["detail"]["code"] == "candidate_not_accepted"
            assert not starts and not manager.evaluations.store.records
            assert not list(app.state.controller_promotions.root.glob("promotion-*.json"))
            assert app.state.robot_resources.controller("fr3-arm") == controller_before
            teacher = await evaluate(http, suite, "robot_arm.newton", plan=plan)
            script = await evaluate(http, suite, program["id"], plan=plan)
            assert teacher["result"]["successes"] == 0 and script["result"]["successes"] == 1
            assert script["result"]["decision"] == "accepted"
            response = await http.post(
                "/api/v1/controller-promotions",
                json={
                    "subject": program["id"],
                    "scene": scene.id,
                    "evaluationId": script["evaluationId"],
                    "planId": plan["id"],
                    "planRevision": 1,
                    "requestId": "actual-script-permission",
                },
            )
            assert response.status_code == 202, response.text
            promotion = response.json()
            deadline = time.monotonic() + 120
            while True:
                response = await http.get("/api/v1/controller-promotions/" + promotion["id"])
                assert response.status_code == 200, response.text
                promotion = response.json()
                if promotion.get("approvalId"):
                    break
                assert promotion["status"] not in ("failed", "inconclusive", "cleanup_blocked"), promotion
                assert time.monotonic() < deadline, promotion
                await asyncio.sleep(0.05)
            assert "test" not in promotion and "candidateBelowReference" not in promotion
            transaction = app.state.controller_promotions.get(promotion["id"])
            for identifier in transaction["runs"].values():
                assert (await http.get("/api/v1/evaluations/" + identifier)).status_code == 404
            private_review = "/api/v1/controller-promotions/" + promotion["id"] + "/review"
            denied = await http.get(private_review, headers={"X-Actor": "agent:optimizer"})
            assert denied.status_code == 403
            assert (await http.get(private_review, headers={"X-Actor": "user:reviewer"})).status_code == 200
            decision = "/api/v1/approvals/" + promotion["approvalId"] + "/decision"
            assert (
                await http.post(
                    decision, json={"decision": "approve"}, headers={"X-Actor": "agent:optimizer"}
                )
            ).status_code == 403
            approved = await http.post(
                decision,
                json={"decision": "approve", "note": "Engineering permissions only"},
                headers={"X-Actor": "user:reviewer"},
            )
            assert approved.status_code == 200, approved.text
            published = approved.json()
            assert published["status"] == "published", published
            deployed = published["deployment"]
            assert deployed["scene"]["id"] != scene.id
            controller = deployed["controller"]["document"]
            assert controller["programArtifact"] == body["program"]
            assert controller["program"] == "robot_arm.script" and controller["policy"] is None
            assert controller["task"] == plan["task"]
            assert controller["action"]["hz"] is None and controller["action"]["hzUnknownReason"]
            readback = await http.get("/api/v1/controllers/fr3-arm")
            assert readback.status_code == 200, readback.text
            assert readback.json()["current"]["programArtifact"] == body["program"]
            revoked = await http.post(
                "/api/v1/approvals/" + plan["approvalId"] + "/decision",
                json={"decision": "revoke"},
                headers={"X-Actor": "user:reviewer"},
            )
            assert revoked.status_code == 200, revoked.text
            assert (await http.get("/api/v1/plans/" + plan["id"])).json()["status"] == "draft"
            starts_before = len(starts)
            stale = await http.post(
                "/api/v1/evaluations",
                json={"subject": program["id"], "suite": suite.id, "planId": plan["id"], "planRevision": 1},
            )
            assert stale.status_code == 409, stale.text
            assert stale.json()["detail"]["code"] == "program_not_approved"
            assert len(starts) == starts_before
            launched = await http.post(
                "/api/v1/sessions",
                json={"sceneVersion": deployed["scene"]["id"], "source": "newton", "device": "cpu"},
            )
            assert launched.status_code == 201, launched.text
            identity = launched.json()["sessionId"]
            try:
                session = app.state.manager.sessions[identity]
                execution = await session.source.program_execution("/World/Lab/RobotA")
                assert execution["program"] == program["id"]
                assert execution["programContentHash"] == program["contentHash"]
                assert execution["callbacks"]["init"] == 1 and execution["callbackErrors"] == 0
            finally:
                assert (await http.delete("/api/v1/sessions/" + identity)).status_code in (200, 204)
            assert all(not value.holds_resources for value in app.state.manager.sessions.values())
            assert not manager.jobs.records
            record_property(
                "script_permission_promotion",
                json.dumps(
                    {
                        "teacher": teacher["evaluationId"],
                        "script": script["evaluationId"],
                        "promotion": promotion["id"],
                        "approvalResult": published,
                        "deployedExecution": execution,
                        "trainingPlanRevoked": True,
                        "committedSceneStillLoadable": True,
                        "ownedCleanupComplete": True,
                        "unacceptedPromotionSourceStarts": 0,
                        "revokedEvaluationSourceStarts": 0,
                        "basis": "gripper-only engineering fixture",
                        "publicationPhysics": "controlled constructor; physical stability unproved",
                    },
                    sort_keys=True,
                ),
            )
