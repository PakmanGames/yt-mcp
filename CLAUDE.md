# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Two Implementations

This repo contains two independent MCP servers with different architectures:

| | Python (`server/`) | TypeScript (`src/`) |
|---|---|---|
| Status | **Primary** | Archived — reference only |
| API keys | None required | `GEMINI_API_KEY` required |
| Video processing | Local (yt-dlp, Whisper, FFmpeg, librosa) | Cloud (Gemini native YouTube URL support) |
| Tools | 4 (transcript, frames, audio, full context) | 5 (summarize, ask, screenshots, timestamps, frames) |

## Python Server

### Architecture

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

**`server/utils/downloader.py`** — `VideoDownloader.download(url)` → `(video_path, audio_path, VideoInfo)`; caches by video ID

**`server/tools/transcript.py`** — Whisper transcription; `get_transcript`, `get_text_in_range`, `count_words_in_range`

**`server/tools/frames.py`** — `detect_scene_timestamps` (PySceneDetect), `extract_frame_as_base64` (FFmpeg), `detect_animation` (OpenCV), `get_keyframes`

**`server/tools/audio.py`** — `AudioAnalyzer`: loads WAV once via librosa; `analyze_segment(t_start, t_end)` → `{energy, music, tempo_bpm, rms_db}`

**`server/tools/timeline.py`** — `build_timeline`: aligns all signals by scene-cut boundaries (min 5s/segment); each segment has transcript, keyframe, scene_change, animation_detected, audio

### MCP Tools Exposed

| Tool | Description |
|---|---|
| `get_video_transcript` | Whisper transcript with word timestamps |
| `get_video_frames` | Keyframes at scene cuts or fixed intervals |
| `get_audio_features` | Energy / tempo / music detection per window |
| `get_full_context` | Unified timeline — primary tool for full video awareness |

### Setup

```bash
brew install ffmpeg   # macOS; apt install ffmpeg on Ubuntu
pip install -r requirements.txt
```

Whisper model weights download automatically on first use (~75MB for `base`, ~1.5GB for `large`).

### Development Commands

```bash
# Run server
python server/main.py

# Run all tests
pytest

# Run a single test file
pytest tests/test_audio.py

# Run a single test by name
pytest tests/test_audio.py::TestAudioAnalyzer::test_analyze_segment
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `YT_CACHE_DIR` | `/tmp/yt-analysis-cache` | Cache directory for downloaded videos |

### MCP Integration

```bash
claude mcp add -s user yt-mcp -- python /path/to/server/main.py
```

### Context Window Notes

- `get_full_context` with `include_frames=False` (default) is safe for any video length
- `include_frames=True` embeds base64 JPEGs — use only for short clips or specific segments

---

## TypeScript Server (Archived)

Not under active development. Kept for reference.

### Architecture

Gemini understands YouTube URLs natively, so `summarize_video` and `ask_about_video` send only the URL — no local download. The `extract_screenshots` and `extract_frames` tools use yt-dlp + ffmpeg locally to pull frames.

**`src/index.ts`** — MCP entry point; routes tool calls; exits on startup if `GEMINI_API_KEY` missing

**`src/gemini-client.ts`** — `GeminiVideoClient`: passes YouTube URL as `fileData.fileUri` to Gemini; handles summarize / ask / extractTimestamps

**`src/screenshot-extractor.ts`** — `ScreenshotExtractor`: runs `yt-dlp -g` to get stream URL (no full download), then `ffmpeg -ss` for fast frame extraction; partial failure tolerance (returns successful frames even if some fail)

**`src/validators.ts`** — Zod schemas for all tool inputs; `extractVideoId` handles `youtube.com/watch?v=`, `youtu.be/`, and `youtube.com/shorts/`

**`src/youtube-metadata.ts`** — Optional `YouTubeMetadataClient`; enriches responses with title/channel/date; omitted gracefully if no API key

### MCP Tools Exposed

| Tool | Required params | Optional params |
|---|---|---|
| `summarize_video` | `youtube_url` | `detail_level` (brief/medium/detailed) |
| `ask_about_video` | `youtube_url`, `question` | — |
| `extract_screenshots` | `youtube_url` | `count`, `output_dir`, `focus`, `resolution` |
| `get_video_timestamps` | `youtube_url` | `count`, `focus` |
| `extract_frames` | `youtube_url`, `timestamps[]` | `output_dir`, `resolution` |

### Setup

```bash
pnpm install
pnpm build
GEMINI_API_KEY=your-key node dist/index.js

# Or dev mode (no build step)
GEMINI_API_KEY=your-key pnpm dev
```

### Development Commands

```bash
pnpm test          # watch mode
pnpm test:run      # single run (CI)
pnpm build         # compile TypeScript → dist/
```

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `GEMINI_MODEL` | No | Model override (default: `gemini-3-flash-preview`) |
| `YOUTUBE_API_KEY` | No | YouTube Data API v3 key for metadata; falls back to `GEMINI_API_KEY` |
| `SCREENSHOT_OUTPUT_DIR` | No | Persistent output directory for saved frames |
