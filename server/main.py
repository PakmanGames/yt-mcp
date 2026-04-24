#!/usr/bin/env python3
"""
yt-mcp Server — local pipeline (no API keys required)

Tools:
  get_video_transcript  — Whisper transcription with word-level timestamps
  get_video_frames      — PySceneDetect + FFmpeg keyframe extraction
  get_audio_features    — librosa energy / tempo / music detection
  get_full_context      — unified timeline combining all signals

Requires: ffmpeg in PATH, Python 3.10+, pip packages from requirements.txt
"""

import json
import os
import sys
from typing import Annotated

# Ensure repo root is on the path regardless of working directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP

from server.utils.downloader import VideoDownloader, DownloadError
from server.tools.transcript import get_transcript
from server.tools.frames import get_keyframes, format_time
from server.tools.audio import AudioAnalyzer
from server.tools.timeline import build_timeline

mcp = FastMCP("yt-mcp")
downloader = VideoDownloader()


# ---------------------------------------------------------------------------
# Tool 1: Transcript
# ---------------------------------------------------------------------------

@mcp.tool()
def get_video_transcript(
    youtube_url: Annotated[str, "Full YouTube URL (youtube.com/watch?v=ID, youtu.be/ID, or youtube.com/shorts/ID)"],
    model_size: Annotated[str, "Whisper model: tiny | base | small | medium | large. Larger = more accurate, slower, more RAM. Default: base"] = "base",
) -> str:
    """
    Transcribe a YouTube video using OpenAI Whisper (runs entirely locally, no API key).
    Returns timestamped segments with word-level precision and detected language.
    First call downloads the video and Whisper model weights — subsequent calls use the cache.
    """
    try:
        _video_path, audio_path, info = downloader.download(youtube_url)
    except DownloadError as e:
        return json.dumps({"error": str(e)})
    except FileNotFoundError:
        return json.dumps({"error": "ffmpeg not found — install it: brew install ffmpeg"})

    try:
        transcript = get_transcript(audio_path, model_size=model_size)
    except Exception as e:
        return json.dumps({"error": f"Transcription failed: {e}"})

    return json.dumps({
        "title": info.title,
        "duration": info.duration,
        "language": transcript["language"],
        "full_text": transcript["full_text"],
        "segments": transcript["segments"],
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 2: Frames
# ---------------------------------------------------------------------------

@mcp.tool()
def get_video_frames(
    youtube_url: Annotated[str, "Full YouTube URL"],
    strategy: Annotated[str, "'scene' = one frame per scene cut, 'interval' = every N seconds, 'both' = union. Default: scene"] = "scene",
    interval: Annotated[int, "Seconds between frames when strategy is 'interval' or 'both'. Default: 30"] = 30,
) -> str:
    """
    Extract keyframes from a YouTube video as base64-encoded JPEGs.
    Uses PySceneDetect for scene cut detection and FFmpeg for frame extraction.
    Also reports animation/motion detected within each segment via pixel differencing.
    """
    try:
        video_path, _audio_path, info = downloader.download(youtube_url)
    except DownloadError as e:
        return json.dumps({"error": str(e)})
    except FileNotFoundError:
        return json.dumps({"error": "ffmpeg not found — install it: brew install ffmpeg"})

    if strategy not in ("scene", "interval", "both"):
        return json.dumps({"error": "strategy must be 'scene', 'interval', or 'both'"})

    try:
        frames = get_keyframes(video_path, strategy=strategy, interval=interval)
    except Exception as e:
        return json.dumps({"error": f"Frame extraction failed: {e}"})

    summary = [{k: v for k, v in f.items() if k != "keyframe"} for f in frames]

    return json.dumps({
        "title": info.title,
        "duration": info.duration,
        "duration_formatted": format_time(info.duration),
        "frame_count": len(frames),
        "strategy": strategy,
        "frames": frames,    # full list with base64 keyframe data
        "summary": summary,  # same list without image bytes (for quick review)
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 3: Audio features
# ---------------------------------------------------------------------------

@mcp.tool()
def get_audio_features(
    youtube_url: Annotated[str, "Full YouTube URL"],
    segment_duration: Annotated[int, "Analysis window size in seconds. Default: 30"] = 30,
) -> str:
    """
    Analyze audio characteristics using librosa (runs locally).
    Returns per-segment: energy level (low/medium/high), music presence,
    estimated tempo in BPM, and RMS level in dB.
    """
    try:
        _video_path, audio_path, info = downloader.download(youtube_url)
    except DownloadError as e:
        return json.dumps({"error": str(e)})
    except FileNotFoundError:
        return json.dumps({"error": "ffmpeg not found — install it: brew install ffmpeg"})

    try:
        analyzer = AudioAnalyzer(audio_path)
        segments = analyzer.analyze_full(segment_duration=segment_duration)
    except Exception as e:
        return json.dumps({"error": f"Audio analysis failed: {e}"})

    return json.dumps({
        "title": info.title,
        "duration": info.duration,
        "segment_duration": segment_duration,
        "segments": segments,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 4: Full context — unified timeline
# ---------------------------------------------------------------------------

@mcp.tool()
def get_full_context(
    youtube_url: Annotated[str, "Full YouTube URL"],
    include_frames: Annotated[bool, "Include base64 keyframe images per segment. Default: False — use get_video_frames separately to avoid token bloat"] = False,
    model_size: Annotated[str, "Whisper model size. Default: base"] = "base",
) -> str:
    """
    Get complete multi-modal context for a YouTube video as a synchronized timeline.

    Each segment in the timeline contains:
      - transcript: speech in that time window
      - scene_change: whether a cut was detected at this boundary
      - animation_detected: whether within-shot motion was detected
      - audio.energy: low | medium | high
      - audio.speech_rate: slow | normal | fast
      - audio.music: true | false
      - keyframe: base64 JPEG (only when include_frames=True)

    This is the primary tool for giving Claude complete situational awareness of a video.
    For long videos (>30 min) set include_frames=False and use get_video_frames for
    specific moments of interest to stay within context limits.
    """
    try:
        video_path, audio_path, info = downloader.download(youtube_url)
    except DownloadError as e:
        return json.dumps({"error": str(e)})
    except FileNotFoundError:
        return json.dumps({"error": "ffmpeg not found — install it: brew install ffmpeg"})

    try:
        transcript = get_transcript(audio_path, model_size=model_size)
    except Exception as e:
        return json.dumps({"error": f"Transcription failed: {e}"})

    try:
        segments = build_timeline(
            video_path=video_path,
            audio_path=audio_path,
            transcript=transcript,
            include_frames=include_frames,
        )
    except Exception as e:
        return json.dumps({"error": f"Timeline build failed: {e}"})

    return json.dumps({
        "title": info.title,
        "channel": info.channel,
        "duration": info.duration,
        "duration_formatted": format_time(info.duration),
        "language": transcript["language"],
        "description": info.description,
        "segments": segments,
    }, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
