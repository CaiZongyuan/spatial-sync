"""Fixed MediaPipe entrypoint executed using an explicitly configured CPU interpreter."""

import argparse
import hashlib
import json
from pathlib import Path

# This script runs in the isolated estimator environment without gateway packages.
BODY = (11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("digest")
    parser.add_argument("version")
    parser.add_argument("--inputs", type=Path)
    args = parser.parse_args()

    import cv2
    import mediapipe as mp

    checksum = hashlib.sha256()
    with args.model.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    if checksum.hexdigest() != args.digest:
        raise ValueError("Pose model digest differs")
    if mp.__version__ != args.version:
        raise ValueError("Pose SDK version differs")
    options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(
            model_asset_path=str(args.model), delegate=mp.tasks.BaseOptions.Delegate.CPU
        ),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        num_poses=2,
    )
    cv2.setNumThreads(1)
    with mp.tasks.vision.PoseLandmarker.create_from_options(options) as estimator:
        if args.inputs is None:
            return
        source = json.loads(args.inputs.read_bytes())
        frames = []
        video = cv2.VideoCapture(str(args.inputs.with_name("video")), cv2.CAP_FFMPEG)
        video.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
        try:
            for seconds in source["frameTimes"]:
                ok, frame = video.read()
                if not ok or frame.shape[:2] != (source["height"], source["width"]):
                    raise ValueError("Decoded frame differs from the inspected video")
                image = mp.Image(
                    image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                )
                result = estimator.detect_for_video(image, round((seconds - source["frameTimes"][0]) * 1000))
                poses = []
                for image_points, world in zip(
                    result.pose_landmarks, result.pose_world_landmarks, strict=True
                ):
                    center = [
                        (getattr(world[23], axis) + getattr(world[24], axis)) / 2 for axis in ("x", "y", "z")
                    ]
                    poses.append(
                        [
                            {
                                "position": [
                                    world[i].x - center[0],
                                    world[i].z - center[2],
                                    center[1] - world[i].y,
                                ],
                                "visibility": image_points[i].visibility,
                                "inFrame": 0 <= image_points[i].x <= 1 and 0 <= image_points[i].y <= 1,
                            }
                            for i in BODY
                        ]
                    )
                frames.append({"timeSeconds": seconds, "poses": poses})
            if video.read()[0]:
                raise ValueError("Decoded frame count differs from inspection")
        finally:
            video.release()
    args.inputs.with_name("pose.json").write_text(
        json.dumps(
            {"sourceSha256": source["sha256"], "modelSha256": args.digest, "frames": frames}, allow_nan=False
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
