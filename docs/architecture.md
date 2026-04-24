# Architecture Overview

This document explains the overall system design of `yt-mcp`, the data flow through each pipeline, and the key design decisions made along the way.

---

## Two Servers, One Purpose

`yt-mcp` is structured as two independent MCP servers that solve the same problem from opposite directions:

```
┌─────────────────────────────────────────────────────┐
│                    AI Assistant                      │
│              (Claude Code / Desktop)                 │
└──────────────────┬──────────────────────────────────┘
                   │  MCP stdio (JSON-RPC 2.0)
          ┌────────┴────────┐
          │                 │
┌─────────▼──────┐  ┌───────▼────────────┐
│  Python Server │  │ TypeScript Server  │
│  server/       │  │ src/               │
│                │  │                    │
│  Local only    │  │ Gemini API         │
│  No API keys   │  │ Requires key       │
│  Heavy: FFmpeg │  │ Light: URL → AI    │
│  Whisper, etc. │  │                    │
└────────────────┘  └────────────────────┘
```

The two servers complement each other:
- **Python server** gives you raw, verifiable signal: exact word timestamps, actual audio dB levels, real pixel diffs. Useful for research, content analysis, or when you need ground truth.
- **TypeScript server** gives you fast semantic understanding: summaries, Q&A, AI-selected screenshots — without downloading anything locally. Useful for quick exploration.

---

## Python Server Pipeline

### End-to-end data flow

```
youtube_url
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ VideoDownloader (server/utils/downloader.py)         │
│                                                     │
│  1. yt_dlp.extract_info() → video metadata          │
│  2. Check cache: /tmp/yt-analysis-cache/<id>/       │
│     ├─ video.mp4  (downloaded video)                │
│     ├─ audio.wav  (16kHz mono WAV)                  │
│     └─ info.json  (metadata snapshot)               │
│  3. If cache miss:                                  │
│     a. yt-dlp download → video.mp4                  │
│     b. ffmpeg: video.mp4 → audio.wav                │
│        (pcm_s16le, 16kHz, mono)                     │
└──────────────┬──────────────────────────────────────┘
               │ (video_path, audio_path, VideoInfo)
               │
    ┌──────────┴──────────────────────────────┐
    │                                         │
    ▼                                         ▼
┌───────────────────┐              ┌──────────────────────────┐
│ Transcript        │              │ Frames                   │
│ (tools/transcript)│              │ (tools/frames.py)        │
│                   │              │                          │
│ whisper.load_     │              │ PySceneDetect:           │
│   model(size)     │              │   ContentDetector        │
│ model.transcribe( │              │   → cut timestamps       │
│   word_timestamps │              │                          │
│   =True)          │              │ ffmpeg → JPEG at each    │
│                   │              │   timestamp (base64)     │
│ → {language,      │              │                          │
│    full_text,     │              │ OpenCV pixel diff:       │
│    segments[]}    │              │   sample 5 frames/window │
└───────────────────┘              │   mean diff > 3% → anim  │
                                   └──────────────────────────┘
                                               │
                                               ▼
                                   ┌──────────────────────────┐
                                   │ Audio                    │
                                   │ (tools/audio.py)         │
                                   │                          │
                                   │ librosa.load(16kHz)      │
                                   │                          │
                                   │ Per segment:             │
                                   │   rms → dB → energy lvl │
                                   │   beat_track → tempo     │
                                   │   hpss → harmonic ratio  │
                                   │   spectral_flatness      │
                                   │   → music bool           │
                                   └──────────────────────────┘
                                               │
    ┌──────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ Timeline Builder (tools/timeline.py)                │
│                                                     │
│  1. Get scene cut timestamps                        │
│  2. Merge cuts that are < 5s apart                  │
│     (avoids hundreds of micro-segments)             │
│  3. For each segment [t_start, t_end]:              │
│     ├─ transcript text overlapping this window      │
│     ├─ word count → speech rate (wpm classification)│
│     ├─ optional keyframe (base64 JPEG)              │
│     ├─ animation detection (OpenCV pixel diff)      │
│     └─ audio features (AudioAnalyzer.analyze_       │
│          segment)                                   │
└─────────────────────────────────────────────────────┘
               │
               ▼
         JSON response to MCP client
```

### Module map

```
server/
├── main.py               — FastMCP entry point; registers 4 tools
│                           handles errors and serializes responses
├── utils/
│   └── downloader.py     — VideoDownloader: yt-dlp + ffmpeg wrapper
│                           VideoInfo: metadata value object
│                           DownloadError: typed error class
└── tools/
    ├── transcript.py     — Whisper wrapper; in-process model cache
    ├── frames.py         — PySceneDetect, FFmpeg, OpenCV functions
    ├── audio.py          — AudioAnalyzer class (librosa)
    └── timeline.py       — build_timeline(): merges all signals
```

### Caching strategy

The downloader caches by YouTube video ID. The cache layout is:

```
/tmp/yt-analysis-cache/
└── <video_id>/
    ├── video.mp4    — original downloaded video
    ├── audio.wav    — 16kHz mono WAV extracted from video
    └── info.json    — serialized VideoInfo fields
```

All three files must exist for the cache to be considered valid. If any is missing, the full download+extraction pipeline runs again. The `YT_CACHE_DIR` environment variable lets you point to persistent storage so the cache survives reboots.

Whisper model weights are cached separately by the Whisper library in `~/.cache/whisper/`.

### Audio analysis design

The `AudioAnalyzer` class loads the full WAV file once into memory via `librosa.load` and then slices numpy arrays for each segment. This is much faster than re-reading the file per window, and 16kHz mono audio is compact — a 60-minute video is about 115 MB in RAM.

**Music vs speech detection** uses two complementary heuristics:

- **Harmonic ratio**: librosa's HPSS (Harmonic-Percussive Source Separation) splits the signal; speech is weakly harmonic while music is strongly harmonic.
- **Spectral flatness**: speech has uneven spectral distribution (peaks at formants); music is broader but more structured.

A segment is classified as music when `harmonic_ratio > 0.25 AND spectral_flatness < 0.15`. These thresholds were chosen empirically and can be adjusted in `audio.py`.

---

## TypeScript Server Pipeline

### End-to-end data flow

```
youtube_url
    │
    ├──────────────────────────────────────────────────┐
    │                                                  │
    ▼                                                  ▼
┌──────────────────────┐              ┌───────────────────────────┐
│ GeminiVideoClient    │              │ YouTubeMetadataClient     │
│ (gemini-client.ts)   │              │ (youtube-metadata.ts)     │
│                      │              │                           │
│ GoogleGenAI SDK      │              │ googleapis v3             │
│ Pass YouTube URL as  │              │ videos.list (snippet)     │
│ fileData.fileUri     │              │ → title, channel,         │
│                      │              │   publishedAt, thumbnail  │
│ Gemini fetches and   │              │                           │
│ understands video    │              │ Optional: falls back to   │
│ natively (no local   │              │ GEMINI_API_KEY if no      │
│   download needed)   │              │ YOUTUBE_API_KEY is set    │
│                      │              └───────────────────────────┘
│ Returns: text string │                          │
└──────────────────────┘                          │
    │                                             │
    └──────────────────┬──────────────────────────┘
                       │  Promise.all([metadata, analysis])
                       ▼
              Merged text response → MCP client

── For screenshot tools only ──────────────────────────────────────

    GeminiVideoClient.extractTimestamps()
        │ Returns TimestampResult:
        │   { timestamps: [{time_seconds, time_formatted, description}]
        │     video_duration_seconds }
        ▼
    ScreenshotExtractor.extractScreenshots()
        │
        ├─ checkDependencies() — yt-dlp + ffmpeg in PATH
        ├─ yt-dlp -f "bestvideo[height<=N]" -g URL  → stream URL
        └─ ffmpeg -ss <t> -i <stream_url> -vframes 1 → JPEG
               │
               ▼
        base64-encoded JPEG → MCP image content block
```

### Module map

```
src/
├── index.ts              — MCP Server entry point; routes tool calls
├── tools.ts              — TOOLS array: JSON Schema definitions for MCP
├── validators.ts         — Zod schemas for all tool inputs; URL parsing
├── gemini-client.ts      — GeminiVideoClient: wraps @google/genai SDK
│                           VideoAnalysisError, VideoAccessError
├── screenshot-extractor.ts — ScreenshotExtractor: yt-dlp + ffmpeg wrapper
│                             DependencyError, ScreenshotExtractionError
└── youtube-metadata.ts   — YouTubeMetadataClient: googleapis wrapper
```

### Input validation

All tool inputs pass through [Zod](https://zod.dev) schemas defined in `validators.ts` before reaching any business logic. This provides:
- Clear, machine-readable error messages surfaced directly in the MCP response
- A single source of truth for valid URL formats (the `YOUTUBE_URL_REGEX`)
- Type inference from schema to implementation — `z.infer<typeof SummarizeInputSchema>` gives a fully typed object

### Error taxonomy

Both servers use typed error hierarchies to produce actionable user-facing messages:

**TypeScript:**
```
Error
└── VideoAnalysisError     — Gemini analysis failure (base class)
    └── VideoAccessError   — Video is private or geo-restricted
DependencyError            — yt-dlp or ffmpeg not found in PATH
ScreenshotExtractionError  — Frame extraction failure (includes timestamp)
```

**Python:**
```
Exception
└── DownloadError          — yt-dlp or ffmpeg failure
```

All errors are caught at the MCP handler layer and serialized so the AI assistant sees a structured `{"error": "..."}` message rather than a raw exception traceback.

---

## MCP Transport

Both servers use **stdio transport** (standard input/output). The MCP client spawns the server as a subprocess and communicates via JSON-RPC 2.0 on stdin/stdout. Stderr is used for diagnostic logging only and is never read by the client.

Benefits of this design:
- No network ports to configure or secure
- The server process lifecycle is managed by the MCP client
- Multiple server instances can run in parallel without conflicts

---

## Key Design Decisions

### Why two separate servers?

The local Python server and the cloud TypeScript server have fundamentally different dependency and operational profiles. Bundling them would force users who only want quick Gemini summaries to install FFmpeg, Whisper, and 2+ GB of ML model weights — and vice versa. Keeping them separate lets each server be installed and used independently.

### Why 16kHz mono WAV?

OpenAI Whisper was trained on 16kHz audio and internally resamples to this rate. librosa also works best at a consistent sample rate. Extracting once at 16kHz mono via FFmpeg:
- Saves disk space (~28 MB/hour vs ~180 MB/hour for CD quality stereo)
- Avoids redundant resampling on every analysis call
- Ensures Whisper and librosa see identical audio data

### Why PySceneDetect `ContentDetector`?

`ContentDetector` compares HSV histograms between adjacent frames and fires when the difference exceeds a threshold. It is robust to gradual zooms and pans (which `ThresholdDetector` false-positives on) while being fast enough to run on CPU. The default threshold of 27.0 balances sensitivity vs false positives for typical YouTube content.

### Why minimum 5-second segments in the timeline?

Rapid-fire cuts (common in trailers, music videos, quick tutorials) can produce 50+ scene boundaries per minute. Enforcing a 5-second minimum in `build_timeline()` prevents hundreds of tiny segments that would bloat the context window. The threshold is configurable via the `min_segment_sec` parameter.

### Why `include_frames=False` by default?

A base64 JPEG at 1280px wide is roughly 80–150 KB of text in the JSON response. A 30-minute video with a scene cut every 10 seconds would produce ~180 frames, totaling 15–25 MB of base64 text — exceeding most context window budgets. The default-off behavior makes the timeline safe for any video length, and the AI can request frames explicitly only for moments that need visual inspection.
