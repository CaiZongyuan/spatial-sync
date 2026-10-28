"""Owned GPU-free browser fixture with explicitly controlled evaluator receipts."""

import asyncio
import json
import os
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from fastapi import Request

ROOT = Path(__file__).resolve().parents[2]
os.environ.pop("SPATIAL_CATALOG_METADATA_ROOT", None)
for variable, directory in (
    ("LW_LIBRARY", "library"),
    ("SPATIAL_STATE_ROOT", "state"),
    ("SPATIAL_RECORDINGS_ROOT", "recordings"),
):
    os.environ.setdefault(variable, str(ROOT / ".scratch/notebooks-browser" / directory))

from apps.api.app.main import create_app  # noqa: E402
from apps.api.app.platform_monitor import platform_snapshot, unavailable_system  # noqa: E402
from apps.api.app.settings import Settings  # noqa: E402
from packages.info.evaluations import evaluation_decision, wilson_interval  # noqa: E402
from tests.fixtures.training_task import task_archive, task_directory, training_plan  # noqa: E402

CANARY = "PRIVATE-HELDOUT-NOTEBOOK-DO-NOT-PUBLISH-729618"
SUITE = "suite/lab-demo-pick-place@1"


class Sampler:
    def sample(self, rows):
        return platform_snapshot(rows, unavailable_system("Owned Notebook browser fixture; no host probes"))


def no_source(*args):
    raise AssertionError("Controlled evaluator fixture must not start physics or a Session")


origins = tuple(filter(None, os.getenv("SPATIAL_ALLOWED_ORIGINS", "").split(",")))
app = create_app(
    Settings(node_trainer=(), allowed_origins=origins or Settings().allowed_origins),
    source_factory=no_source,
    platform_sampler=Sampler(),
)
base_identities = app.state.notebooks.identities()
version = ["a" * 64]
outcomes = [0]
app.state.notebooks.identities = lambda: base_identities | {"candidate": version[0]}


def now():
    return datetime.now(UTC).isoformat()


async def controlled_evaluator(identifier):
    """Uses real admission/stores/statistics/publishing; scores are a labelled fake adapter."""
    service = app.state.manager.evaluations
    record = deepcopy(service.store.records[identifier])
    suite = service.suites.resolve(record["suite"])
    record.update(status="running", startedAt=now())
    service.store.save(record)
    selected = app.state.manager.library.resolve(suite.metadata["scene"])
    identity = {name: base_identities[name] for name in ("environment", "judge")}
    key = {
        "suite": suite.id,
        "suiteContentHash": suite.content_hash,
        "scene": selected.id,
        "sceneContentHash": selected.content_hash,
        "source": "synthetic",
        "newtonVersion": None,
        "solver": "Controlled evaluator receipt fixture; no physics",
        "target": suite.task["robot"],
        "bindingHash": "f" * 64,
        **identity,
    }
    record.update(
        key=key,
        comparisonIdentity=identity,
        comparisonState="known",
        candidateIdentity={"kind": "controlled-fixture", "contentHash": version[0]},
    )
    stages = [stage["id"] for stage in suite.task["milestones"]]
    for index, case in enumerate(case for case in suite.metadata["episodes"] if case["split"] == "train"):
        success = index < outcomes[0]
        record["attemptedEpisodes"] += 1
        record["episodes"].append(
            {
                "caseId": case["id"],
                "success": success,
                "failedStage": None if success else stages[0],
                "milestones": {stage: success for stage in stages},
            }
        )
    successes, count = outcomes[0], len(record["episodes"])
    reference = service.store.reference(key)
    row = {
        "evaluationId": identifier,
        "suite": suite.id,
        "subject": record["subject"],
        "version": version[0],
        "key": key,
        "comparisonIdentity": identity,
        "split": "train",
        "successes": successes,
        "episodes": count,
        "score": successes / count,
        "ci95": wilson_interval(successes, count),
        "decision": evaluation_decision(successes, count, reference["score"] if reference else None),
        "reference": reference["evaluationId"] if reference else None,
        "phases": {
            stage: {"passed": successes, "episodes": count, "rate": successes / count} for stage in stages
        },
        "recordings": [],
        "durationSeconds": 0.01,
        "at": now(),
        "planId": record["planId"],
        "planRevision": record["planRevision"],
        "goalId": record["goalId"],
    }
    service.store.append(row)
    record.update(status="completed", result=row, endedAt=now(), privateConditions=CANARY, privateSeed=CANARY)
    service._capture_completion(record)
    service.store.save(record)


app.state.manager.evaluations._evaluate = controlled_evaluator


@app.get("/e2e/storage-roots")
async def storage_roots():
    manager = app.state.manager
    return {
        "sceneLibrary": str(manager.library_assets.roots["user"]),
        "assetLibrary": str(manager.library_assets.roots["user"]),
        "state": str(manager.settings.state_root),
        "recordings": str(manager.settings.recordings_root),
    }


@app.post("/e2e/notebook-rounds")
async def seed(request: Request):
    async with httpx.AsyncClient(base_url=str(request.base_url), trust_env=False, timeout=10) as http:
        with tempfile.TemporaryDirectory() as temporary:
            package = task_directory(Path(temporary) / "task", "rsi-browser-probe")
            task_path = package / "task.yaml"
            metadata = yaml.safe_load(task_path.read_text())
            metadata["scene"] = metadata["evaluation"]["scene"] = "lab-demo@3"
            metadata["loopBudget"] = {"gpuHours": 0.0, "episodes": 4, "days": 1.0}
            task_path.write_text(yaml.safe_dump(metadata))
            uploaded = await http.post(
                "/api/v1/tasks", content=task_archive(package), headers={"Content-Type": "application/x-tar"}
            )
        assert uploaded.status_code == 201, uploaded.text
        task = uploaded.json()
        plans, evaluations = [], []
        for index in range(2):
            version[0] = ("a" if index == 0 else "b") * 64
            outcomes[0] = 0 if index == 0 else 2
            body = training_plan(
                task["id"],
                title=f"受控评估第 {index + 1} 轮",
                goal="在固定评估中检验改进假设",
                goalId="goal/rsi-browser-probe",
                jobs={"seeds": []},
                budget={"gpuHours": 0.0, "episodes": 2},
                evaluation={"scene": "lab-demo@3", "episodes": 2, "threshold": 2},
                previous=plans[-1]["id"] if plans else None,
                hypotheses=[
                    {
                        "id": f"H{index + 1}",
                        "cause": "控制改动是否改善成绩",
                        "change": "使用受控候选版本",
                        "predict": {"metric": "success", "to": ">= 0.8"},
                    }
                ],
                interview="Owned controlled receipt fixture; no business approval or training.",
            )
            submitted = await http.post("/api/v1/plans", json=body)
            assert submitted.status_code == 201, submitted.text
            plan = submitted.json()
            approved = await http.post(
                f"/api/v1/approvals/{plan['approvalId']}/decision",
                headers={"X-Actor": "user:owned-browser-fixture"},
                json={"decision": "approve", "note": "仅测试受控数据"},
            )
            assert approved.status_code == 200, approved.text
            evaluated = await http.post(
                "/api/v1/evaluations",
                json={
                    "subject": "robot_arm.virtual",
                    "suite": SUITE,
                    "planId": plan["id"],
                    "planRevision": plan["revision"],
                },
            )
            assert evaluated.status_code == 202, evaluated.text
            identifier = evaluated.json()["evaluationId"]
            for _ in range(100):
                result = (await http.get(f"/api/v1/evaluations/{identifier}")).json()
                if result["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            assert result["status"] == "completed", result
            plans.append(plan)
            evaluations.append(identifier)
        third = await http.post(
            "/api/v1/plans", json=body | {"title": "预算用尽后的新计划", "previous": plans[-1]["id"]}
        )
        assert third.status_code == 201, third.text
        # This private future promotion receipt is deliberately not a public Notebook/Goal record.
        private = deepcopy(app.state.manager.evaluations.store.records[evaluations[-1]])
        private.update(
            evaluationId="eval-" + "e" * 32,
            split="test",
            planId=None,
            privateConditions=CANARY,
            privateSeed=CANARY,
        )
        private["result"] = private["result"] | {"split": "test", "decision": "test", "privateGrade": CANARY}
        app.state.manager.evaluations.store.save(private)
        public = (await http.get("/api/v1/notebooks/goal/rsi-browser-probe")).json()
        assert CANARY not in json.dumps(public)
        return {
            "goalId": "goal/rsi-browser-probe",
            "plans": [plan["id"] for plan in plans],
            "evaluations": evaluations,
            "entries": [entry["entryId"] for entry in public["entries"]],
            "pendingApproval": third.json()["approvalId"],
            "scope": "Controlled fake receipts, no Source/training/GPU",
        }
