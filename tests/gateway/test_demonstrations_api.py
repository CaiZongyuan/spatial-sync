"""Real Synthetic recording → DemoSet → real node transfer with a fake CPU trainer."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import yaml
from test_training_node_process import start_server, wait_for

from apps.api.app.settings import Settings
from packages.info.plans import PlanError
from packages.library.common import content_hash
from packages.training.inputs import pack_package, store_artifacts
from tests.fixtures.demonstration_task import demonstration_task, successful_recording
from tests.fixtures.training_task import task_archive, training_plan


def test_success_demo_versions_reach_isolated_node_inputs_and_versions_without_rewriting_recording(tmp_path):
    package = demonstration_task(tmp_path / "task")
    capabilities = tmp_path / "capabilities.json"
    capabilities.write_text(json.dumps({
        "hardware": "demonstration CPU contract fixture", "gpus": [], "cpuAvailable": True, "profiles": [],
    }), encoding="utf-8")
    trainer = Path(__file__).resolve().parents[1] / "fixtures/fake_trainer.py"
    settings = Settings(node_trainer=(sys.executable, str(trainer)), node_capabilities=capabilities,
                        node_interval=0.2, node_job_timeout=30)
    app, server, thread = start_server(settings)
    try:
        with httpx.Client(base_url=settings.node_gateway_url, trust_env=False, timeout=30) as http:
            pushed = http.post("/api/v1/tasks", content=task_archive(package),
                               headers={"Content-Type": "application/x-tar"})
            assert pushed.status_code == 201, pushed.text
            task = pushed.json()
            identifier = successful_recording(http)
            source = settings.recordings_root / identifier
            original_hash = content_hash(source)
            missing = http.post("/api/v1/demos", json={"recordingId": identifier})
            assert missing.status_code == 422 and missing.json()["detail"]["code"] == "demo_task_required"
            response = http.post("/api/v1/demos", json={"recordingId": identifier, "task": task["id"]})
            assert response.status_code == 201, response.text
            first = response.json()
            assert first["recordingContentHash"] == original_hash
            assert first["sourceProgram"]["id"] == "robot_arm.virtual"
            assert first["sourceProgram"]["implementationHash"] is None
            assert first["params"] == {"from": "rack.A1", "to": "centrifuge.slot1"}
            assert http.post("/api/v1/demos", json={"recordingId": identifier}).json()["idempotent"]
            listed = http.get("/api/v1/demos", params={"task": task["id"]}).json()["demos"]
            assert listed[0]["id"] == first["id"]
            metadata = http.get(first["recordingUrl"]).json()
            assert metadata == json.loads((source / "recording.json").read_bytes())
            segment = metadata["segments"][0]["file"]
            frozen_segment = http.get(first["recordingUrl"].rsplit("/", 1)[0] + "/" + segment).content
            assert frozen_segment == (source / segment).read_bytes()
            assert frozen_segment[:6] == b"LWLG\x01\x00"

            file = package / "task.yaml"
            declaration = yaml.safe_load(file.read_text(encoding="utf-8"))
            declaration["demonstrations"] = [{"set": first["set"]["id"], "purpose": "reset"}]
            file.write_text(yaml.safe_dump(declaration), encoding="utf-8")
            consumer = http.post("/api/v1/tasks", content=task_archive(package),
                                 headers={"Content-Type": "application/x-tar"})
            assert consumer.status_code == 201, consumer.text
            consumer = consumer.json()
            # A later dataset revision cannot change the consumer Task's pinned v1.
            second_recording = successful_recording(http)
            second = http.post("/api/v1/demos", json={"recordingId": second_recording, "task": task["id"]})
            assert second.status_code == 201, second.text
            assert second.json()["set"]["id"].endswith("@2")
            assert len(http.get("/api/v1/demo-sets/demonstration-probe/1").json()["demonstrations"]) == 1
            plan_body = training_plan(consumer["id"], hyperparameters={
                "num_envs": 4, "max_iterations": 1, "device": "cpu",
            }, evaluation={"scene": "lab-demo@3", "episodes": 2, "threshold": 2})
            submitted = http.post("/api/v1/plans", json=plan_body)
            assert submitted.status_code == 201, submitted.text
            plan = submitted.json()
            approved = http.post(f"/api/v1/approvals/{plan['approvalId']}/decision", json={
                "decision": "approve", "note": "Isolated fake CPU trainer, no actual training",
            })
            assert approved.status_code == 200, approved.text
            started = http.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42})
            assert started.status_code == 202, started.text
            job = started.json()
            expected = [first["set"] | {"purpose": "reset"}]
            assert job["demonstrationSets"] == expected
            assert job["demonstrationRecordings"] == [{"id": identifier, "contentHash": original_hash}]
            measurement = tmp_path / "measurement.json"
            measurement.write_text(json.dumps({
                "schema": "lw.training-profile/1", "taskContentHash": consumer["contentHash"],
                "hardware": "demonstration CPU contract fixture", "resourceId": "cpu",
                **{key: plan_body[key] for key in ("algorithm", "model", "hyperparameters")},
                "samplesSeconds": [0.01], "setupSeconds": 0, "measuredAt": 10,
            }), encoding="utf-8")
            imported = subprocess.run([
                sys.executable, str(trainer.parents[2] / "tools/import_training_profile.py"),
                "--job", job["jobId"], "--resource", "cpu", "--measurement", str(measurement),
                "--capabilities", str(capabilities), "--url", settings.node_gateway_url,
            ], capture_output=True, text=True, encoding="utf-8", timeout=15)
            assert imported.returncode == 0, imported.stdout + imported.stderr
            finished = wait_for(lambda: http.get(f"/api/v1/jobs/{job['jobId']}").json(),
                                lambda value: value["status"] in ("succeeded", "failed"), timeout=45)
            assert finished["status"] == "succeeded", finished
            assert finished["versions"]["demonstrationSets"] == expected
            assert finished["versions"]["hardware"]["device"] == "cpu"
            assert finished["chargedGpuHours"] == 0
            artifact_root = (
                app.state.manager.library_assets.roots["user"] / "jobs" / job["jobId"] / "artifacts"
            )
            actual = json.loads((artifact_root / "demonstration-inputs.json").read_bytes())
            received = [{key: item[key] for key in ("id", "contentHash", "purpose")} for item in actual]
            assert received == expected
            assert list(actual[0]["recordings"]) == [identifier]
            # Server artifact validation rejects omission or substitution of a pinned DemoSet.
            forged = tmp_path / "forged-artifacts"
            shutil.copytree(artifact_root, forged)
            versions = json.loads((forged / "versions.json").read_bytes())
            versions["demonstrationSets"] = []
            (forged / "versions.json").write_text(json.dumps(versions), encoding="utf-8")
            import io

            with pytest.raises(PlanError) as refused:
                store_artifacts(app.state.manager, app.state.jobs.get(job["jobId"]),
                                io.BytesIO(pack_package(forged)))
            assert refused.value.detail["code"] == "artifacts_incomplete"
            assert content_hash(source) == original_hash
            assert all(item["state"] == "STOPPED" for item in http.get("/api/v1/health").json()["sessions"])
            wait_for(lambda: http.get("/api/v1/nodes").json(), lambda value: not value["nodes"][0]["leases"])
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        assert not thread.is_alive(), "Owned Demonstration API did not close"
