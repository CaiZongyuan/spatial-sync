"""Bounded inspection of uploaded MP4/WebM video."""

import json
import math
import subprocess
from fractions import Fraction

from packages.info.plans import PlanError
from packages.motion.contract import MAX_DIMENSION, MAX_FRAMES, MAX_PIXELS, MAX_SECONDS


def inspect_video(path):
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                "mov,matroska,webm",
                "-max_alloc",
                str(64 * 1024 * 1024),
                "-threads",
                "1",
                "-max_pixels",
                str(MAX_PIXELS),
                "-select_streams",
                "v",
                "-show_entries",
                "format=duration,format_name:"
                "stream=codec_type,codec_name,width,height,avg_frame_rate:"
                "frame=media_type,best_effort_timestamp_time,width,height",
                "-show_frames",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
        data = json.loads(result.stdout)
        streams = [stream for stream in data["streams"] if stream["codec_type"] == "video"]
        (video,) = streams
        if result.stderr or video["codec_name"] not in ("h264", "vp8", "vp9"):
            raise ValueError
        duration = float(data["format"]["duration"])
        fps = Fraction(video["avg_frame_rate"])
        frames = [frame for frame in data["frames"] if frame["media_type"] == "video"]
        times = [float(frame["best_effort_timestamp_time"]) for frame in frames]
    except FileNotFoundError as exc:
        raise PlanError(
            "motion_video_probe_unavailable", "ffprobe is required for motion video admission", status=503
        ) from exc
    except (subprocess.SubprocessError, ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
        raise PlanError("motion_video_invalid", "Expected a decodable MP4 or WebM video", status=422) from exc
    if (
        not 0 < duration <= MAX_SECONDS
        or not 0 < fps <= 60
        or len(frames) > MAX_FRAMES
        or any(not 1 <= frame[key] <= MAX_DIMENSION for frame in frames for key in ("width", "height"))
        or any(frame["width"] * frame["height"] > MAX_PIXELS for frame in frames)
        or (times and times[-1] - times[0] > MAX_SECONDS)
    ):
        raise PlanError(
            "motion_video_limits", "Video exceeds the motion duration, frame or resolution limit", status=422
        )
    if (
        len(times) < 2
        or any(not math.isfinite(time) for time in times)
        or any(right - left < 0.001 for left, right in zip(times, times[1:]))
        or any((frame["width"], frame["height"]) != (video["width"], video["height"]) for frame in frames)
    ):
        raise PlanError(
            "motion_video_invalid", "Video must have stable dimensions and increasing timestamps", status=422
        )
    return {
        "width": video["width"],
        "height": video["height"],
        "frameRate": str(fps),
        "durationSeconds": duration,
        "frameCount": len(times),
        "frameTimes": times,
        "codec": video["codec_name"],
    }
