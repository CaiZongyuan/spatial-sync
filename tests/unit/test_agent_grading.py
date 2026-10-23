"""Independent fixed-score oracles for the internal weighted evaluator."""

import json
import shutil
from copy import deepcopy
from types import SimpleNamespace

import pytest
import yaml

from packages.info.agent_grading import final_observations, grade_agent_case
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


@pytest.mark.parametrize("revision", ["0", "-1", "01", "+1", "１", "1.0"])
def test_actual_agent_suite_index_rejects_nonpositive_or_noncanonical_revisions(tmp_path, revision):
    shutil.copytree(BUILTIN_ROOT / "suites/lab-ops-basic/1", tmp_path / "suites/lab-ops-basic" / revision)
    suites = SuiteLibrary({"user": tmp_path})
    with pytest.raises(LibraryError) as rejected:
        suites.refresh()
    assert rejected.value.detail["code"] == "invalid_suite"
    assert suites.versions == {}


def test_actual_agent_suite_index_keeps_positive_versions_readable(tmp_path):
    for revision in (1, 2):
        shutil.copytree(BUILTIN_ROOT / "suites/lab-ops-basic/1",
                        tmp_path / "suites/lab-ops-basic" / str(revision))
    suites = SuiteLibrary({"user": tmp_path})
    suites.refresh()
    assert [item["id"] for item in suites.list()] == ["suite/lab-ops-basic@1", "suite/lab-ops-basic@2"]
    assert len(suites.resolve("suite/lab-ops-basic@2").public()["cases"]) == 10


def action_evidence(event, value):
    return {"scope": {key: event[key] for key in ("sessionId", "sceneRevision", "epoch")}, "state": {
        "entities": {CENTRIFUGE: {"values": {"lidOpen": value}, "meta": {"lidOpen": {"stale": False}}}}
    }}


def witness():
    return {"entity": CENTRIFUGE, "action": "closeLid", "status": "succeeded", "params": {},
            "before": {"entity": CENTRIFUGE, "property": "lidOpen", "equals": True},
            "after": {"entity": CENTRIFUGE, "property": "lidOpen", "equals": False}}


def witnessed_trace():
    trace = good_trace()
    trace[-1]["before"] = action_evidence(trace[-1], True)
    trace[-1]["after"] = action_evidence(trace[-1], False)
    return trace


def witnessed_final():
    return {CENTRIFUGE: {"lidOpen": False}, "$session": {
        "sessionId": "owned-session", "sceneRevision": "scene-revision", "epoch": 0,
    }}


@pytest.mark.parametrize("fault", ["none", "missing", "malformed", "session", "revision", "epoch",
                                   "stale", "missing-meta", "params", "duplicate", "foreign-consistent",
                                   "missing-final-scope"])
def test_action_witness_credit_requires_distinct_fresh_matching_scope_and_observations(fault):
    suite, trace = grading_suite(), witnessed_trace()
    action = witness()
    suite.grading["close"]["dimensions"][0]["actions"] = [action]
    if fault == "missing":
        trace[-1].pop("after")
    elif fault == "malformed":
        trace[-1]["after"] = None
    elif fault in ("session", "revision", "epoch"):
        key = {"session": "sessionId", "revision": "sceneRevision", "epoch": "epoch"}[fault]
        trace[-1]["before"]["scope"][key] = "another-context"
    elif fault == "stale":
        trace[-1]["before"]["state"]["entities"][CENTRIFUGE]["meta"]["lidOpen"]["stale"] = True
    elif fault == "missing-meta":
        trace[-1]["before"]["state"]["entities"][CENTRIFUGE]["meta"] = {}
    elif fault == "params":
        action["params"] = {"required": 1}
    elif fault == "duplicate":
        suite.grading["close"]["dimensions"][0]["actions"].append(deepcopy(action))
    elif fault == "foreign-consistent":
        trace[-1]["sessionId"] = "another-session"
        trace[-1]["before"]["scope"]["sessionId"] = "another-session"
        trace[-1]["after"]["scope"]["sessionId"] = "another-session"
    final = witnessed_final()
    if fault == "missing-final-scope":
        final.pop("$session")
    result = grade_agent_case(suite, "close", final, trace, 2)
    assert result["dimensions"]["finalState"] == (50 if fault == "none" else 0)


def test_ordered_witnesses_consume_different_events_and_allow_declared_parameter_subset():
    suite, trace = grading_suite(), witnessed_trace()
    first = witness()
    first["params"] = {"rpm": 500}
    first["action"] = trace[-1]["action"] = "start"
    first["before"]["equals"] = False
    trace[-1]["before"] = action_evidence(trace[-1], False)
    trace[-1]["params"] = {"rpm": 500, "durationSeconds": 1}
    second = deepcopy(first) | {"action": "stop", "params": {}}
    later = deepcopy(trace[-1]) | {"action": "stop", "params": {}, "atSeconds": 1.5}
    suite.grading["close"]["dimensions"][0]["actions"] = [first, second]
    valid = trace + [later]
    score = grade_agent_case(suite, "close", witnessed_final(), valid, 2)
    assert score["dimensions"]["finalState"] == 50
    reordered = [trace[0], later | {"atSeconds": 0.5}, trace[-1]]
    score = grade_agent_case(suite, "close", witnessed_final(), reordered, 2)
    assert score["dimensions"]["finalState"] == 0
    mixed = deepcopy(valid)
    mixed[-1]["epoch"] = 1
    for stage in ("before", "after"):
        mixed[-1][stage]["scope"]["epoch"] = 1
    score = grade_agent_case(suite, "close", witnessed_final(), mixed, 2)
    assert score["dimensions"]["finalState"] == 0


@pytest.mark.parametrize("invalid", [[], {}, [{"entity": "/Wrong"}], [witness() | {"status": "accepted"}],
                                      [witness() | {"params": "not-a-map"}],
                                      [witness() | {"before": {"property": "lidOpen", "equals": True}}]])
def test_private_action_schema_rejects_invalid_evidence_requirements_without_disclosure(tmp_path, invalid):
    package = tmp_path / "suites/lab-ops-basic/1"
    shutil.copytree(BUILTIN_ROOT / "suites/lab-ops-basic/1", package)
    file = package / "private/return-sample.yaml"
    rubric = yaml.safe_load(file.read_text(encoding="utf-8"))
    rubric["dimensions"][0]["actions"] = invalid
    file.write_text(yaml.safe_dump(rubric), encoding="utf-8")
    with pytest.raises(LibraryError) as rejected:
        SuiteLibrary({"user": tmp_path}).refresh()
    assert rejected.value.detail == {"code": "invalid_suite", "message": "Invalid Agent suite package"}


@pytest.mark.parametrize("entity,rule,revision,epoch,session_id", [
    ("/World/Lab/BiosafetyCabinet", "lowInflow", "r", 0, "s"),
    ("/World/Lab/Incubator", "doorOpen", "r", 0, "s"),
    ("/World/Lab/Incubator", "tempDeviation", "old", 0, "s"),
    ("/World/Lab/Incubator", "tempDeviation", "r", 1, "s"),
    ("/World/Lab/Incubator", "tempDeviation", "r", 0, "other"),
])
def test_acknowledgement_evidence_keeps_rule_entity_and_current_context(
    entity, rule, revision, epoch, session_id
):
    values, _ = final_observations({"entities": {}}, {
        "state": "PAUSED", "sessionId": "s", "epoch": 0, "sceneRevision": "r"
    }, [
        {"entityKey": entity, "rule": rule, "sessionId": session_id,
         "sceneRevision": revision, "epoch": epoch,
         "acknowledgement": {"actor": {"kind": "agent", "name": "scripted"}}}
    ])
    assert "acknowledged:/World/Lab/Incubator.tempDeviation" not in values["$alerts"]


@pytest.mark.parametrize("fault", ["none", "entity", "path", "response-key", "risk", "scope", "status"])
def test_inspection_credit_requires_actual_description_request_response_and_final_scope(fault):
    suite = grading_suite()
    suite.grading["close"]["dimensions"][0]["when"] = {"all": [
        {"entity": CENTRIFUGE, "property": "lidOpen", "equals": False},
        {"entity": "$process", "property": "described:/World/Lab/Incubator.setSetpoint.risk",
         "equals": "high"},
    ]}
    key = "/World/Lab/Incubator"
    event = {"kind": "read", "atSeconds": 0, "sessionId": "owned-session",
             "sceneRevision": "scene-revision", "epoch": 0, "statusCode": 200,
             "request": {"method": "GET", "path": "/api/v1/sessions/owned-session/entity",
                         "query": {"key": key}},
             "response": {"key": key, "definition": {"actions": {"setSetpoint": {"risk": "high"}}}}}
    if fault == "entity":
        event["request"]["query"]["key"] = event["response"]["key"] = "/World/Lab/Other"
    elif fault == "path":
        event["request"]["path"] = "/api/v1/sessions/owned-session/state"
    elif fault == "response-key":
        event["response"]["key"] = "/World/Lab/Other"
    elif fault == "risk":
        event["response"]["definition"]["actions"]["setSetpoint"]["risk"] = "normal"
    elif fault == "scope":
        event["sessionId"] = "foreign-session"
        event["request"]["path"] = "/api/v1/sessions/foreign-session/entity"
    elif fault == "status":
        event["statusCode"] = 404
    score = grade_agent_case(suite, "close", witnessed_final(), [event], 1)
    assert score["dimensions"]["finalState"] == (50 if fault == "none" else 0)
