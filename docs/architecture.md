# Architecture Overview

This document explains the overall system design of `yt-mcp`, the data flow through the processing pipeline, and the key design decisions made along the way.

> **Scope:** This document covers the Python server (`server/`), which is the primary implementation. The TypeScript server (`src/`) is not under active development; its design is described briefly at the [end of this document](#typescript-server--not-actively-developed) for reference.

---

## How the server fits into MCP

`yt-mcp` is a local MCP server. The AI assistant (Claude Code or Claude Desktop) spawns it as a subprocess and communicates over stdin/stdout via JSON-RPC 2.0. The server holds no network ports and requires no API keys — every dependency (FFmpeg, Whisper, PySceneDetect, librosa) runs on-device.

The diagram below shows a typical `get_full_context` call. The first call pays the download + transcription cost; every later call for the same video is served from the on-disk cache.

```mermaid
sequenceDiagram
    autonumber
    participant A as AI Assistant<br/>(Claude Code / Desktop)
    participant S as yt-mcp server<br/>(FastMCP, stdio)
    participant D as VideoDownloader
    participant C as Disk cache
    participant Y as YouTube

    A->>S: initialize / tools/list (JSON-RPC)
    S-->>A: 4 tools advertised
    A->>S: tools/call get_full_context(url)
    S->>D: download(url)
    D->>C: cache hit?
    alt cache miss (first call)
        D->>Y: yt-dlp download video.mp4
        D->>D: ffmpeg → audio.wav (16 kHz mono)
        D->>C: write video.mp4 · audio.wav · info.json
    else cache hit
        C-->>D: existing paths + info.json
    end
    D-->>S: (video_path, audio_path, VideoInfo)
    S->>S: Whisper + PySceneDetect + librosa + build_timeline
    S-->>A: JSON timeline (text content)
```

> Errors never escape as tracebacks: each pipeline stage is wrapped so the assistant always receives a structured `{"error": "..."}` payload instead. See [Error taxonomy](#error-taxonomy).

---

## Python Server Pipeline

### End-to-end data flow

`get_full_context` is the widest path through the system: it touches every stage. The other three tools are slices of the same graph (transcript-only, frames-only, audio-only).

```mermaid
flowchart TD
    URL([youtube_url]) --> DL

    subgraph dl["VideoDownloader · server/utils/downloader.py"]
        DL["yt_dlp.extract_info() → video_id"]
        DL --> HIT{"cache complete?<br/>video.mp4 + audio.wav + info.json"}
        HIT -->|miss| GET["yt-dlp download → video.mp4<br/>ffmpeg → audio.wav (pcm_s16le, 16 kHz, mono)"]
        HIT -->|hit| LOAD["load info.json"]
        GET --> OUT0
        LOAD --> OUT0(["(video_path, audio_path, VideoInfo)"])
    end

    OUT0 --> TR
    OUT0 --> FR
    OUT0 --> AU

    subgraph tr["Transcript · tools/transcript.py"]
        TR["whisper.load_model(size)<br/>model.transcribe(word_timestamps=True)<br/>→ language · full_text · segments[]"]
    end

    subgraph fr["Frames · tools/frames.py"]
        FR["PySceneDetect ContentDetector → cut timestamps<br/>ffmpeg → base64 JPEG per timestamp<br/>OpenCV pixel diff: 5 samples/window, mean diff &gt; 3% → animation"]
    end

    subgraph au["Audio · tools/audio.py"]
        AU["librosa.load(16 kHz) once<br/>per segment: rms→dB→energy · beat_track→tempo<br/>hpss harmonic ratio + spectral flatness → music"]
    end

    TR --> TLB
    FR --> TLB
    AU --> TLB

    subgraph tl["Timeline Builder · tools/timeline.py"]
        TLB["1. scene cuts → boundaries (merge cuts &lt; 5 s apart)<br/>2. per segment: transcript text · word count → speech rate<br/>· optional keyframe · animation · audio features"]
    end

    TLB --> RESP([JSON response → MCP client])

    classDef io fill:#d1e7dd,stroke:#0f5132,color:#03190f;
    class URL,RESP io;
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

### Component model (UML)

The runtime is built from one stateful downloader, one audio analyzer instantiated per call, three stateless tool modules, and the FastMCP entry point that wires them together. `main.py` holds a single shared `VideoDownloader` so the disk cache is reused across every tool call in a session.

```mermaid
classDiagram
    class main {
        <<FastMCP entry>>
        +get_video_transcript(url, model_size) str
        +get_video_frames(url, strategy, interval) str
        +get_audio_features(url, segment_duration) str
        +get_full_context(url, include_frames, model_size) str
    }
    class VideoDownloader {
        +Path cache_dir
        +download(url) tuple
        +clear_cache(video_id) None
        -_extract_info(url) dict
    }
    class VideoInfo {
        +str id
        +str title
        +float duration
        +str channel
        +str upload_date
        +str description
        +str url
    }
    class DownloadError {
        <<Exception>>
    }
    class AudioAnalyzer {
        +int SR
        +ndarray y
        +analyze_segment(t_start, t_end) dict
        +analyze_full(segment_duration) list
    }
    class transcript {
        <<module>>
        +get_transcript(audio_path, model_size) dict
        +get_text_in_range(transcript, t_start, t_end) str
        +count_words_in_range(transcript, t_start, t_end) int
    }
    class frames {
        <<module>>
        +get_keyframes(video_path, strategy, interval) list
        +detect_scene_timestamps(video_path, threshold) list
        +extract_frame_as_base64(video_path, timestamp, width) str
        +detect_animation(video_path, t_start, t_end, samples) bool
        +get_video_duration(video_path) float
        +format_time(seconds) str
    }
    class timeline {
        <<module>>
        +build_timeline(video_path, audio_path, transcript, include_frames, min_segment_sec) list
    }

    main ..> VideoDownloader : shared singleton
    main ..> transcript : calls
    main ..> frames : calls
    main ..> AudioAnalyzer : calls
    main ..> timeline : calls
    VideoDownloader ..> VideoInfo : returns
    VideoDownloader ..> DownloadError : raises
    timeline ..> transcript : text / word count
    timeline ..> frames : scenes · keyframe · animation
    timeline ..> AudioAnalyzer : analyze_segment
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

## TypeScript Server (not actively developed)

> The TypeScript server (`src/`) is kept for reference only and is not under active development. The Python server above is the implementation to use.

### End-to-end data flow

For `summarize_video` and `ask_about_video`, metadata and analysis are fetched concurrently and merged:

```mermaid
flowchart TD
    URL([youtube_url]) --> P{{"Promise.all"}}

    P --> G["GeminiVideoClient · gemini-client.ts<br/>GoogleGenAI SDK<br/>URL passed as fileData.fileUri<br/>(Gemini fetches video natively — no local download)"]
    P --> M["YouTubeMetadataClient · youtube-metadata.ts<br/>googleapis videos.list(snippet)<br/>→ title · channel · publishedAt · thumbnail<br/>(optional; falls back to GEMINI_API_KEY)"]

    G --> MERGE["merge"]
    M --> MERGE
    MERGE --> RESP([Text response → MCP client])

    classDef io fill:#d1e7dd,stroke:#0f5132,color:#03190f;
    class URL,RESP io;
```

The screenshot tools (`extract_screenshots`, `get_video_timestamps`, `extract_frames`) take a different path — Gemini picks timestamps, then frames are pulled locally:

```mermaid
flowchart TD
    URL([youtube_url]) --> TS["GeminiVideoClient.extractTimestamps()<br/>→ TimestampResult { timestamps[], video_duration_seconds }"]
    TS --> SE["ScreenshotExtractor.extractScreenshots()"]
    SE --> DEP["checkDependencies() — yt-dlp + ffmpeg in PATH"]
    DEP --> STREAM["yt-dlp -f 'bestvideo[height&lt;=N]' -g URL → stream URL"]
    STREAM --> FF["ffmpeg -ss t -i stream_url -vframes 1 → JPEG"]
    FF --> RESP([base64 JPEG → MCP image content block])

    classDef io fill:#d1e7dd,stroke:#0f5132,color:#03190f;
    class URL,RESP io;
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

The Python server uses a typed error hierarchy to produce actionable user-facing messages:

```
Exception
└── DownloadError          — yt-dlp or ffmpeg failure
```

All errors are caught at the MCP handler layer and serialized so the AI assistant always sees a structured `{"error": "..."}` message rather than a raw Python traceback.

---

## MCP Transport

Both servers use **stdio transport** (standard input/output). The MCP client spawns the server as a subprocess and communicates via JSON-RPC 2.0 on stdin/stdout. Stderr is used for diagnostic logging only and is never read by the client.

Benefits of this design:
- No network ports to configure or secure
- The server process lifecycle is managed by the MCP client
- Multiple server instances can run in parallel without conflicts

---

## Key Design Decisions

### Why fully local?

Running everything on-device means no API keys, no data leaving the machine, and deterministic output. The signals produced (exact word timestamps, real audio dB levels, actual pixel diffs) are verifiable ground truth rather than AI-generated approximations — which matters for research and content analysis use cases.

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
