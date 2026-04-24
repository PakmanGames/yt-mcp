"""Merge transcript, keyframes, and audio features into a unified time-aligned timeline."""

from .transcript import get_text_in_range, count_words_in_range
from .frames import detect_scene_timestamps, extract_frame_as_base64, detect_animation, get_video_duration
from .audio import AudioAnalyzer


def _speech_rate_label(words: int, duration_sec: float) -> str:
    if duration_sec <= 0:
        return "unknown"
    wpm = words / (duration_sec / 60.0)
    if wpm < 100:
        return "slow"
    if wpm < 160:
        return "normal"
    return "fast"


def build_timeline(
    video_path: str,
    audio_path: str,
    transcript: dict,
    include_frames: bool = False,
    min_segment_sec: float = 5.0,
) -> list[dict]:
    """
    Build a synchronized segment list aligned to scene cuts.

    Each segment matches the schema from the project spec:
        {
            "t_start": 0.0,
            "t_end": 12.0,
            "transcript": "Welcome to this video on transformers...",
            "keyframe": "<base64 JPEG>" | null,
            "scene_change": true,
            "animation_detected": false,
            "audio": {
                "energy": "low",
                "speech_rate": "slow",
                "music": true,
                "tempo_bpm": 0.0,
                "rms_db": -28.4
            }
        }

    include_frames=False by default to avoid token bloat in the MCP response.
    Set to True when Claude needs to visually inspect each segment.
    """
    duration = get_video_duration(video_path)
    scene_times = detect_scene_timestamps(video_path)

    # Build segment boundaries from scene cuts, enforcing min_segment_sec so
    # rapid-fire cuts don't produce hundreds of tiny segments.
    boundaries: list[float] = [0.0]
    for t in scene_times:
        if t - boundaries[-1] >= min_segment_sec:
            boundaries.append(t)
    if boundaries[-1] < duration - 0.5:
        boundaries.append(duration)

    audio_analyzer = AudioAnalyzer(audio_path)
    segments: list[dict] = []

    for i in range(len(boundaries) - 1):
        t_start = boundaries[i]
        t_end = boundaries[i + 1]
        seg_duration = t_end - t_start

        text = get_text_in_range(transcript, t_start, t_end)
        word_count = count_words_in_range(transcript, t_start, t_end)

        keyframe = None
        if include_frames:
            keyframe = extract_frame_as_base64(video_path, t_start)

        anim = detect_animation(video_path, t_start, t_end) if seg_duration > 2.0 else False

        audio_features = audio_analyzer.analyze_segment(t_start, t_end)
        audio_features["speech_rate"] = _speech_rate_label(word_count, seg_duration)

        segments.append({
            "t_start": round(t_start, 3),
            "t_end": round(t_end, 3),
            "transcript": text,
            "keyframe": keyframe,
            "scene_change": i > 0,
            "animation_detected": anim,
            "audio": audio_features,
        })

    return segments
