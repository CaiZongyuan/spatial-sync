import hashlib
import subprocess

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.motion.contract import JOINTS, MAX_VIDEO_BYTES, MODEL_SHA256, POSE_RUNTIME
from tests.gateway.test_remote_training_nodes import OPERATOR, VIEWER, beat, register, remote_settings

VIDEO_URL = "/api/v1/library/motion-videos"
CLIP_URL = "/api/v1/library/motion-clips"
RIGHTS = {"license": "CC BY 3.0", "attribution": "Fixture", "sourceUrl": "https://example.org/reference"}


@pytest.fixture
def video(tmp_path):
    target = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "color=s=64x64:r=2",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            str(target),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    return target.read_bytes()


def test_motion_upload_rejects_invalid_video_and_needs_operator():
    with TestClient(create_app(remote_settings())) as client:
        unauthenticated = client.post(VIDEO_URL, params=RIGHTS, content=b"invalid")
        assert unauthenticated.status_code == 401
        assert client.post(VIDEO_URL, params=RIGHTS, content=b"invalid", headers=VIEWER).status_code == 403
        rejected = client.post(VIDEO_URL, params=RIGHTS, content=b"invalid", headers=OPERATOR)
        assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "motion_video_invalid"
        assert client.get(VIDEO_URL, headers=VIEWER).json() == {"videos": []}


@pytest.mark.parametrize(
    "filter_source,duration", [("64x64:r=2", "21"), ("64x64:r=60", "11"), ("2048x64:r=2", "1")]
)
def test_motion_upload_checks_actual_duration_frames_and_dimensions(tmp_path, filter_source, duration):
    target = tmp_path / "over-limit.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "color=s=" + filter_source,
            "-t",
            duration,
            "-c:v",
            "libx264",
            "-threads",
            "1",
            str(target),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    with TestClient(create_app(remote_settings())) as client:
        response = client.post(VIDEO_URL, params=RIGHTS, content=target.read_bytes(), headers=OPERATOR)
        assert response.status_code == 422 and response.json()["detail"]["code"] == "motion_video_limits"


def test_motion_upload_enforces_byte_limit_before_probing():
    with TestClient(create_app(remote_settings())) as client:
        response = client.post(
            VIDEO_URL, params=RIGHTS, content=b"x" * (MAX_VIDEO_BYTES + 1), headers=OPERATOR
        )
        assert response.status_code == 413 and response.json()["detail"]["code"] == "motion_video_too_large"


def test_motion_job_round_trip_uses_pose_lease_and_publishes_immutable_clip(video):
    settings = remote_settings()
    with TestClient(create_app(settings)) as client:
        uploaded = client.post(VIDEO_URL, params=RIGHTS, content=video, headers=OPERATOR)
        assert uploaded.status_code == 201, uploaded.text
        source = uploaded.json()
        assert source["sha256"] == hashlib.sha256(video).hexdigest()
        assert source["frameTimes"] == [0, 0.5, 1, 1.5] and source["license"] == RIGHTS
        again = client.post(VIDEO_URL, params=RIGHTS, content=video, headers=OPERATOR)
        assert again.json() == source
        node = register(client, "pose-fixture")
        assert beat(client, node, cpuAvailable=True).json()["assignments"] == []
        requested = client.post(
            "/api/v1/jobs",
            headers=OPERATOR,
            json={
                "kind": "motion_preprocess",
                "motionVideo": source["id"],
                "requestId": "motion-test",
            },
        )
        assert requested.status_code == 202, requested.text
        job = requested.json()
        assert job["status"] == "queued" and job["leaseId"] is None
        (assignment,) = beat(client, node, cpuAvailable=True, poseRuntime=POSE_RUNTIME).json()["assignments"]
        assert assignment["jobId"] == job["jobId"] and assignment["resourceId"] == "cpu"
        base = f"/api/v1/jobs/{job['jobId']}"
        lease = node | {"X-Lease-Id": assignment["leaseId"]}
        assert client.get(base + "/inputs/video", headers=node).status_code == 403
        inputs = client.get(base + "/inputs", headers=lease).json()
        assert inputs["video"]["sha256"] == source["sha256"]
        assert client.get(inputs["video"]["url"], headers=lease).content == video

        points = [
            {"position": [i / 100, 0, 0.1], "inFrame": True, "visibility": 0.9} for i in range(len(JOINTS))
        ]
        wire = {
            "sourceSha256": source["sha256"],
            "modelSha256": MODEL_SHA256,
            "frames": [{"timeSeconds": t, "poses": [points]} for t in source["frameTimes"]],
        }
        for poses, code in (
            ([], "motion_no_person"),
            ([points, points], "motion_multiple_people"),
            ([[point | {"visibility": 0.1} for point in points]], "motion_occluded"),
        ):
            rejected = client.post(
                base + "/artifacts",
                headers=lease,
                json=wire
                | {
                    "frames": [frame | {"poses": poses} for frame in wire["frames"]],
                },
            )
            assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == code
        mismatch = client.post(base + "/artifacts", headers=lease, json=wire | {"sourceSha256": "0" * 64})
        assert mismatch.status_code == 422 and mismatch.json()["detail"]["code"] == "motion_artifact_mismatch"
        published = client.post(base + "/artifacts", headers=lease, json=wire)
        assert published.status_code == 200, published.text
        descriptor = published.json()["motionClip"]
        assert client.get(CLIP_URL, headers=VIEWER).json() == {"clips": []}
        assert client.post(base + "/artifacts", headers=lease, json=wire).json() == published.json()
        report = {"jobId": job["jobId"], "leaseId": lease["X-Lease-Id"], "status": "succeeded"}
        assert beat(client, node, cpuAvailable=True, reports=[report]).status_code == 422
        assert (
            beat(client, node, cpuAvailable=True, poseRuntime=POSE_RUNTIME, reports=[report]).status_code
            == 200
        )
        assert client.get(base + "/inputs", headers=lease).status_code == 403

    with TestClient(create_app(settings)) as client:
        assert client.get(CLIP_URL, headers=VIEWER).json() == {"clips": [descriptor]}
        digest = descriptor["sha256"]
        clip = client.get(f"{CLIP_URL}/{digest}/1", headers=VIEWER).json()
        assert clip["schema"] == "lw.motion-clip/1"
        assert clip["source"]["license"] == RIGHTS and clip["model"]["landmarkLicense"] == "Apache-2.0"
        assert [frame["timeSeconds"] for frame in clip["frames"]] == source["frameTimes"]
        assert clip["frames"][0]["joints"]["leftShoulder"]["position"] == [0, 0, 0.1]
