import json

from packages.motion.contract import JOINTS, MODEL_SHA256, PoseArtifact, build_clip


def test_motion_clip_preserves_missing_timestamp_and_no_invented_root():
    times = [index / 2 for index in range(10)]
    points = [{"position": [0, 0.1, 0.2], "visibility": 0.9, "inFrame": True} for _ in JOINTS]
    artifact = PoseArtifact.model_validate_json(
        json.dumps(
            {
                "sourceSha256": "1" * 64,
                "modelSha256": MODEL_SHA256,
                "frames": [{"timeSeconds": time, "poses": [points] if time != 1 else []} for time in times],
            }
        )
    )
    clip = build_clip(artifact, {"sha256": "1" * 64, "frameTimes": times})
    assert clip["frames"][2] == {"timeSeconds": 1, "joints": None}
    assert clip["coordinates"]["origin"] == "hip-midpoint"
    assert clip["coordinates"]["rootTranslation"] == "unobserved"
    assert clip["diagnostics"] == {"visibleFrames": 9, "missingFrames": 1, "noPersonFrames": 1}
