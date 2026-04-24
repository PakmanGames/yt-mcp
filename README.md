# yt-mcp

A collection of two complementary MCP (Model Context Protocol) servers that give AI assistants deep, multi-modal awareness of YouTube videos.

| | Python Server (`server/`) | TypeScript Server (`src/`) |
|---|---|---|
| **Approach** | Fully local processing | Cloud via Gemini API |
| **API Key Required** | No | Yes (Gemini) |
| **Capabilities** | Transcript · Frames · Audio · Unified timeline | Summarize · Q&A · Smart screenshot extraction |
| **Best for** | Privacy, offline use, detailed signal extraction | Fast answers, no local dependencies |
| **Language** | Python 3.10+ | Node.js 18+ |

Both servers implement the [MCP stdio transport](https://modelcontextprotocol.io) and can be used side-by-side in Claude Code or Claude Desktop.

---

## Table of Contents

- [Python Server — Local Pipeline](#python-server--local-pipeline)
  - [How it works](#how-it-works)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [MCP integration](#mcp-integration-python)
  - [Tools](#tools-python)
- [TypeScript Server — Gemini API](#typescript-server--gemini-api)
  - [How it works](#how-it-works-1)
  - [Prerequisites](#prerequisites-1)
  - [Installation](#installation-1)
  - [MCP integration](#mcp-integration-typescript)
  - [Tools](#tools-typescript)
- [Supported URL Formats](#supported-url-formats)
- [Environment Variables](#environment-variables)
- [Development](#development)
- [Architecture](#architecture)
- [License](#license)

---

## Python Server — Local Pipeline

### How it works

```
YouTube URL
    │
    ▼
yt-dlp ──────────────── download video.mp4
    │                   extract audio.wav (16 kHz mono)
    ▼
Whisper ─────────────── timestamped transcript (word-level)
    │
    ▼
PySceneDetect ────────── detect scene-cut timestamps
    │
    ▼
FFmpeg ──────────────── extract keyframe JPEGs at scene cuts
    │
    ▼
OpenCV ──────────────── pixel-diff animation detection
    │
    ▼
librosa ─────────────── energy · tempo · music vs speech
    │
    ▼
timeline.py ─────────── unified JSON timeline (all signals, time-aligned)
```

All results are cached in `/tmp/yt-analysis-cache/<video_id>/`. Re-calling the same URL is instant.

### Prerequisites

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Verify
ffmpeg -version
python3 --version   # must be 3.10+
```

### Installation

```bash
git clone https://github.com/yourusername/yt-mcp.git
cd yt-mcp
pip install -r requirements.txt
```

Whisper model weights download automatically on the first transcription call (~75 MB for `base`, ~1.5 GB for `large`).

### MCP Integration (Python)

**Claude Code:**
```bash
claude mcp add -s user yt-local -- python /path/to/yt-mcp/server/main.py
```

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "yt-local": {
      "command": "python",
      "args": ["/path/to/yt-mcp/server/main.py"]
    }
  }
}
```

### Tools (Python)

#### `get_video_transcript`

Transcribe a YouTube video using OpenAI Whisper (runs entirely locally).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `youtube_url` | string | — | Full YouTube URL |
| `model_size` | string | `base` | `tiny` · `base` · `small` · `medium` · `large` |

**Response:**
```json
{
  "title": "Video Title",
  "duration": 847,
  "language": "en",
  "full_text": "Welcome to this video...",
  "segments": [
    {
      "t_start": 0.0,
      "t_end": 4.5,
      "text": "Welcome to this video.",
      "words": [{ "word": "Welcome", "start": 0.0, "end": 0.6 }]
    }
  ]
}
```

#### `get_video_frames`

Extract keyframes as base64-encoded JPEGs. Uses PySceneDetect for scene detection and FFmpeg for extraction.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `youtube_url` | string | — | Full YouTube URL |
| `strategy` | string | `scene` | `scene` · `interval` · `both` |
| `interval` | integer | `30` | Seconds between frames (for `interval` or `both` strategies) |

**Response:**
```json
{
  "title": "Video Title",
  "duration": 847,
  "duration_formatted": "14:07",
  "frame_count": 12,
  "strategy": "scene",
  "frames": [
    {
      "t": 0.0,
      "t_formatted": "0:00",
      "keyframe": "<base64 JPEG>",
      "scene_change": false,
      "animation_detected": false
    }
  ],
  "summary": [ /* same list without keyframe bytes — for quick review */ ]
}
```

#### `get_audio_features`

Analyze audio characteristics using librosa (runs locally).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `youtube_url` | string | — | Full YouTube URL |
| `segment_duration` | integer | `30` | Analysis window size in seconds |

**Response:**
```json
{
  "title": "Video Title",
  "duration": 847,
  "segment_duration": 30,
  "segments": [
    {
      "t_start": 0.0,
      "t_end": 30.0,
      "energy": "medium",
      "music": false,
      "tempo_bpm": 95.0,
      "rms_db": -22.1
    }
  ]
}
```

#### `get_full_context`

**Primary tool.** Returns a complete, synchronized multi-modal timeline — transcript + scene boundaries + animation detection + audio features, all time-aligned.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `youtube_url` | string | — | Full YouTube URL |
| `include_frames` | boolean | `false` | Embed base64 keyframes per segment |
| `model_size` | string | `base` | Whisper model size |

**Response:**
```json
{
  "title": "How Transformers Work",
  "channel": "AI Explained",
  "duration": 847,
  "duration_formatted": "14:07",
  "language": "en",
  "description": "In this video...",
  "segments": [
    {
      "t_start": 0.0,
      "t_end": 12.0,
      "transcript": "Welcome to this video on transformers...",
      "keyframe": null,
      "scene_change": false,
      "animation_detected": false,
      "audio": {
        "energy": "low",
        "speech_rate": "normal",
        "music": true,
        "tempo_bpm": 0.0,
        "rms_db": -28.4
      }
    }
  ]
}
```

> **Context window tip:** Call `get_full_context` with `include_frames=false` first to understand the video structure, then call `get_video_frames` for specific timestamps of interest.

---

## TypeScript Server — Gemini API

### How it works

```
YouTube URL
    │
    ▼
GeminiVideoClient ────── pass URL directly to Gemini (no local download)
    │                    Gemini fetches and understands the video natively
    ▼
YouTubeMetadataClient ── optional: fetch title/channel via YouTube Data API v3
    │
    ▼
ScreenshotExtractor ──── yt-dlp + ffmpeg for frame extraction at timestamps
                         (only needed for extract_screenshots / extract_frames)
```

Gemini processes the video natively by URL — no local video download is needed for summarization or Q&A.

### Prerequisites

- **Node.js 18+**
- **Gemini API key** — get one at [Google AI Studio](https://aistudio.google.com/apikey)
- **yt-dlp + ffmpeg** — only needed for the `extract_screenshots` and `extract_frames` tools

```bash
# macOS (for screenshot tools)
brew install yt-dlp ffmpeg

# Verify
node --version   # must be 18+
```

### Installation

```bash
git clone https://github.com/yourusername/yt-mcp.git
cd yt-mcp
pnpm install
pnpm build
```

Copy the example environment file and fill in your key:
```bash
cp .env.example .env
# Edit .env and set GEMINI_API_KEY=your-key-here
```

### MCP Integration (TypeScript)

**Claude Code:**
```bash
claude mcp add -s user -e GEMINI_API_KEY=your-key yt-gemini -- node /path/to/yt-mcp/dist/index.js
```

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "yt-gemini": {
      "command": "node",
      "args": ["/path/to/yt-mcp/dist/index.js"],
      "env": {
        "GEMINI_API_KEY": "your-key"
      }
    }
  }
}
```

### Tools (TypeScript)

#### `summarize_video`

Summarize a YouTube video using Gemini. No local download required.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `youtube_url` | string | — | Full YouTube URL |
| `detail_level` | string | `medium` | `brief` · `medium` · `detailed` |

#### `ask_about_video`

Ask a specific question about a video's content.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `youtube_url` | string | — | Full YouTube URL |
| `question` | string | — | Your question about the video |

#### `get_video_timestamps`

Preview mode: identify important moments without extracting frames. Use this before `extract_screenshots` to preview timestamp selection.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `youtube_url` | string | — | Full YouTube URL |
| `count` | number | `5` | Number of timestamps to identify (1–20) |
| `focus` | string | — | Optional focus hint (e.g. `"product demos"`, `"code examples"`) |

#### `extract_screenshots`

Extract key frames at AI-identified important moments. Returns base64 images and optionally saves to disk.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `youtube_url` | string | — | Full YouTube URL |
| `count` | number | `5` | Number of screenshots (1–20) |
| `output_dir` | string | — | Directory to save files (optional) |
| `focus` | string | — | Focus hint for timestamp selection |
| `resolution` | string | `large` | `thumbnail` (160p) · `small` (360p) · `medium` (720p) · `large` (1080p) · `full` |

#### `extract_frames`

Extract frames at timestamps you specify manually — use when you already know which moments you want.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `youtube_url` | string | — | Full YouTube URL |
| `timestamps` | number[] | — | Array of seconds, e.g. `[5, 30, 120]` (1–20 entries) |
| `output_dir` | string | — | Directory to save files (optional) |
| `resolution` | string | `large` | Same options as `extract_screenshots` |

---

## Supported URL Formats

Both servers accept any of these YouTube URL formats:

```
https://www.youtube.com/watch?v=VIDEO_ID
https://youtu.be/VIDEO_ID
https://youtube.com/shorts/VIDEO_ID
```

---

## Environment Variables

### Python Server

| Variable | Default | Description |
|----------|---------|-------------|
| `YT_CACHE_DIR` | `/tmp/yt-analysis-cache` | Cache directory for downloaded videos and audio |

### TypeScript Server

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | — | **Required.** Your Gemini API key |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Gemini model identifier |
| `YOUTUBE_API_KEY` | falls back to `GEMINI_API_KEY` | YouTube Data API v3 key for metadata enrichment |
| `SCREENSHOT_OUTPUT_DIR` | system temp dir | Default directory for saved screenshots |

---

## Development

### Python Server

```bash
# Run the server directly (stdio mode — same as MCP clients use)
python server/main.py

# Quick smoke test
python -c "
from server.utils.downloader import VideoDownloader
from server.tools.transcript import get_transcript
d = VideoDownloader()
vp, ap, info = d.download('https://www.youtube.com/watch?v=jNQXAC9IVRw')
print(get_transcript(ap)['language'])
"
```

### TypeScript Server

```bash
# Development mode (tsx, no build step needed)
pnpm dev

# Compile TypeScript → dist/
pnpm build

# Run compiled server
pnpm start

# Run all tests
pnpm test

# Run tests once (CI mode)
pnpm test:run
```

---

## Architecture

For a detailed explanation of system design, data flows, and how to add new tools:

- [**docs/architecture.md**](docs/architecture.md) — overall system design and pipeline diagrams
- [**docs/python-server.md**](docs/python-server.md) — Python local server component reference
- [**docs/typescript-server.md**](docs/typescript-server.md) — TypeScript Gemini server component reference
- [**docs/extending.md**](docs/extending.md) — how to add new tools to either server

---

## License

[MIT](LICENSE)
