"""Frame extraction via PySceneDetect + FFmpeg, with OpenCV animation detection."""

import base64
import json
import os
import subprocess
import tempfile
from typing import Optional


def get_video_duration(video_path: str) -> float:
    """Return video duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", video_path],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(result.stdout)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return float(stream.get("duration", 0))
    return 0.0


def detect_scene_timestamps(video_path: str, threshold: float = 27.0) -> list[float]:
    """Use PySceneDetect ContentDetector to find scene-cut timestamps (seconds)."""
    from scenedetect import detect, ContentDetector
    scenes = detect(video_path, ContentDetector(threshold=threshold))
    timestamps: set[float] = set()
    for start, end in scenes:
        timestamps.add(round(start.get_seconds(), 3))
    return sorted(timestamps)


def extract_frame_as_base64(video_path: str, timestamp: float, width: int = 1280) -> Optional[str]:
    """Extract a single frame at `timestamp` seconds, return base64-encoded JPEG."""
    fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(timestamp),
                "-i", video_path,
                "-vframes", "1",
                "-vf", f"scale={width}:-1",
                "-q:v", "3",
                tmp_path,
            ],
            capture_output=True,
        )
        if result.returncode != 0 or not os.path.getsize(tmp_path):
            return None
        with open(tmp_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    finally:
        os.unlink(tmp_path)


def detect_animation(video_path: str, t_start: float, t_end: float, samples: int = 5) -> bool:
    """
    Detect within-shot animation by comparing pixel differences across sampled frames.
    Returns True if mean inter-frame pixel change > 3% (motion/animation present).
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return False

    times = [t_start + (t_end - t_start) * i / max(samples - 1, 1) for i in range(samples)]
    frames = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ret, frame = cap.read()
        if ret:
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    cap.release()

    if len(frames) < 2:
        return False

    import numpy as np
    diffs = [
        cv2.absdiff(frames[i - 1], frames[i]).mean() / 255.0
        for i in range(1, len(frames))
    ]
    return float(np.mean(diffs)) > 0.03


def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def get_keyframes(
    video_path: str,
    strategy: str = "scene",
    interval: int = 30,
    frame_width: int = 1280,
) -> list[dict]:
    """
    Extract keyframes from video.

    strategy:
        "scene"    — one frame per scene cut (PySceneDetect ContentDetector)
        "interval" — one frame every `interval` seconds
        "both"     — union of scene cuts and fixed-interval frames

    Returns list of:
        {
            "t": 12.0,
            "t_formatted": "0:12",
            "keyframe": "<base64 JPEG>",
            "scene_change": true,
            "animation_detected": false
        }
    """
    duration = get_video_duration(video_path)
    scene_times: set[float] = set()
    timestamps: list[float] = []

    if strategy in ("scene", "both"):
        scene_times = set(detect_scene_timestamps(video_path))
        timestamps.extend(scene_times)

    if strategy in ("interval", "both"):
        t = 0.0
        while t < duration:
            timestamps.append(t)
            t += interval

    if 0.0 not in timestamps:
        timestamps.insert(0, 0.0)

    timestamps = sorted(set(round(t, 3) for t in timestamps if t < duration))

    results = []
    for i, t in enumerate(timestamps):
        frame_b64 = extract_frame_as_base64(video_path, t, width=frame_width)
        if frame_b64 is None:
            continue

        t_next = timestamps[i + 1] if i + 1 < len(timestamps) else min(t + 10, duration)
        anim = detect_animation(video_path, t, t_next) if (t_next - t) > 1.0 else False

        results.append({
            "t": t,
            "t_formatted": format_time(t),
            "keyframe": frame_b64,
            "scene_change": t in scene_times,
            "animation_detected": anim,
        })

    return results
