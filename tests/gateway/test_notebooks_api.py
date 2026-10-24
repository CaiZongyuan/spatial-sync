"""Real evaluator receipts publish train-only Notebooks with independent human audit."""

import asyncio
import json
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.info.evaluations import evaluation_decision, wilson_interval
from packages.library.common import BUILTIN_ROOT
from tests.fixtures.training_task import task_archive, task_directory, training_plan

SUITE = "suite/lab-demo-pick-place@1"
CANARY = "DO-NOT-PUBLISH-PRIVATE-GRADE-62419"


def assert_public(value):
    if isinstance(value, dict):
        assert not (
            {
                "seed",
                "seeds",
                "privateSeed",
                "privateSeeds",
                "privateConditions",
                "notebookContext",
                "comparisonInputs",
                "suiteContentHash",
            }
            & set(value)
        )
        for item in value.values():
            assert_public(item)
    elif isinstance(value, list):
        for item in value:
            assert_public(item)
    elif type(value) is int:
        assert value not in (4101, 4102, 9101)
    elif isinstance(value, str):
        assert value not in ("4101", "4102", "9101", "held-out-example")
        assert CANARY not in value


def prepare(client, directory, *, episodes=2):
    package = task_directory(directory, "notebook-probe")
    path = package / "task.yaml"
    task = yaml.safe_load(path.read_text())
    task["scene"] = task["evaluation"]["scene"] = "lab-demo@3"
    path.write_text(yaml.safe_dump(task))
    response = client.post(
        "/api/v1/tasks", content=task_archive(package), headers={"Content-Type": "application/x-tar"}
    )
    assert response.status_code == 201, response.text
    task = response.json()
    body = training_plan(
        task["id"],
        budget={"gpuHours": 0.0, "episodes": episodes},
        evaluation={"scene": "lab-demo@3", "episodes": episodes, "threshold": episodes},
        hypotheses=[
            {
                "id": "H1",
                "cause": "Need a controlled platform result",
                "change": "Test public receipt",
                "predict": {"metric": "success", "to": ">= 0.8"},
            }
        ],
    )
    response = client.post("/api/v1/plans", json=body)
    assert response.status_code == 201, response.text
    plan = response.json()
    assert (
        client.post(
            f"/api/v1/approvals/{plan['approvalId']}/decision", json={"decision": "approve"}
        ).status_code
        == 200
    )
    return plan


def until(read, predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(0.01)
    raise AssertionError(value)


def controlled_receipt(
    app, plan, *, successes=2, reference=None, split="train", identifier=None, legacy=False
):
    """Owned fixture adapter; actual stores/decision function/projection, no physics claim."""
    service = app.state.manager.evaluations
    body = type("Request", (), {"planId": plan["id"], "planRevision": plan["revision"]})()
    suite = service.suites.resolve(SUITE)
    scene = app.state.manager.library.resolve(suite.metadata["scene"])
    context = app.state.notebooks.prepare_evaluation(body, suite, scene)
    identifier = identifier or "eval-" + uuid.uuid4().hex
    score = successes / 2
    identity = {name: context["implementations"][name] for name in ("environment", "judge")}
    key = {
        "suite": SUITE,
        "suiteContentHash": suite.content_hash,
        "scene": scene.id,
        "sceneContentHash": scene.content_hash,
        "source": "synthetic",
        "target": suite.task["robot"],
        "bindingHash": "a" * 64,
        "newtonVersion": None,
        "solver": None,
        **identity,
    }
    row = {
        "evaluationId": identifier,
        "suite": SUITE,
        "subject": "robot_arm.virtual",
        "version": "v1",
        "key": key,
        "comparisonIdentity": identity,
        "split": split,
        "successes": successes,
        "episodes": 2,
        "score": score,
        "ci95": wilson_interval(successes, 2),
        "decision": evaluation_decision(successes, 2, reference["score"] if reference else None, split=split),
        "reference": reference["evaluationId"] if reference else None,
        "phases": {},
        "recordings": [],
        "durationSeconds": 1.0,
        "at": "2026-10-24T00:00:00Z",
    }
    record = {
        "evaluationId": identifier,
        "suite": SUITE,
        "subject": "robot_arm.virtual",
        "split": split,
        "status": "completed",
        "at": row["at"],
        "endedAt": row["at"],
        "key": key,
        "comparisonIdentity": identity,
        "candidateIdentity": {
            "kind": "owned-fixture",
            "contentHash": context["implementations"]["candidate"],
        },
        "result": row,
        "episodes": [],
        "attemptedEpisodes": 2,
        "allocatedEpisodes": 2,
        "planId": plan["id"],
        "planRevision": plan["revision"],
        "goalId": plan["goalId"],
        "notebookContext": context,
        "privateConditions": CANARY,
        "privateSeed": CANARY,
    }
    if legacy:
        for name in ("environment", "judge"):
            key.pop(name)
        row.pop("comparisonIdentity")
        record.pop("comparisonIdentity")
        record.pop("candidateIdentity")
    service.store.append(row)
    app.state.notebooks.complete_evaluation(record)
    service.store.save(record)
    return record


def test_legacy_receipt_keeps_its_decision_but_cannot_establish_a_current_reference(tmp_path):
    app = create_app(Settings())
    with TestClient(app) as client:
        plan = prepare(client, tmp_path / "task")
        legacy = controlled_receipt(app, plan, successes=0, legacy=True)
        current = controlled_receipt(app, plan, successes=2)
        [old_entry, new_entry] = client.get("/api/v1/notebooks/goal/notebook-probe").json()["entries"]
        entries = {entry["evaluationId"]: entry for entry in (old_entry, new_entry)}
        assert entries[legacy["evaluationId"]]["comparison"]["state"] == "unknown"
        assert entries[legacy["evaluationId"]]["score"]["score"] == 0
        assert entries[legacy["evaluationId"]]["reportedDecision"] == "baseline"
        assert entries[current["evaluationId"]]["comparison"]["state"] == "baseline"
        assert entries[current["evaluationId"]]["comparison"]["referenceEvaluationId"] is None
        service = app.state.manager.evaluations
        assert service.store.reference(current["key"])["evaluationId"] == current["evaluationId"]
        response = client.get(
            "/api/v1/suites/lab-demo-pick-place/1/scoreboard",
            params={"compare": legacy["evaluationId"], "against": current["evaluationId"]},
        )
        assert response.status_code == 409 and response.json()["detail"]["code"] == "not_comparable"
        retained = client.get("/api/v1/evaluations/" + legacy["evaluationId"]).json()["result"]
        assert retained["score"] == 0 and retained["decision"] == "baseline"
        assert "comparisonIdentity" not in service.store.records[legacy["evaluationId"]]


def test_committed_legacy_notebook_projects_unknown_after_restart_without_rewriting_history(tmp_path):
    settings = Settings()
    app = create_app(settings)
    with TestClient(app) as client:
        plan = prepare(client, tmp_path / "task")
        baseline = controlled_receipt(app, plan, successes=0, legacy=True)
        candidate = controlled_receipt(app, plan, successes=2, reference=baseline["result"], legacy=True)
        entries = client.get("/api/v1/notebooks/goal/notebook-probe").json()["entries"]
        selected = next(entry for entry in entries if entry["evaluationId"] == candidate["evaluationId"])
        changed = client.patch(
            "/api/v1/notebooks/goal/notebook-probe/" + selected["entryId"],
            headers={"X-Actor": "user:historical-reviewer"},
            json={
                "hypothesisId": "H1",
                "verdict": "refuted",
                "note": "Retain the original audit",
                "requestId": "old-audit",
            },
        )
        assert changed.status_code == 200, changed.text
        path = app.state.notebooks.store.path
    # This is the already committed canonical format from the old producer,
    # not a new receipt projected with current comparison rules.
    historical = json.loads(path.read_bytes())
    snapshots = {}
    for entry in historical["entries"]:
        is_candidate = entry["evaluationId"] == candidate["evaluationId"]
        entry["comparison"] = {
            "state": "comparable" if is_candidate else "baseline",
            "reason": None,
            "conditionsGroup": baseline["evaluationId"],
            "referenceEvaluationId": baseline["evaluationId"] if is_candidate else None,
            "referenceScore": 0.0 if is_candidate else None,
            "remainingGapReduction": 1.0 if is_candidate else None,
            "gapReason": None,
        }
        snapshots[entry["evaluationId"]] = entry
    path.write_text(json.dumps(historical, indent=2), encoding="utf-8")
    canonical_bytes = path.read_bytes()
    with TestClient(create_app(settings)) as restarted:
        public = restarted.get("/api/v1/notebooks/goal/notebook-probe").json()["entries"]
        for entry in public:
            original = snapshots[entry["evaluationId"]]
            assert entry["comparison"]["state"] == "unknown"
            assert entry["comparison"]["remainingGapReduction"] is None
            assert entry["comparison"]["referenceEvaluationId"] is None
            assert entry["comparison"]["referenceScore"] is None
            assert entry["recordedComparison"] == original["comparison"]
            for name in ("score", "reportedDecision"):
                assert entry[name] == original[name]
            for current_hypothesis, historical_hypothesis in zip(entry["hypotheses"], original["hypotheses"]):
                for name in ("automatic", "definition", "overrides"):
                    assert current_hypothesis[name] == historical_hypothesis[name]
        loop = restarted.get("/api/v1/loops/goal/notebook-probe").json()
        assert loop["reference"]["state"] == "unknown" and loop["reference"]["remainingGapReduction"] is None
        assert all(entry["comparison"]["state"] == "unknown" for entry in loop["rounds"])
        assert path.read_bytes() == canonical_bytes


def test_actual_evaluation_is_bound_to_plan_and_private_inputs_never_enter_notebook(tmp_path):
    app = create_app(Settings())
    with TestClient(app) as client:
        plan = prepare(client, tmp_path / "task")
        private_suite = app.state.library_assets.roots["user"] / "suites/notebook-private-oracle/1"
        shutil.copytree(BUILTIN_ROOT / "suites/lab-demo-pick-place/1", private_suite)
        suite_path = private_suite / "suite.yaml"
        metadata = yaml.safe_load(suite_path.read_text())
        metadata["id"] = "suite/notebook-private-oracle"
        suite_path.write_text(yaml.safe_dump(metadata))
        with (private_suite / "private/seeds.yaml").open("a") as stream:
            stream.write(f"\n# {CANARY}\n")
        app.state.manager.evaluations.suites.refresh()
        started = client.post(
            "/api/v1/evaluations",
            json={
                "subject": "robot_arm.virtual",
                "suite": "suite/notebook-private-oracle@1",
                "planId": plan["id"],
                "planRevision": plan["revision"],
            },
        )
        assert started.status_code == 202, started.text
        evaluation = started.json()["evaluationId"]
        result = until(
            lambda: client.get(f"/api/v1/evaluations/{evaluation}").json(),
            lambda value: value["status"] in ("completed", "failed"),
            seconds=60,  # Observer bound; the fixed Suite's task timeout is unchanged.
        )
        assert result["status"] == "completed", result
        assert result["attemptedEpisodes"] == 2 and result["planId"] == plan["id"]
        notebook = client.get("/api/v1/notebooks/goal/notebook-probe")
        assert notebook.status_code == 200, notebook.text
        [entry] = notebook.json()["entries"]
        assert entry["score"]["successes"] == 2 and entry["reportedDecision"] == "baseline"
        assert entry["hypotheses"][0]["automatic"]["verdict"] == "confirmed"
        assert entry["deployment"]["status"] == "not_requested"
        assert_public(entry)
        # The oracle still rejects a genuine private-field/value leak, but ignores
        # short digit sequences inside legitimate public UUIDs/hashes/timestamps.
        with pytest.raises(AssertionError):
            assert_public({"privateSeeds": [4101]})
        with pytest.raises(AssertionError):
            assert_public({"payload": [4102]})
        text = next(
            (app.state.notebooks.store.library_root / "notebooks/notebook-probe").glob("*.md")
        ).read_text()
        assert CANARY not in text
        assert_public(yaml.safe_load(text.split("---\n", 2)[1]))


def test_manual_override_is_audited_and_test_receipts_are_not_published(tmp_path):
    app = create_app(Settings())
    with TestClient(app) as client:
        plan = prepare(client, tmp_path / "task")
        record = controlled_receipt(app, plan)
        [entry] = client.get("/api/v1/notebooks/goal/notebook-probe").json()["entries"]
        controlled_receipt(app, plan, split="test")
        route = f"/api/v1/notebooks/goal/notebook-probe/{entry['entryId']}"
        body = {
            "hypothesisId": "H1",
            "verdict": "refuted",
            "note": "Human reevaluated support",
            "requestId": "override-1",
        }
        assert client.patch(route, json=body, headers={"X-Actor": "agent:optimizer"}).status_code == 403
        changed = client.patch(route, json=body, headers={"X-Actor": "user:reviewer"})
        assert changed.status_code == 200, changed.text
        result = changed.json()
        assert result["reportedDecision"] == entry["reportedDecision"]
        assert result["deployment"] == entry["deployment"]
        assert result["hypotheses"][0]["automatic"]["verdict"] == "confirmed"
        assert result["hypotheses"][0]["effectiveVerdict"] == "refuted"
        assert client.patch(route, json=body, headers={"X-Actor": "user:reviewer"}).json() == result
        app.state.manager.evaluations.store.save(record)  # Replay a real terminal notification.
        public = client.get("/api/v1/notebooks/goal/notebook-probe?refuted=true").json()
        assert len(public["entries"]) == 1
        assert public["entries"][0]["hypotheses"][0]["overrides"][0]["actor"]["name"] == "reviewer"
        assert CANARY not in json.dumps(public)
        assert CANARY not in client.get("/api/v1/loops/goal/notebook-probe").text
        for file in (app.state.notebooks.store.library_root / "notebooks").rglob("*"):
            if file.is_file():
                assert CANARY not in file.read_text()


def test_linked_evaluation_admission_preserves_budget_through_failure_and_refuses_a_duplicate_allocation(
    tmp_path,
):
    app = create_app(Settings())
    entered = threading.Event()

    async def unavailable(session, suite, case, *, attempt=None):
        entered.set()
        await asyncio.Event().wait()

    app.state.manager.evaluations._episode = unavailable
    with TestClient(app) as client:
        plan = prepare(client, tmp_path / "task")
        body = {"subject": "robot_arm.virtual", "suite": SUITE, "planId": plan["id"], "planRevision": 1}
        first = client.post("/api/v1/evaluations", json=body)
        assert first.status_code == 202, first.text
        assert entered.wait(5)
        duplicate = client.post("/api/v1/evaluations", json=body)
        assert (
            duplicate.status_code == 409
            and duplicate.json()["detail"]["code"] == "plan_evaluation_budget_exhausted"
        )
        evaluation = first.json()["evaluationId"]
        assert client.post(f"/api/v1/evaluations/{evaluation}/cancel").status_code == 200
        until(
            lambda: client.get(f"/api/v1/evaluations/{evaluation}").json(),
            lambda value: value["status"] == "cancelled",
        )
        [entry] = client.get("/api/v1/notebooks/goal/notebook-probe").json()["entries"]
        assert entry["attemptedEpisodes"] == 1 and entry["score"] is None
        [attempt] = entry["partialEpisodes"]
        assert attempt["status"] == "cancelled" and attempt["success"] is None
        assert attempt["recordingId"] in entry["recordings"] and attempt["cleanupComplete"] is True
        assert entry["hypotheses"][0]["automatic"]["verdict"] == "inconclusive"
        budget = client.get("/api/v1/loops/goal/notebook-probe").json()["budget"]
        assert budget["actual"]["episodes"] == 1 and budget["effective"]["episodes"] == 2


def test_attempt_journal_failure_before_source_does_not_manufacture_an_executed_episode(
    tmp_path, monkeypatch
):
    starts = []

    def forbidden(*args):
        starts.append(args)
        raise AssertionError("Counter write failed before a Source may start")

    app = create_app(Settings(), source_factory=forbidden)
    with TestClient(app) as client:
        plan = prepare(client, tmp_path / "task")
        store = app.state.manager.evaluations.store
        save = store.save
        failed = []

        def fail_once(record):
            if record["status"] == "running" and record.get("attemptedEpisodes") == 1 and not failed:
                failed.append(True)
                raise OSError("Owned injected attempt-journal failure")
            return save(record)

        monkeypatch.setattr(store, "save", fail_once)
        response = client.post(
            "/api/v1/evaluations",
            json={
                "subject": "robot_arm.virtual",
                "suite": SUITE,
                "planId": plan["id"],
                "planRevision": plan["revision"],
            },
        )
        assert response.status_code == 202, response.text
        identifier = response.json()["evaluationId"]
        result = until(
            lambda: client.get(f"/api/v1/evaluations/{identifier}").json(),
            lambda value: value["status"] == "failed",
        )
        assert not starts and failed
        assert result["attemptedEpisodes"] == 0
        [entry] = client.get("/api/v1/notebooks/goal/notebook-probe").json()["entries"]
        assert entry["attemptedEpisodes"] == 0 and entry["score"] is None


def test_invalid_source_attempt_retains_notebook_recording_and_consumed_budget(tmp_path):
    class UnavailableSource(SyntheticSource):
        async def invoke(self, request):
            raise OSError("Owned unavailable transport; not an ability failure")

    app = create_app(Settings(), source_factory=lambda kind, identifier: UnavailableSource(identifier))
    with TestClient(app) as client:
        plan = prepare(client, tmp_path / "task")
        response = client.post(
            "/api/v1/evaluations",
            json={"subject": "robot_arm.virtual", "suite": SUITE, "planId": plan["id"], "planRevision": 1},
        )
        assert response.status_code == 202, response.text
        identifier = response.json()["evaluationId"]
        failed = until(
            lambda: client.get("/api/v1/evaluations/" + identifier).json(),
            lambda value: value["status"] == "failed",
        )
        assert failed.get("result") is None
        [entry] = client.get("/api/v1/notebooks/goal/notebook-probe").json()["entries"]
        assert entry["score"] is None and entry["attemptedEpisodes"] == 1
        [attempt] = entry["partialEpisodes"]
        assert attempt["status"] == "invalid" and attempt["success"] is None
        assert attempt["reason"] == "source_unavailable" and attempt["cleanupComplete"] is True
        assert entry["recordings"] == [attempt["recordingId"]]
        assert client.get("/recordings/" + attempt["recordingId"] + "/recording.json").is_success
        budget = client.get("/api/v1/loops/goal/notebook-probe").json()["budget"]
        assert budget["actual"]["episodes"] == 1 and budget["effective"]["episodes"] == 2
        assert client.get("/api/v1/suites/lab-demo-pick-place/1/scoreboard").json()["rows"] == []


def test_candidate_change_judge_change_and_reference_ceiling_remain_distinct(tmp_path, monkeypatch):
    app = create_app(Settings())
    with TestClient(app) as client:
        plan = prepare(client, tmp_path / "task")
        identities = app.state.notebooks.identities()
        monkeypatch.setattr(app.state.notebooks, "identities", lambda: dict(identities))
        baseline = controlled_receipt(app, plan, successes=0)
        identities["candidate"] = "c" * 64
        candidate = controlled_receipt(app, plan, successes=2, reference=baseline["result"])

        def current(identifier):
            entries = client.get("/api/v1/notebooks/goal/notebook-probe").json()["entries"]
            return next(entry for entry in entries if entry["evaluationId"] == identifier)

        selected = current(candidate["evaluationId"])
        assert selected["reportedDecision"] == "accepted"
        assert selected["comparison"]["state"] == "comparable"
        assert selected["comparison"]["remainingGapReduction"] == 1.0
        ceiling = controlled_receipt(app, plan, successes=2, reference=candidate["result"])
        selected = current(ceiling["evaluationId"])
        assert selected["reportedDecision"] == "rejected"
        assert selected["hypotheses"][0]["automatic"]["verdict"] == "confirmed"
        assert selected["comparison"]["remainingGapReduction"] is None

        assert selected["comparison"]["gapReason"] == "reference_at_ceiling"
        identities["judge"] = "f" * 64
        changed = controlled_receipt(app, plan, successes=2, reference=candidate["result"])
        selected = current(changed["evaluationId"])
        assert selected["comparison"]["state"] == "different"
        assert selected["comparison"]["remainingGapReduction"] is None


def test_comparison_group_anchor_does_not_change_when_a_later_uuid_sorts_before_reference(tmp_path):
    app = create_app(Settings())
    with TestClient(app) as client:
        plan = prepare(client, tmp_path / "task")
        first = controlled_receipt(app, plan, successes=0, identifier="eval-" + "f" * 32)
        later = controlled_receipt(
            app, plan, successes=2, reference=first["result"], identifier="eval-" + "a" * 32
        )
        entries = client.get("/api/v1/notebooks/goal/notebook-probe").json()["entries"]
        by_id = {entry["evaluationId"]: entry for entry in entries}
        assert by_id[later["evaluationId"]]["comparison"]["state"] == "comparable"
        assert (
            by_id[later["evaluationId"]]["comparison"]["conditionsGroup"]
            == by_id[first["evaluationId"]]["comparison"]["conditionsGroup"]
        )


def test_publication_failure_keeps_evaluator_truth_and_recovery_uses_frozen_completion_identity(
    tmp_path, monkeypatch
):
    app = create_app(Settings())
    with TestClient(app) as client:
        plan = prepare(client, tmp_path / "task")

        def fail(entries):
            raise OSError("Owned publication failure")

        with monkeypatch.context() as patch:
            patch.setattr(app.state.notebooks.store, "_commit", fail)
            record = controlled_receipt(app, plan)
            assert client.get(f"/api/v1/evaluations/{record['evaluationId']}").json()["status"] == "completed"
            assert client.get("/api/v1/notebooks/goal/notebook-probe").status_code == 503
        identities = app.state.notebooks.identities() | {"judge": "e" * 64}
        monkeypatch.setattr(app.state.notebooks, "identities", lambda: identities)
        [entry] = client.get("/api/v1/notebooks/goal/notebook-probe").json()["entries"]
        assert entry["comparison"]["state"] == "baseline"
        assert entry["hypotheses"][0]["automatic"]["verdict"] == "confirmed"
        assert entry["reportedDecision"] == "baseline"


def test_normal_library_projection_conflict_does_not_escape_durable_evaluation_save(tmp_path):
    app = create_app(Settings())
    with TestClient(app) as client:
        plan = prepare(client, tmp_path / "task")
        collision = app.state.notebooks.store.library_root / "notebooks"
        collision.write_text("Owned projection conflict")
        record = controlled_receipt(app, plan)
        response = client.get(f"/api/v1/evaluations/{record['evaluationId']}")
        assert response.status_code == 200 and response.json()["status"] == "completed"
        assert client.get("/api/v1/notebooks/goal/notebook-probe").status_code == 503
        later = controlled_receipt(app, plan, successes=0)
        assert client.get(f"/api/v1/evaluations/{later['evaluationId']}").json()["status"] == "completed"
        collision.unlink()
        restored = client.get("/api/v1/notebooks/goal/notebook-probe")
        assert restored.status_code == 200, restored.text
        assert len(restored.json()["entries"]) == 2


@pytest.mark.parametrize("boundary", ["membership", "markdown"])
def test_worker_projection_and_new_revision_share_only_short_plan_snapshots(tmp_path, monkeypatch, boundary):
    app = create_app(Settings())
    entered, release = threading.Event(), threading.Event()
    with TestClient(app) as client, ThreadPoolExecutor(3) as pool:
        plan = prepare(client, tmp_path / "task")
        controlled_receipt(app, plan)
        blocked = []

        def hold_once():
            if not blocked:
                blocked.append(True)
                entered.set()
                assert release.wait(5)

        if boundary == "membership":
            snapshot = app.state.manager.training_plans.snapshot

            def pause_after_membership():
                result = snapshot()
                hold_once()
                return result

            monkeypatch.setattr(app.state.manager.training_plans, "snapshot", pause_after_membership)
        else:
            publish = app.state.notebooks.store.publish

            def pause_during_projection(identity):
                hold_once()
                return publish(identity)

            monkeypatch.setattr(app.state.notebooks.store, "publish", pause_during_projection)
        read = pool.submit(client.get, "/api/v1/loops/goal/notebook-probe")
        assert entered.wait(2)
        body = training_plan(
            plan["task"],
            budget={"gpuHours": 0.0, "episodes": 2},
            evaluation={"scene": "lab-demo@3", "episodes": 2, "threshold": 2},
        )
        writing = pool.submit(client.post, f"/api/v1/plans/{plan['id']}/revisions", json=body)
        try:
            response = writing.result(timeout=2)
            assert response.status_code == 201, response.text
        finally:
            release.set()
        response = read.result(timeout=5)
        assert response.status_code == 200, response.text
        assert response.json()["goalId"] == plan["goalId"]
