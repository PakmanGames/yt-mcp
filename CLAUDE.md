# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

A fully local MCP server that gives Claude complete multi-modal context for any YouTube video. **No API keys required.** All processing runs on-device via yt-dlp, OpenAI Whisper, FFmpeg, PySceneDetect, and librosa.

### Pipeline (per video)

```
YouTube URL
    ↓
yt-dlp          → download video.mp4 + extract audio.wav (16kHz mono)
    ↓
Whisper         → timestamped transcript with word-level precision
    ↓
PySceneDetect   → detect scene cuts / transition boundaries
    ↓
FFmpeg          → extract keyframes at scene boundaries
    ↓
OpenCV          → pixel-diff animation detection within shots
    ↓
librosa         → audio energy, tempo, music vs speech classification
    ↓
timeline.py     → unified JSON: transcript + frames + audio, time-aligned
```

Results are cached in `/tmp/yt-analysis-cache/<video_id>/` — re-calling with the same URL is instant.

### Core Components

**`server/main.py`** — FastMCP entry point, registers 4 tools

**`server/utils/downloader.py`** — `VideoDownloader` class
- `download(url)` → `(video_path, audio_path, VideoInfo)`
- Caches by video ID; extracts 16kHz mono WAV for Whisper + librosa

**`server/tools/transcript.py`**
- `get_transcript(audio_path, model_size)` → `{language, full_text, segments}`
- `get_text_in_range(transcript, t_start, t_end)` — used by timeline merger
- `count_words_in_range(transcript, t_start, t_end)` — for speech rate

**`server/tools/frames.py`**
- `detect_scene_timestamps(video_path)` → list of cut times (PySceneDetect)
- `extract_frame_as_base64(video_path, t)` → base64 JPEG (FFmpeg)
- `detect_animation(video_path, t_start, t_end)` → bool (OpenCV pixel diff)
- `get_keyframes(video_path, strategy, interval)` → list of frame dicts

**`server/tools/audio.py`** — `AudioAnalyzer` class
- Loads full WAV once via librosa; slices for per-segment analysis
- `analyze_segment(t_start, t_end)` → `{energy, music, tempo_bpm, rms_db}`
- `analyze_full(segment_duration)` → list of fixed-window segments

**`server/tools/timeline.py`**
- `build_timeline(video_path, audio_path, transcript, include_frames)` → segments
- Aligns all signals by scene-cut boundaries (min 5s per segment)
- Each segment: `{t_start, t_end, transcript, keyframe, scene_change, animation_detected, audio}`

### MCP Tools Exposed

| Tool | Description |
|---|---|
| `get_video_transcript` | Whisper transcript with word timestamps |
| `get_video_frames` | Keyframes at scene cuts or fixed intervals |
| `get_audio_features` | Energy / tempo / music detection per window |
| `get_full_context` | Unified timeline — the primary tool for full video awareness |

### Output Schema (`get_full_context`)

```json
{
  "title": "How Transformers Work",
  "duration": 847,
  "segments": [
    {
      "t_start": 0,
      "t_end": 12,
      "transcript": "Welcome to this video...",
      "keyframe": "<base64 JPEG or null>",
      "scene_change": false,
      "animation_detected": false,
      "audio": {
        "energy": "low",
        "speech_rate": "slow",
        "music": true,
        "tempo_bpm": 0.0,
        "rms_db": -28.4
      }
    }
  ]
}
```

## Prerequisites

```bash
# System dependency (required)
brew install ffmpeg        # macOS
# apt install ffmpeg       # Ubuntu/Debian

# Python 3.10+
python3 --version
```

## Setup

```bash
pip install -r requirements.txt
```

Whisper model weights are downloaded automatically on first transcription call (~75MB for `base`, ~1.5GB for `large`).

## Development Commands

```bash
# Run the MCP server directly
python server/main.py

# Quick smoke test
python -c "
from server.utils.downloader import VideoDownloader
from server.tools.transcript import get_transcript
d = VideoDownloader()
vp, ap, info = d.download('https://www.youtube.com/watch?v=jNQXAC9IVRw')
print(get_transcript(ap))
"
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `YT_CACHE_DIR` | `/tmp/yt-analysis-cache` | Cache directory for downloaded videos |

## MCP Integration with Claude Code

```bash
claude mcp add -s user yt-mcp -- python /path/to/server/main.py
```

The server uses stdio transport (stdin/stdout JSON-RPC 2.0). No API keys needed.

## Context Window Notes

- `get_full_context` with `include_frames=False` (default) is safe for any video length
- `include_frames=True` embeds base64 JPEGs — use only for short clips or specific segments
- For long videos: call `get_full_context` first to understand structure, then `get_video_frames` for timestamps of interest
