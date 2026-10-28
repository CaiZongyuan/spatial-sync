"""Owned browser fixture seeds real Synthetic grades and the production identity producer."""

import asyncio
import os
import time

import httpx
from fastapi import Request

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from tests.gateway.test_agent_promotions import accepted_round, action, private_work
from tests.gateway.test_agent_rsi import agent_plan, complete_scope
from tests.gateway.test_external_agent_evaluation import work

os.environ.pop("SPATIAL_CATALOG_METADATA_ROOT", None)
os.environ["SPATIAL_CATALOG_ROOT"] = ""
app = create_app(Settings(remote=False))
context = {}


def seed(url):
    with httpx.Client(base_url=url, timeout=40, trust_env=False) as http:
        plan, _, _ = accepted_round(http, app, varied_scores=True)
        response = http.post(
            "/api/v1/plans/" + plan["id"] + "/promotions", json={"revision": 1, "requestId": "ui-promotion"}
        )
        response.raise_for_status()
        promotion = response.json()
        path = "/api/v1/agent-promotions/" + promotion["id"]
        grant = http.post(path + "/controller-grant", json={"wallSeconds": 120}).json()
        headers = {"Authorization": "Bearer " + grant["token"]}
        for index in range(6):
            opaque = private_work(http, path, headers)
            native = "ui-private-" + opaque["work"]["caseRunId"]
            http.post(
                path + "/bind-native",
                headers=headers,
                json={"caseRunId": opaque["work"]["caseRunId"], "nativeSessionId": native},
            ).raise_for_status()
            item = private_work(http, path, headers)["work"]
            if index >= 3:
                assert (
                    action(http, item, "/World/Lab/Incubator", "setSetpoint", {"temperatureC": 45}).json()[
                        "status"
                    ]
                    == "rejected"
                )
            http.post(
                path + "/complete",
                headers=headers,
                json={"caseRunId": item["caseRunId"], "nativeSessionId": native},
            ).raise_for_status()
        for _ in range(100):
            card = http.get(path + "/review").json()
            if card["status"] == "waiting-human":
                break
            time.sleep(0.01)
        assert card["candidateBelowReference"]
        context.update(plan=plan, promotion=promotion)
        return {
            "planId": plan["id"],
            "goalId": plan["goalId"],
            "agentId": plan["agentId"],
            "promotionId": promotion["id"],
            "approvalId": card["approvalId"],
            "acceptedVersion": 2,
        }


def next_round(http, suffix):
    first = context["plan"]
    agent = http.get("/api/v1/agents/lab-operator").json()
    reference = next(
        value
        for value in app.state.agent_rsi.baselines.values()
        if value["harnessVersion"] == agent["referenceVersion"]
    )
    baseline = app.state.agent_rsi.public_baseline(reference)
    files = http.get("/api/v1/agents/lab-operator/harness/" + str(agent["referenceVersion"])).json()["files"]
    next(file for file in files if file["path"] == "AGENTS.md")["content"] += (
        "\n\nControlled " + suffix + " guidance.\n"
    )
    candidate = http.post(
        "/api/v1/agents/lab-operator/candidates",
        json={
            "requestId": "ui-candidate-" + suffix,
            "referenceVersion": agent["referenceVersion"],
            "files": files,
            "change": first["change"],
        },
    ).json()
    payload = agent_plan(baseline, candidate) | {
        "referenceVersion": agent["referenceVersion"],
        "runtime": first["runtime"],
        "previous": first["id"],
        "title": "Controlled " + suffix + " round",
    }
    plan = http.post("/api/v1/plans", json=payload).json()
    http.post(
        "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "approve"}
    ).raise_for_status()
    response = http.post(
        "/api/v1/plans/" + plan["id"] + "/agent-runs", json={"revision": 1, "requestId": "ui-round-" + suffix}
    )
    response.raise_for_status()
    return plan, response.json()


def reject(url):
    with httpx.Client(base_url=url, timeout=40, trust_env=False) as http:
        plan, run = next_round(http, "rejected")
        complete_scope(http, run)
        settled = http.post("/api/v1/plans/" + plan["id"] + "/settle", json={"revision": 1})
        settled.raise_for_status()
        assert settled.json()["result"]["decision"] == "rejected"
        return {"planId": plan["id"], "version": plan["candidateVersion"]}


def contaminate(url):
    with httpx.Client(base_url=url, timeout=40, trust_env=False) as http:
        plan, run = next_round(http, "contaminated")
        work(http, run)
        grant = http.post(
            "/api/v1/agents/lab-operator/optimizer-grant",
            json={
                "referenceEvaluationId": plan["referenceEvaluationId"],
                "goalId": plan["goalId"],
                "nativeSessionId": "ui-exposed-optimizer",
                "requestId": "ui-exposure",
                "wallSeconds": 60,
            },
        ).json()
        files = http.get("/api/v1/agents/lab-operator/harness/2").json()["files"]
        suite = app.state.agent_rsi.manager.evaluations.suites.resolve(plan["suite"])
        next(file for file in files if file["path"] == "AGENTS.md")["content"] += (
            "\n" + suite.statements["held-out-safe-sensor"]
        )
        response = http.post(
            "/api/v1/agents/lab-operator/candidates",
            headers={"Authorization": "Bearer " + grant["token"]},
            json={
                "referenceVersion": 2,
                "requestId": "ui-exposed-input",
                "files": files,
                "change": plan["change"],
            },
        )
        assert (
            response.status_code == 409 and response.json()["detail"]["code"] == "agent_workflow_contaminated"
        )
        saved = http.get("/api/v1/plans/" + plan["id"]).json()
        assert saved["stop"]["application"] == "complete"
        return {"planId": plan["id"], "version": plan["candidateVersion"]}


def late_contaminate(url):
    with httpx.Client(base_url=url, timeout=40, trust_env=False) as http:
        plan, run = next_round(http, "late-accepted")
        for _ in range(3):
            item = work(http, run)
            assert action(http, item, "/World/Lab/RobotA", "home").json()["status"] == "succeeded"
            assert action(http, item, "/World/Lab/Centrifuge", "closeLid").json()["status"] == "succeeded"
            http.post(
                "/api/v1/agent-runs/" + run["id"] + "/complete",
                json={"caseRunId": item["caseRunId"], "nativeSessionId": "ui-late-" + item["caseRunId"]},
            ).raise_for_status()
        for _ in range(100):
            if http.get("/api/v1/agent-runs/" + run["id"]).json()["status"] == "completed":
                break
            time.sleep(0.01)
        settled = http.post("/api/v1/plans/" + plan["id"] + "/settle", json={"revision": 1})
        settled.raise_for_status()
        assert settled.json()["result"]["decision"] == "accepted"
        grant = http.post(
            "/api/v1/agents/lab-operator/optimizer-grant",
            json={
                "referenceEvaluationId": plan["referenceEvaluationId"],
                "goalId": plan["goalId"],
                "nativeSessionId": "ui-late-exposed-optimizer",
                "requestId": "ui-late-exposure",
                "wallSeconds": 60,
            },
        ).json()
        files = http.get("/api/v1/agents/lab-operator/harness/2").json()["files"]
        suite = app.state.agent_rsi.manager.evaluations.suites.resolve(plan["suite"])
        next(file for file in files if file["path"] == "AGENTS.md")["content"] += (
            "\n" + suite.statements["held-out-safe-sensor"]
        )
        exposed = http.post(
            "/api/v1/agents/lab-operator/candidates",
            headers={"Authorization": "Bearer " + grant["token"]},
            json={
                "referenceVersion": 2,
                "requestId": "ui-late-input",
                "files": files,
                "change": plan["change"],
            },
        )
        assert exposed.status_code == 409
        entry = http.get("/api/v1/notebooks/" + plan["goalId"]).json()["entries"][-1]
        assert entry["reportedDecision"] == "accepted" and entry["currentDisposition"] == "contaminated"
        assert entry["comparison"]["remainingGapReduction"] is None
        return {"planId": plan["id"], "version": plan["candidateVersion"]}


@app.post("/e2e/agent-rounds")
async def seed_rounds(request: Request):
    return await asyncio.to_thread(seed, str(request.base_url).rstrip("/"))


@app.post("/e2e/agent-rejected")
async def rejected_round(request: Request):
    return await asyncio.to_thread(reject, str(request.base_url).rstrip("/"))


@app.post("/e2e/promotion-publication-fault")
async def publication_fault():
    from packages.info.plans import PlanError

    service = app.state.agent_rsi
    write, armed = service._write, True

    def interrupted(**values):
        nonlocal armed
        if armed and any(item["status"] == "promoted" for item in values.get("promotions", {}).values()):
            armed = False
            raise PlanError("agent_rsi_unavailable", "Controlled publication interruption", status=503)
        return write(**values)

    service._write = interrupted
    return {"armed": True}


@app.post("/e2e/agent-contaminated")
async def contaminated_round(request: Request):
    return await asyncio.to_thread(contaminate, str(request.base_url).rstrip("/"))


@app.post("/e2e/agent-late-contaminated")
async def late_contaminated_round(request: Request):
    return await asyncio.to_thread(late_contaminate, str(request.base_url).rstrip("/"))


@app.post("/e2e/agent-reference-invalid")
async def invalidate_reference():
    service = app.state.agent_rsi
    version = service.agents.show("lab-operator")["referenceVersion"]
    baseline = next(value for value in service.baselines.values() if value["harnessVersion"] == version)
    run = service.external.get(baseline["evaluationId"])
    run["identitySnapshot"]["files"]["packages/agent_rsi/workflow.py"] = "0" * 64
    return {"version": version, "recordedScore": baseline["score"]}


@app.get("/e2e/storage-roots")
async def roots():
    settings = app.state.settings
    return {
        "state": str(settings.state_root.resolve()),
        "library": str(app.state.agents.user_root.resolve()),
        "recordings": str(settings.recordings_root.resolve()),
    }
