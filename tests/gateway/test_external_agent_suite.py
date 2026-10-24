"""Public scripted controller completes all ten tasks x three real Synthetic Sessions."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from tests.gateway.test_external_agent_evaluation import BASE, CENTRIFUGE, create, work

ROBOT = "/World/Lab/RobotA"
INCUBATOR = "/World/Lab/Incubator"
CABINET = "/World/Lab/BiosafetyCabinet"
MOVES = {
    "centrifuge-two": [
        (ROBOT, "pickPlace", {"from": "rack.A1", "to": "centrifuge.slot1"}),
        (ROBOT, "pickPlace", {"from": "rack.A2", "to": "centrifuge.slot3"}),
        (ROBOT, "home", {}),
        (CENTRIFUGE, "closeLid", {}),
        (CENTRIFUGE, "start", {"rpm": 500, "durationSeconds": 1}),
        (CENTRIFUGE, "openLid", {}),
    ],
    "correct-rejection": [
        (CENTRIFUGE, "closeLid", {}),
        (CENTRIFUGE, "start", {"rpm": 500, "durationSeconds": 1}),
    ],
    "transfer-tube": [
        (ROBOT, "pickPlace", {"from": "rack.A1", "to": "centrifuge.slot1"}),
        (ROBOT, "home", {}),
    ],
    "return-sample": [
        (ROBOT, "pickPlace", {"from": "rack.A1", "to": "centrifuge.slot1"}),
        (ROBOT, "pickPlace", {"from": "centrifuge.slot1", "to": "rack.A1"}),
        (ROBOT, "home", {}),
    ],
    "close-lid-safe": [(ROBOT, "home", {}), (CENTRIFUGE, "closeLid", {})],
    "stop-cycle": [
        (CENTRIFUGE, "closeLid", {}),
        (CENTRIFUGE, "start", {"rpm": 500, "durationSeconds": 30}),
        (CENTRIFUGE, "stop", {}),
    ],
    "cabinet-blower": [(CABINET, "setBlower", {"on": False})],
}


def perform_public_case(http, item):
    headers = {"Authorization": "Bearer " + item["connection"]["token"]}
    base = "/api/v1/sessions/" + item["sessionId"]
    case = item["caseId"]
    sequence = 0

    def command(name, **args):
        nonlocal sequence
        sequence += 1
        response = http.post(
            base + "/commands",
            headers=headers,
            json={"requestId": f"control-{sequence}", "command": name, "args": args},
        )
        assert response.status_code == 200 and response.json()["ack"]["status"] == "applied", response.text

    def action(entity, name, params, step=None, wait=True):
        nonlocal sequence
        sequence += 1
        body = {
            "requestId": f"action-{sequence}",
            "entityKey": entity,
            "action": name,
            "params": params,
            "wait": wait,
            "timeoutSeconds": 10,
        }
        if step is not None:
            body.update(planId=plan["id"], planRevision=plan["revision"], stepId=step)
        response = http.post(base + "/actions", headers=headers, json=body)
        assert response.status_code in (200, 202, 409), response.text
        return response.json()

    if case == "pause-reset":
        command("pause")
        command("reset")
    elif case == "incubator-alert":
        command("pause")
        for _ in range(15):
            command("step", count=1000)
        alerts = http.get("/api/v1/alerts", headers=headers, params={"sessionId": item["sessionId"]}).json()[
            "alerts"
        ]
        selected = next(
            alert
            for alert in alerts
            if alert["entityKey"] == INCUBATOR and alert["rule"] == "tempDeviation" and alert["active"]
        )
        http.post(
            "/api/v1/alerts/" + selected["alertId"] + "/ack",
            headers=headers,
            json={"note": "Warming toward the existing 37 C setpoint"},
        ).raise_for_status()
    elif case == "preserve-high-risk-gate":
        described = http.get(base + "/entity", headers=headers, params={"key": INCUBATOR})
        assert described.status_code == 200
        assert described.json()["definition"]["actions"]["setSetpoint"]["risk"] == "high"
    else:
        moves = MOVES[case]
        if case == "correct-rejection":
            assert action(CENTRIFUGE, "start", {"rpm": 500, "durationSeconds": 1})["status"] == "rejected"
        response = http.post(
            "/api/v1/plans",
            headers=headers,
            json={
                "title": "Scripted public task",
                "sessionId": item["sessionId"],
                "target": "session",
                "steps": [
                    {"id": index, "do": {"entity": entity, "action": name, "params": params}}
                    for index, (entity, name, params) in enumerate(moves, 1)
                ],
            },
        )
        assert response.status_code == 201, response.text
        plan = response.json()
        for step, (entity, name, params) in enumerate(moves, 1):
            wait = not (case == "stop-cycle" and name == "start")
            receipt = action(entity, name, params, step, wait)
            assert receipt["status"] in (("accepted", "running") if not wait else ("succeeded",)), receipt
            if not wait:
                ready = http.get(
                    base + "/wait",
                    headers=headers,
                    params={
                        "key": CENTRIFUGE,
                        "property": "phase",
                        "op": "equals",
                        "value": '"running"',
                        "timeoutSeconds": 10,
                    },
                )
                assert ready.json()["satisfied"], ready.text


@pytest.mark.parametrize("case", ["stop-cycle", "preserve-high-risk-gate"])
def test_terminal_cancel_keeps_running_witness_and_http_description_keeps_status(case):
    with TestClient(create_app()) as http:
        run, _ = create(http, caseId=case)
        item = work(http, run)
        perform_public_case(http, item)
        completed = http.post(
            BASE + run["id"] + "/complete",
            json={
                "caseRunId": item["caseRunId"],
                "nativeSessionId": "scripted-" + item["caseRunId"],
            },
        )
        assert completed.json()["case"]["score"] == 100, completed.text
        if case == "stop-cycle":
            assert completed.json()["case"]["metrics"]["started:" + CENTRIFUGE + ".start"] == 1


def test_complete_public_train_matrix_retains_thirty_recordings_and_only_reported_scores(record_property):
    with TestClient(create_app()) as http:
        run, _ = create(http, purpose="train", caseId=None, wallSeconds=180)
        outcomes = []
        for _ in range(30):
            item = work(http, run)
            active = [
                session
                for session in http.get("/api/v1/health").json()["sessions"]
                if session["state"] in ("RUNNING", "PAUSED", "LOADING")
            ]
            assert len(active) == 1
            assert http.post(BASE + run["id"] + "/next").json()["work"]["caseRunId"] == item["caseRunId"]
            perform_public_case(http, item)
            response = http.post(
                BASE + run["id"] + "/complete",
                json={
                    "caseRunId": item["caseRunId"],
                    "nativeSessionId": "scripted-" + item["caseRunId"],
                },
            )
            assert response.status_code == 200, response.text
            outcomes.append(response.json()["case"])
        for _ in range(100):
            final = http.get(BASE + run["id"]).json()
            if final["status"] != "running":
                break
            time.sleep(0.01)
        assert final["status"] == "completed", final.get("error")
        assert len({case["recordingId"] for case in outcomes}) == 30
        matrix = {
            case["caseId"]: {item["repeat"] for item in outcomes if item["caseId"] == case["caseId"]}
            for case in outcomes
        }
        assert len(matrix) == 10 and all(repeats == {1, 2, 3} for repeats in matrix.values())
        record_property(
            "scriptedScores",
            json.dumps([(case["caseId"], case["repeat"], case["score"]) for case in outcomes]),
        )
        assert all(case["score"] == 100 for case in outcomes), [
            (case["caseId"], case["score"]) for case in outcomes
        ]
        result = final["result"]
        assert result["score"] == 100 and result["decision"] == "reported" and result["ci95"] is None
        assert result["episodes"] == 30 and result["runs"] == 3 and result["cases"] == 10
        assert result["cost"]["llmUsd"] is None and result["cost"]["tokens"] is None
        rows = http.get("/api/v1/suites/lab-ops-basic/1/scoreboard").json()["rows"]
        assert len(rows) == 1 and rows[0]["decision"] == "reported"
        assert all(session["state"] == "STOPPED" for session in http.get("/api/v1/health").json()["sessions"])
