"""Only public train snapshots become Markdown; human changes retain immutable evidence."""

import json

import pytest
import yaml
from pydantic import ValidationError

from packages.info.evaluations import wilson_interval
from packages.info.plans import PlanError
from packages.protocol.schema import Actor
from packages.training.notebook_contract import NotebookEntry
from packages.training.notebook_rules import judge_hypothesis
from packages.training.notebooks import LabNotebooks


def entry():
    hypothesis = {
        "id": "H1",
        "cause": "Guidance is weak",
        "change": "Change guidance",
        "predict": {"metric": "success", "to": ">= 0.9"},
    }
    return {
        "schemaVersion": 1,
        "entryId": "entry-" + "a" * 32,
        "evaluationId": "eval-" + "a" * 32,
        "goalId": "goal/probe",
        "split": "train",
        "plan": {
            "id": "plan-a",
            "revision": 1,
            "task": "task/probe@1",
            "goalId": "goal/probe",
            "goal": "Improve the result",
            "title": "Controlled notebook fixture",
            "changedFields": ["task"],
        },
        "suite": "suite/probe@1",
        "subject": "virtual.robot",
        "candidateVersion": "candidate-public-code",
        "status": "completed",
        "at": "2026-10-24T00:00:00Z",
        "endedAt": "2026-10-24T00:00:01Z",
        "score": {
            "successes": 10,
            "episodes": 10,
            "score": 1.0,
            "ci95": wilson_interval(10, 10),
            "phases": {},
        },
        "partialEpisodes": [],
        "attemptedEpisodes": 10,
        "reportedDecision": "rejected",
        "comparison": {
            "state": "comparable",
            "reason": None,
            "conditionsGroup": "eval-" + "b" * 32,
            "referenceEvaluationId": "eval-" + "b" * 32,
            "referenceScore": 0.97,
            "remainingGapReduction": 1.0,
            "gapReason": None,
        },
        "deployment": {"status": "not_requested"},
        "hypotheses": [
            {
                "definition": hypothesis,
                "automatic": judge_hypothesis(
                    hypothesis,
                    {
                        "split": "train",
                        "successes": 10,
                        "episodes": 10,
                    },
                ),
                "overrides": [],
            }
        ],
        "recordings": [],
        "errorCode": None,
        "knownJobGpuHours": 0.0,
        "costCoverage": "Only known Job high-water usage; no quality claim",
    }


@pytest.fixture
def store(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    notebook = LabNotebooks(tmp_path / "state", library)
    notebook.startup()
    return notebook


def test_auto_snapshot_is_immutable_and_human_override_records_actor_without_accepting_or_deploying(store):
    original = store.add(entry())
    changed = store.override(
        "goal/probe",
        original["entryId"],
        "H1",
        "refuted",
        Actor(kind="user", name="reviewer"),
        "Insufficient causal support",
        "override-1",
    )
    item = changed["hypotheses"][0]
    assert item["automatic"]["verdict"] == "confirmed" and item["effectiveVerdict"] == "refuted"
    assert item["overrides"][0]["actor"] == {"kind": "user", "name": "reviewer"}
    assert changed["reportedDecision"] == "rejected" and changed["deployment"]["status"] == "not_requested"
    replayed = store.add(entry())
    assert replayed == changed  # Evaluation reconciliation cannot overwrite a human audit.
    assert store.list("goal/probe", refuted=True) == [changed]
    restarted = LabNotebooks(store.path.parent, store.library_root)
    restarted.startup()
    assert restarted.list("goal/probe") == [changed]
    markdown = next((store.library_root / "notebooks/probe").glob("*.md")).read_text(encoding="utf-8")
    front = yaml.safe_load(markdown.split("---\n", 2)[1])
    assert front["hypotheses"][0]["effectiveVerdict"] == "refuted"
    assert front["reportedDecision"] == "rejected"


def test_test_or_extra_private_material_is_rejected_before_any_projection_write(store):
    private = entry() | {"split": "test", "privateSeed": "NEVER-PUBLISH-42"}
    with pytest.raises(ValidationError):
        store.add(private)
    assert not store.entries and not store.path.exists()
    assert not (store.library_root / "notebooks").exists()
    nested = entry()
    nested["score"]["privateConditions"] = {"secret": "NEVER-PUBLISH-42"}
    with pytest.raises(ValidationError):
        NotebookEntry.model_validate(nested)


def test_failed_canonical_write_does_not_publish_or_grant_a_manual_change(store, monkeypatch):
    original = store.add(entry())

    def fail(*args):
        raise OSError("owned injected journal failure")

    monkeypatch.setattr("packages.training.notebooks.durable_json", fail)
    with pytest.raises(OSError):
        store.override("goal/probe", original["entryId"], "H1", "refuted", Actor(), "Reason", "request")
    assert store.document(original["entryId"]) == original
    assert len(json.loads(store.path.read_text())["entries"]) == 1


def test_projection_failure_after_commit_recovers_and_retry_does_not_duplicate_the_audit(store, monkeypatch):
    original = store.add(entry())
    publish = store.publish
    with monkeypatch.context() as patch:
        patch.setattr(
            store, "publish", lambda identity: (_ for _ in ()).throw(OSError("owned projection failure"))
        )
        with pytest.raises(OSError):
            store.override("goal/probe", original["entryId"], "H1", "refuted", Actor(), "Reason", "request")
    result = store.override("goal/probe", original["entryId"], "H1", "refuted", Actor(), "Reason", "request")
    assert len(result["hypotheses"][0]["overrides"]) == 1
    assert store.publish == publish


def test_agent_override_is_refused_at_the_domain_boundary(store):
    original = store.add(entry())
    with pytest.raises(PlanError) as refused:
        store.override(
            "goal/probe",
            original["entryId"],
            "H1",
            "confirmed",
            Actor(kind="agent", name="optimizer"),
            "Self approval",
            "request",
        )
    assert refused.value.status == 403
    assert not store.document(original["entryId"])["hypotheses"][0]["overrides"]
