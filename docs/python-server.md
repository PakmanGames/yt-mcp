# Python Server — Component Reference

The Python server (`server/`) is a fully local MCP server. It downloads YouTube videos using yt-dlp, extracts multi-modal features using open-source models, and returns a structured JSON timeline to the AI assistant. **No API keys required.**

---

## Quick start

```bash
pip install -r requirements.txt
python server/main.py  # runs in stdio MCP mode
```

---

## Component Reference

### `server/main.py` — MCP entry point

Registers four MCP tools using [FastMCP](https://github.com/jlowin/fastmcp) and wires together the downloader, transcript, frames, audio, and timeline modules. All tools share a single `VideoDownloader` instance so the disk cache is reused across tool calls within a session.

**Shared instances (module-level):**
- `mcp = FastMCP("yt-mcp")` — the MCP server
- `downloader = VideoDownloader()` — shared downloader with disk cache

**Error handling pattern:** Every tool wraps each pipeline stage in a `try/except` and returns `json.dumps({"error": "..."})` on failure. This ensures the AI assistant always receives valid JSON, never a raw Python traceback.

---

### `server/utils/downloader.py` — Download and cache

#### `DownloadError`

Raised whenever yt-dlp or FFmpeg fails. Wraps the underlying error with a human-readable message.

#### `VideoInfo`

A simple value object holding metadata extracted from yt-dlp:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | YouTube video ID (e.g. `dQw4w9WgXcQ`) |
| `title` | `str` | Video title |
| `duration` | `float` | Duration in seconds |
| `channel` | `str` | Uploader name |
| `upload_date` | `str` | Upload date as `YYYYMMDD` string |
| `description` | `str` | First 500 characters of description |
| `url` | `str` | Original URL passed to `download()` |

#### `VideoDownloader`

```python
class VideoDownloader:
    def __init__(self, cache_dir: Optional[Path] = None)
```

| Method | Returns | Description |
|--------|---------|-------------|
| `download(url)` | `(video_path, audio_path, VideoInfo)` | Download video and extract audio; uses cache if available |
| `clear_cache(video_id)` | `None` | Delete cached files for a specific video ID |

**`download(url)` detail:**

1. Calls `yt_dlp.extract_info()` to get the video ID without downloading
2. Checks if `<cache_dir>/<video_id>/video.mp4`, `audio.wav`, and `info.json` all exist
3. If all three exist → loads `info.json` and returns immediately (cache hit)
4. Otherwise → downloads with yt-dlp, normalizes to `.mp4`, then runs FFmpeg to extract 16kHz mono WAV
5. Writes `info.json` and returns paths + `VideoInfo`

The WAV extraction command used:
```
ffmpeg -y -i video.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 audio.wav
```

---

### `server/tools/transcript.py` — Whisper transcription

#### `get_transcript(audio_path, model_size="base") → dict`

Runs OpenAI Whisper locally on the 16kHz WAV file. Models are cached in-process via the module-level `_model_cache` dict — the first call downloads weights; subsequent calls are instant.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `audio_path` | `str` | — | Path to a 16kHz mono WAV file |
| `model_size` | `str` | `"base"` | `tiny` · `base` · `small` · `medium` · `large` |

**Returns:**
```python
{
    "language": "en",          # detected language code
    "full_text": "...",        # full transcript as a single string
    "segments": [
        {
            "t_start": 0.0,    # segment start (seconds)
            "t_end": 4.5,      # segment end (seconds)
            "text": "...",     # segment text
            "words": [         # word-level timestamps
                {"word": "Hello", "start": 0.0, "end": 0.4}
            ]
        }
    ]
}
```

**Model size tradeoffs:**

| Model | Approx. size | Speed (CPU) | Accuracy |
|-------|-------------|-------------|---------|
| `tiny` | ~75 MB | Fastest | Lower |
| `base` | ~142 MB | Fast | Good (default) |
| `small` | ~466 MB | Moderate | Better |
| `medium` | ~1.5 GB | Slow | High |
| `large` | ~2.9 GB | Slowest | Best |

#### `get_text_in_range(transcript, t_start, t_end) → str`

Returns all transcript segment text that overlaps with the time range `[t_start, t_end]`. Used by `build_timeline()` to assign transcript text to each video segment.

#### `count_words_in_range(transcript, t_start, t_end) → int`

Counts individual words (using word-level timestamps) that fall within `[t_start, t_end]`. Used by `build_timeline()` to compute speech rate.

---

### `server/tools/frames.py` — Scene detection and frame extraction

#### `get_video_duration(video_path) → float`

Calls `ffprobe` to read the video stream duration in seconds. Used to know when to stop generating interval timestamps.

#### `detect_scene_timestamps(video_path, threshold=27.0) → list[float]`

Runs PySceneDetect's `ContentDetector` on the video. Returns sorted unique timestamps (in seconds) where scene cuts were detected.

- **`threshold`**: HSV histogram difference threshold. Higher = fewer detections (only major cuts). Lower = more detections (sensitive to small changes). Default `27.0` is a good balance for typical YouTube content.

#### `extract_frame_as_base64(video_path, timestamp, width=1280) → Optional[str]`

Extracts a single frame at `timestamp` seconds using FFmpeg, writes it to a temp JPEG file, reads and base64-encodes it, then deletes the temp file. Returns `None` if FFmpeg fails or the output file is empty.

FFmpeg command used:
```
ffmpeg -y -ss <timestamp> -i <video_path> -vframes 1 -vf scale=1280:-1 -q:v 3 <tmp.jpg>
```

#### `detect_animation(video_path, t_start, t_end, samples=5) → bool`

Samples `samples` frames evenly across `[t_start, t_end]`, converts each to grayscale, computes mean absolute pixel difference between adjacent frames, and returns `True` if the mean difference exceeds 3%.

This distinguishes animated segments (slideshows, screen recordings, motion graphics) from static talking-head segments where the background barely changes.

#### `format_time(seconds) → str`

Formats a duration as `M:SS` or `H:MM:SS`.

#### `get_keyframes(video_path, strategy="scene", interval=30, frame_width=1280) → list[dict]`

The main entry point for frame extraction. Builds a list of timestamps according to `strategy`, then extracts a frame and runs animation detection at each.

**`strategy` options:**
- `"scene"` — timestamps from `detect_scene_timestamps()` only
- `"interval"` — every `interval` seconds from 0 to end of video
- `"both"` — union of scene cut times and interval times

Always includes `t=0.0` (first frame). Timestamps within 1ms of each other are deduplicated.

**Returns:**
```python
[
    {
        "t": 12.0,
        "t_formatted": "0:12",
        "keyframe": "<base64 JPEG>",
        "scene_change": True,   # whether this timestamp is a detected scene cut
        "animation_detected": False
    }
]
```

---

### `server/tools/audio.py` — Audio feature extraction

#### `AudioAnalyzer`

```python
class AudioAnalyzer:
    SR = 16000  # sample rate, matches the WAV produced by downloader.py
    def __init__(self, audio_path: str)
```

Loads the entire WAV file into memory once via `librosa.load()`. All subsequent analysis calls slice the in-memory numpy array — no additional disk I/O per segment.

#### `analyze_segment(t_start, t_end) → dict`

Analyzes audio in the time window `[t_start, t_end]`.

**Energy classification:**

| RMS dB | Label |
|--------|-------|
| < -35 dB | `"low"` |
| -35 to -18 dB | `"medium"` |
| > -18 dB | `"high"` |

**Tempo:** Estimated via `librosa.beat.beat_track()`. Returns `0.0` if beat tracking fails (common for speech-only segments).

**Music detection:** Combines harmonic ratio (from HPSS) and spectral flatness. Returns `True` when `harmonic_ratio > 0.25 AND flatness < 0.15`.

**Minimum segment length:** Segments shorter than 100ms are returned as `{"energy": "low", "music": False, "tempo_bpm": 0.0, "rms_db": -60.0}` without running analysis, to avoid librosa edge cases on near-empty buffers.

**Returns:**
```python
{
    "energy": "medium",    # "low" | "medium" | "high"
    "music": False,        # bool
    "tempo_bpm": 95.0,     # float (0.0 if undetermined)
    "rms_db": -22.1        # float
}
```

#### `analyze_full(segment_duration=30) → list[dict]`

Runs `analyze_segment()` on non-overlapping windows of `segment_duration` seconds across the full audio. Returns a list of segment dicts with `t_start` and `t_end` prepended.

---

### `server/tools/timeline.py` — Unified timeline builder

#### `build_timeline(video_path, audio_path, transcript, include_frames=False, min_segment_sec=5.0) → list[dict]`

Merges all signal sources into a single time-aligned segment list.

**Algorithm:**

1. Get scene cut timestamps from `detect_scene_timestamps()`
2. Build segment boundaries: start at `0.0`, add each cut only if it is `>= min_segment_sec` after the last boundary (prevents micro-segments from rapid cuts)
3. Append `duration` as the final boundary
4. For each consecutive boundary pair `[t_start, t_end]`:
   - Extract transcript text via `get_text_in_range()`
   - Count words via `count_words_in_range()` → compute speech rate label
   - Extract keyframe via `extract_frame_as_base64()` (only if `include_frames=True`)
   - Run `detect_animation()` if segment is longer than 2 seconds
   - Run `AudioAnalyzer.analyze_segment()` and attach speech rate

**Speech rate classification:**

| WPM | Label |
|-----|-------|
| < 100 | `"slow"` |
| 100–160 | `"normal"` |
| > 160 | `"fast"` |

**Returns:** A list of segment dicts matching this schema:
```python
{
    "t_start": 0.0,
    "t_end": 12.0,
    "transcript": "Welcome to this video...",
    "keyframe": None,            # base64 JPEG or None
    "scene_change": False,       # True for all segments after the first
    "animation_detected": False, # True if significant pixel motion detected
    "audio": {
        "energy": "low",
        "speech_rate": "normal",
        "music": True,
        "tempo_bpm": 0.0,
        "rms_db": -28.4
    }
}
```

---

## Cache Management

To clear the cache for a specific video:
```python
from server.utils.downloader import VideoDownloader
downloader = VideoDownloader()
downloader.clear_cache("dQw4w9WgXcQ")  # pass the video ID
```

To clear all cached videos:
```bash
rm -rf /tmp/yt-analysis-cache/
# or, if you set a custom cache dir:
rm -rf "$YT_CACHE_DIR"
```
