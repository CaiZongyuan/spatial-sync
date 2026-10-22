"""Public evaluation projections and comparisons, without starting a spatial Source."""
import json
import re
from copy import deepcopy

import httpx
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.platform_monitor import platform_snapshot, unavailable_system
from apps.api.app.settings import Settings
from apps.cli.lw.client import GatewayClient
from apps.cli.lw.commands import COMMANDS

SUITE = "suite/lab-demo-pick-place@1"
ROUTE = "/api/v1/suites/lab-demo-pick-place/1"
CANARY = "DO-NOT-PUBLISH-GRADING-594621"


class NoHostProbes:
    def sample(self, rows):
        return platform_snapshot(rows, unavailable_system("Source-free evaluation view test"))


def no_source(*args):
    raise AssertionError("A public read must not create a Source")


def documents():
    key = {"suite": SUITE, "suiteContentHash": "a" * 64, "scene": "lab-demo@3",
           "sceneContentHash": "b" * 64, "source": "synthetic", "newtonVersion": None,
           "solver": None, "target": "/World/Lab/RobotA", "bindingHash": "c" * 64}
    row = {"evaluationId": "eval-public", "suite": SUITE, "subject": "robot_arm.virtual",
           "version": "d" * 64, "key": key, "split": "train", "successes": 1, "episodes": 2,
           "score": .5, "ci95": [.09, .91], "decision": "baseline", "reference": None,
           "phases": {"grasp": {"passed": 1, "episodes": 2, "rate": .5}},
           "recordings": ["session-public"], "durationSeconds": 2.5, "at": "2026-10-23T00:00:00Z"}
    record = {"evaluationId": "eval-public", "suite": SUITE, "suiteContentHash": "a" * 64,
              "subject": "robot_arm.virtual", "split": "train", "status": "completed",
              "runtimeHash": "d" * 64, "at": row["at"], "startedAt": row["at"], "endedAt": row["at"],
              "key": key, "episodes": [{"caseId": "train-one", "success": True,
                                        "milestones": {"grasp": True}, "failedStage": None,
                                        "invocationId": "inv-public", "invocationStatus": "succeeded",
                                        "recordingId": "session-public"}],
              "result": row, "error": {"code": "evaluation_failed"}}
    return deepcopy(row), deepcopy(record)


def app_for(tmp_path):
    return create_app(Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings"),
                      source_factory=no_source, platform_sampler=NoHostProbes())


def test_every_public_evaluation_read_keeps_normal_dto_and_excludes_nested_private_inputs(tmp_path):
    expected_row, expected_record = documents()
    row, record = deepcopy(expected_row), deepcopy(expected_record)
    row.update(privateGrading=CANARY, seeds={"test": CANARY}, provenance={"privateTask": CANARY})
    row["key"]["privateConditions"] = {"testSeed": CANARY}
    row["phases"]["grasp"]["when"] = {"equals": CANARY}
    row["recordings"].append({"privateSeed": CANARY})
    record.update(privateGrading=CANARY, provenance={"seeds": CANARY})
    record["key"]["privateConditions"] = {"testSeed": CANARY}
    record["episodes"][0].update(grading={"equals": CANARY}, seed=CANARY, testQuestion=CANARY)
    record["episodes"][0]["milestones"]["privateCondition"] = {"equals": CANARY}
    record["error"].update(message=CANARY, details={"test": CANARY})
    record["result"] = row
    with TestClient(app_for(tmp_path)) as client:
        service = client.app.state.manager.evaluations
        service.store.save(record)
        service.store.rows.append(row)
        service.store.save(record | {"evaluationId": "eval-held-out", "split": "test"})
        service.store.rows.append(row | {"evaluationId": "eval-held-out", "split": "test"})
        listed = client.get("/api/v1/evaluations").json()
        score = client.get(ROUTE + "/scoreboard").json()
        shown = client.get("/api/v1/evaluations/eval-public").json()
        cancelled = client.post("/api/v1/evaluations/eval-public/cancel").json()
        for value in [listed, score, shown, cancelled]:
            assert CANARY not in json.dumps(value)
            assert "eval-held-out" not in json.dumps(value)
        assert set(score) == {"rows"}
        public_row = score["rows"][0]
        assert re.fullmatch(r"[a-f0-9]{64}", public_row.pop("conditionsHash"))
        assert public_row == expected_row
        for value in [listed["evaluations"][0], shown, cancelled]:
            assert re.fullmatch(r"[a-f0-9]{64}", value["result"].pop("conditionsHash"))
            assert value == expected_record
        suite = client.get(ROUTE).json()
        assert [case["id"] for case in suite["episodes"]] == ["train-one", "train-two"]
        assert client.get("/api/v1/suites").json() == {"suites": [suite]}
        assert all(value not in json.dumps(suite) for value in ["held-out-example", "4101", "4102", "9101"])
        for method, suffix in [("get", ""), ("post", "/cancel"), ("post", "/cleanup")]:
            response = getattr(client, method)("/api/v1/evaluations/eval-held-out" + suffix)
            assert response.status_code == 404 and CANARY not in response.text
        for filename in ("task.yaml", "private/seeds.yaml"):
            assert client.get("/api/v1/library/files/builtin/suites/lab-demo-pick-place/1/" + filename).status_code == 404
        assert client.get("/api/v1/health").json()["sessionCount"] == 0


def test_scoreboard_comparison_requires_both_public_same_suite_ids_and_the_entire_key(tmp_path):
    first, _ = documents()
    second = deepcopy(first) | {"evaluationId": "eval-second", "score": .75}
    different = deepcopy(first) | {"evaluationId": "eval-different"}
    different["key"]["bindingHash"] = "e" * 64
    held = deepcopy(first) | {"evaluationId": "eval-test", "split": "test"}
    other = deepcopy(first) | {"evaluationId": "eval-other", "suite": "suite/other@1"}
    with TestClient(app_for(tmp_path)) as client:
        client.app.state.manager.evaluations.store.rows.extend([first, second, different, held, other])
        route = ROUTE + "/scoreboard"
        matched = client.get(route, params={"compare": "eval-second", "against": "eval-public"})
        assert matched.status_code == 200
        assert matched.json()["comparison"] == {"compare": "eval-second", "against": "eval-public", "scoreDelta": .25}
        mismatch = client.get(route, params={"compare": "eval-different", "against": "eval-public"})
        assert mismatch.status_code == 409 and mismatch.json()["detail"]["code"] == "not_comparable"
        for identifier in ("eval-missing", "eval-test", "eval-other"):
            result = client.get(route, params={"compare": identifier, "against": "eval-public"})
            assert result.status_code == 404 and result.json()["detail"] == {"code": "unknown_evaluation"}
        for parameter in ("compare", "against"):
            result = client.get(route, params={parameter: "eval-public"})
            assert result.status_code == 422 and result.json()["detail"]["code"] == "invalid_comparison"
        result = client.get(route, params={"subject": "another-program", "compare": "eval-public", "against": "eval-second"})
        assert result.status_code == 404
        forged = client.post("/api/v1/evaluations", headers={"X-Role": "builder", "X-Actor": "user:builder"},
                             json={"suite": SUITE, "subject": "robot_arm.virtual", "split": "test"})
        assert forged.status_code == 403 and forged.json()["detail"]["code"] == "test_requires_promotion"


async def test_existing_cli_and_mcp_shared_client_keeps_default_paths_payloads_and_subject_filter():
    row, record = documents()
    seen = []

    def respond(request):
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/scoreboard"):
            return httpx.Response(200, json={"rows": [row]})
        if request.url.path.endswith("/eval-public"):
            return httpx.Response(200, json=record)
        return httpx.Response(200, json={"evaluations": [record]})

    async with httpx.AsyncClient(base_url="http://evaluation.invalid", transport=httpx.MockTransport(respond)) as http:
        gateway = GatewayClient(http=http)
        assert await gateway.score_list(SUITE, subject="robot_arm.virtual") == [row]
        assert await gateway.evaluation_show("eval-public") == record
        assert await gateway.evaluations_list() == [record]
    assert seen == [(ROUTE + "/scoreboard", {"subject": "robot_arm.virtual"}),
                    ("/api/v1/evaluations/eval-public", {}), ("/api/v1/evaluations", {})]
    methods = {command.mcp_name: command.method for command in COMMANDS}
    assert methods["score_list"] == "score_list" and methods["evaluation_show"] == "evaluation_show"
