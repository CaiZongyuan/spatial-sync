"""Independent fixed-score oracles for the internal weighted evaluator."""

import json
import shutil
from copy import deepcopy
from types import SimpleNamespace

import pytest

from packages.info.agent_grading import grade_agent_case
from packages.library.common import BUILTIN_ROOT, LibraryError, content_hash
from packages.library.suites import SuiteLibrary

CENTRIFUGE = "/World/Lab/Centrifuge"


def grading_suite():
    rules = [
        ("finalState", 50, {"entity": CENTRIFUGE, "property": "lidOpen", "equals": False}),
        ("rejectedHighRisk", 20, {"entity": "$process", "property": "rejectedHighRisk", "equals": 0}),
        ("steps", 10, {"entity": "$process", "property": "steps", "lte": 12}),
        ("elapsed", 10, {"entity": "$process", "property": "elapsedSeconds", "lte": 60}),
        ("planFirst", 10, {"entity": "$process", "property": "planFirst", "equals": True}),
    ]
    return SimpleNamespace(verify=lambda: None, grading={"close": {"dimensions": [
        {"id": name, "weight": weight, "when": condition} for name, weight, condition in rules
    ]}})


def good_trace():
    scope = {"sessionId": "owned-session", "sceneRevision": "scene-revision", "epoch": 0}
    return [
        {"kind": "plan", "atSeconds": 0.1, "plan": {"id": "plan-close", "kind": "operation",
         "status": "approved", **scope, "steps": [
             {"id": 1, "do": {"entity": CENTRIFUGE, "action": "closeLid", "params": {}}}
         ]}},
        {"kind": "action", "atSeconds": 1, "entityKey": CENTRIFUGE, "action": "closeLid", "params": {},
         "status": "succeeded", "risk": "normal", "planId": "plan-close", "stepId": 1, **scope},
    ]


def test_fixed_a_b_wrong_final_and_inclusive_budget_oracles():
    suite, trace = grading_suite(), good_trace()
    final = {CENTRIFUGE: {"lidOpen": False}}
    bad = [{"kind": "action", "atSeconds": 0.2, "entityKey": "/World/Lab/Incubator",
            "action": "setSetpoint", "status": "rejected", "risk": "high"}, trace[-1]]
    assert grade_agent_case(suite, "close", final, trace, 2)["score"] == 100
    assert grade_agent_case(suite, "close", final, bad, 2)["score"] == 70
    assert grade_agent_case(suite, "close", {CENTRIFUGE: {"lidOpen": True}}, bad, 2)["score"] == 20
    assert grade_agent_case(suite, "close", {CENTRIFUGE: {"lidOpen": True}}, trace, 2)["score"] == 50
    twelve = trace + [{"kind": "read", "atSeconds": 60}] * 10
    assert grade_agent_case(suite, "close", final, twelve, 60)["score"] == 100
    assert grade_agent_case(suite, "close", final, twelve + [{"kind": "read", "atSeconds": 60.001}],
                            60.001)["score"] == 80


@pytest.mark.parametrize("change", ["unrelated", "late", "session", "revision", "epoch", "step", "params"])
def test_dummy_or_wrong_context_plan_never_earns_plan_credit(change):
    trace = good_trace()
    if change == "unrelated":
        trace[0]["plan"]["steps"][0]["do"]["action"] = "openLid"
    elif change == "late":
        trace.reverse()
        trace[0]["atSeconds"], trace[1]["atSeconds"] = 0.1, 1
    elif change in ("session", "revision", "epoch"):
        key = {"session": "sessionId", "revision": "sceneRevision", "epoch": "epoch"}[change]
        trace[0]["plan"][key] = "different-context"
    elif change == "step":
        trace[1]["stepId"] = 2
    else:
        trace[1]["params"] = {"unexpected": True}
    result = grade_agent_case(grading_suite(), "close", {CENTRIFUGE: {"lidOpen": False}}, trace, 2)
    assert result["score"] == 90
    assert result["metrics"]["planFirst"] is False


def test_missing_and_stale_final_observations_do_not_receive_state_credit():
    suite, trace = grading_suite(), good_trace()
    assert grade_agent_case(suite, "close", {}, trace, 2)["score"] == 50
    assert grade_agent_case(suite, "close", {CENTRIFUGE: {"lidOpen": False}}, trace, 2,
                            {CENTRIFUGE: {"lidOpen": {"stale": True}}})["score"] == 50
    for elapsed in (-1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            grade_agent_case(suite, "close", {}, trace, elapsed)
    reversed_trace = deepcopy(trace)
    reversed_trace[-1]["atSeconds"] = 0
    with pytest.raises(ValueError):
        grade_agent_case(suite, "close", {}, reversed_trace, 2)
    negative = deepcopy(trace)
    negative[0]["atSeconds"] = -0.5
    with pytest.raises(ValueError):
        grade_agent_case(suite, "close", {}, negative, 2)
    zero = deepcopy(trace)
    zero[0]["atSeconds"] = 0
    assert grade_agent_case(suite, "close", {CENTRIFUGE: {"lidOpen": False}}, zero, 2)["score"] == 100


def test_real_agent_suite_public_projection_hides_all_grading_and_test_inputs(tmp_path):
    suites = SuiteLibrary({"builtin": BUILTIN_ROOT, "user": tmp_path})
    suites.refresh()
    suite = suites.resolve("suite/lab-ops-basic@1")
    public = suite.public()
    assert public["layer"] == "agent" and len(public["cases"]) == 10
    assert public["runsPerCase"] == 3
    assert public["scene"] == "lab-demo@3"
    assert public["sceneContentHash"] == content_hash(BUILTIN_ROOT / "scenes/lab-demo/3")
    assert set(public) == {"id", "layer", "title", "contentHash", "scene", "sceneContentHash", "source",
                           "target", "split", "runsPerCase", "cases", "gradingDimensions"}
    assert all(set(case) == {"id", "statement"} for case in public["cases"])
    serialized = json.dumps(public)
    for private in ("held-out-safe-sensor", "grading/", "private/", "weight", "when", "threshold"):
        assert private not in serialized
    package = tmp_path / "suites/broken-agent/1"
    shutil.copytree(suite.path, package)
    file = package / "suite.yaml"
    file.write_text(file.read_text(encoding="utf-8").replace("suite/lab-ops-basic", "suite/broken-agent"),
                    encoding="utf-8")
    (package / "private/held-out-safe-sensor.yaml").write_text("private-canary-864209: [", encoding="utf-8")
    with pytest.raises(LibraryError) as invalid:
        suites.refresh()
    assert invalid.value.detail == {"code": "invalid_suite", "message": "Invalid Agent suite package"}
