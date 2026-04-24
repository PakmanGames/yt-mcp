# Testing Guide

This document covers the yt-mcp Python test suite: how to run it, how it is structured, how the mocking strategy works, and how to write tests when you add a new tool.

---

## Quick start

```bash
# Install test dependencies (adds pytest and pytest-mock on top of requirements.txt)
pip install -r requirements-dev.txt

# Run the full suite
python -m pytest

# Run with extra verbosity
python -m pytest -v

# Run a single file
python -m pytest tests/test_audio.py -v
```

Expected: **164 passed** in roughly 4 seconds (audio tests run fast because librosa model weights are already cached by your venv).

---

## Test files at a glance

| File | Module under test | Tests |
|---|---|---|
| `tests/test_downloader.py` | `server/utils/downloader.py` | 20 |
| `tests/test_transcript.py` | `server/tools/transcript.py` | 24 |
| `tests/test_frames.py` | `server/tools/frames.py` | 27 |
| `tests/test_audio.py` | `server/tools/audio.py` | 25 |
| `tests/test_timeline.py` | `server/tools/timeline.py` | 24 |
| `tests/test_main.py` | `server/main.py` (MCP handlers) | 34 |

All tests are pure unit tests. **No network access, no Whisper model downloads, no real video files** are required to run the suite. Every external tool (yt-dlp, FFmpeg, Whisper, librosa, PySceneDetect, OpenCV) is either mocked or replaced with a synthetic numpy signal.

---

## Shared fixtures (`tests/conftest.py`)

`conftest.py` provides fixtures shared across all test files:

| Fixture | Type | Description |
|---|---|---|
| `silence_array` | `np.ndarray` | 3 s of zeros at 16 kHz (very low energy) |
| `loud_tone_array` | `np.ndarray` | 3 s of summed harmonics at 440 Hz (high energy, music-like) |
| `medium_noise_array` | `np.ndarray` | 3 s of white noise at amplitude 0.05 (medium energy, speech-like) |
| `silence_wav` | `str` (path) | Temporary 16-bit PCM WAV file containing silence |
| `tone_wav` | `str` (path) | Temporary WAV file containing the loud tone array |
| `medium_wav` | `str` (path) | Temporary WAV file containing the medium noise array |
| `sample_transcript` | `dict` | Minimal transcript matching `get_transcript()` output |
| `fake_video_info_data` | `dict` | Synthetic `VideoInfo` fields |
| `cache_hit_dir` | `(Path, str)` | Temp dir pre-populated with `video.mp4`, `audio.wav`, `info.json` |

---

## Mocking strategy

### External processes (FFmpeg, ffprobe, yt-dlp)

These are called via `subprocess.run` or `yt_dlp.YoutubeDL`. Tests patch them at the call site:

```python
with patch("subprocess.run") as mock_run:
    mock_run.return_value = MagicMock(returncode=0, stdout='{"streams": [...]}')
    result = get_video_duration("fake.mp4")
```

### Locally-imported heavy packages (cv2, scenedetect, whisper)

The source code imports these **inside function bodies** (`import cv2` at the top of `detect_animation`, etc.), which means standard `patch("server.tools.frames.cv2")` won't work — the name doesn't exist at module level.

The solution is `patch.dict(sys.modules, ...)`, which injects a `MagicMock` into the module registry so the local `import` statement returns the mock:

```python
import sys
from unittest.mock import MagicMock, patch

fake_cv2 = MagicMock()
fake_cv2.CAP_PROP_POS_MSEC = 0
fake_cv2.COLOR_BGR2GRAY = 6

with patch.dict(sys.modules, {"cv2": fake_cv2}):
    result = detect_animation("fake.mp4", 0.0, 10.0)
```

The helper functions `_inject_fake_scenedetect()` and `_inject_fake_cv2()` in `test_frames.py` encapsulate this pattern for reuse across that file.

### AudioAnalyzer (librosa)

`AudioAnalyzer.__init__` calls `librosa.load()`. Tests patch this to inject a synthetic numpy array, then let the **real** librosa analysis run on it — this exercises the actual energy/music classification logic without needing a real WAV file:

```python
from unittest.mock import patch
import numpy as np

SR = 16_000
t = np.linspace(0, 3, 3 * SR, endpoint=False)
y = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)  # loud 440 Hz tone

with patch("librosa.load", return_value=(y, SR)):
    analyzer = AudioAnalyzer("dummy.wav")

result = analyzer.analyze_segment(0.0, 3.0)
assert result["energy"] == "high"
assert result["music"] is True
```

### MCP tool handlers (`server/main.py`)

The MCP tool functions (`get_video_transcript`, `get_video_frames`, etc.) are regular Python callables even after being decorated with `@mcp.tool()`. Tests call them directly and mock the module-level `downloader` singleton via `patch.object`:

```python
import server.main as main_module
from server.main import get_video_transcript

with patch.object(main_module.downloader, "download", side_effect=DownloadError("404")):
    result = json.loads(get_video_transcript("https://www.youtube.com/watch?v=bad"))

assert "error" in result
```

---

## Example: live smoke test against a real video

The unit tests verify behaviour without network access. To verify the full pipeline end-to-end, run a live smoke test against a real video.

The example below uses [プリマドンナ / 星街すいせい](https://www.youtube.com/watch?v=M1GYqy0tHV0) (Hoshimachi Suisei · Suisei Channel, 2:52), a Japanese animated music video published March 2026. It is a useful reference because it stresses every part of the pipeline simultaneously:

| Pipeline stage | Why this video exercises it |
|---|---|
| `VideoDownloader` | Short video (172 s) — fast to download and cache |
| `get_transcript` (Whisper) | Japanese audio — verifies multilingual detection returns `language: "ja"` |
| `detect_scene_timestamps` | Animated music video has frequent cuts — typically 30–60 scene boundaries |
| `detect_animation` | OpenCV pixel-diff sees large frame changes between animation shots |
| `AudioAnalyzer` | Identifies audio as music (`music: True`) with high energy |
| `build_timeline` | Rapid cuts are collapsed to `min_segment_sec=5` windows, producing ~8 segments |

```python
from server.utils.downloader import VideoDownloader
from server.tools.transcript import get_transcript
from server.tools.audio import AudioAnalyzer
from server.tools.frames import detect_scene_timestamps, format_time
from server.tools.timeline import build_timeline

URL = "https://www.youtube.com/watch?v=M1GYqy0tHV0"

# Step 1: Download (results cached after first run)
d = VideoDownloader()
video_path, audio_path, info = d.download(URL)
print(f"Title:    {info.title}")
print(f"Channel:  {info.channel}")
print(f"Duration: {format_time(info.duration)}")

# Step 2: Transcribe (Whisper base model, ~1 min on CPU)
transcript = get_transcript(audio_path, model_size="base")
print(f"Language: {transcript['language']}")      # ja
print(f"Excerpt:  {transcript['full_text'][:80]}")

# Step 3: Audio features
analyzer = AudioAnalyzer(audio_path)
seg0 = analyzer.analyze_segment(0, 30)
print(f"First 30s energy: {seg0['energy']}")      # 'high'
print(f"First 30s music:  {seg0['music']}")       # True
print(f"First 30s tempo:  {seg0['tempo_bpm']} bpm")

# Step 4: Scene cuts
cuts = detect_scene_timestamps(video_path)
print(f"Scene cuts: {len(cuts)}")                 # typically 30–60

# Step 5: Unified timeline (no frames, stays within any context window)
segments = build_timeline(video_path, audio_path, transcript, include_frames=False)
print(f"Timeline segments: {len(segments)}")
for seg in segments[:3]:
    print(f"  [{seg['t_start']:.1f}s–{seg['t_end']:.1f}s] "
          f"energy={seg['audio']['energy']} "
          f"music={seg['audio']['music']} "
          f"text={seg['transcript'][:40]!r}")
```

Expected output:

```
Title:    プリマドンナ / 星街すいせい(official)
Channel:  Suisei Channel
Duration: 2:52
Language: ja
Excerpt:  はい 。 取り締めしたい。その心を幕がれば...
First 30s energy: high
First 30s music:  True
First 30s tempo:  ~120.0 bpm
Scene cuts: ~45
Timeline segments: ~8
  [0.0s–8.3s] energy=high music=True text='はい 。 取り締めしたい...'
  [8.3s–14.1s] energy=high music=True text='待ってる人がいるのところで...'
  [14.1s–21.0s] energy=high music=True text='今どんなにはもうふざりだわ...'
```

> Results vary slightly with Whisper model size and PySceneDetect threshold. Use `model_size="small"` for more accurate Japanese transcription at the cost of extra RAM and time.

---

## Writing tests for a new tool

When you add a new MCP tool following [docs/extending.md](extending.md), add tests in `tests/test_<feature>.py`.

### Checklist

1. **Happy path** — mock all external calls and assert the return dict has the correct keys and values.
2. **`DownloadError`** — patch `main_module.downloader.download` to raise `DownloadError`; assert `"error"` is in the JSON response.
3. **`FileNotFoundError`** — simulates FFmpeg not installed; assert the error message mentions `ffmpeg`.
4. **Pipeline failure** — patch the analysis function to raise a generic `RuntimeError`; assert a descriptive error message is returned.
5. **Parameter forwarding** — assert custom parameters reach the underlying function via mock call inspection.
6. **Valid JSON** — call `json.loads(result)` — it must not raise.

### Template

```python
# tests/test_my_feature.py
import json
from unittest.mock import MagicMock, patch

import server.main as main_module
from server.main import my_new_tool
from server.utils.downloader import DownloadError, VideoInfo

URL = "https://www.youtube.com/watch?v=M1GYqy0tHV0"


def _make_info():
    return VideoInfo({
        "id": "M1GYqy0tHV0",
        "title": "プリマドンナ / 星街すいせい(official)",
        "duration": 172.0,
        "channel": "Suisei Channel",
        "upload_date": "20260322",
        "description": "Starring & Vocals：Hoshimachi Suisei",
        "url": URL,
    })


class TestMyNewTool:
    def test_download_error_returns_error_json(self):
        with patch.object(main_module.downloader, "download",
                          side_effect=DownloadError("private video")):
            result = json.loads(my_new_tool(URL))
        assert "error" in result

    def test_ffmpeg_not_found_returns_error_json(self):
        with patch.object(main_module.downloader, "download",
                          side_effect=FileNotFoundError("ffmpeg")):
            result = json.loads(my_new_tool(URL))
        assert "error" in result
        assert "ffmpeg" in result["error"].lower()

    def test_analysis_failure_returns_error_json(self):
        info = _make_info()
        with patch.object(main_module.downloader, "download",
                          return_value=("v.mp4", "a.wav", info)):
            with patch("server.main.my_analysis", side_effect=RuntimeError("OOM")):
                result = json.loads(my_new_tool(URL))
        assert "error" in result

    def test_success_returns_correct_structure(self):
        info = _make_info()
        with patch.object(main_module.downloader, "download",
                          return_value=("v.mp4", "a.wav", info)):
            with patch("server.main.my_analysis", return_value={"field": "value"}):
                result = json.loads(my_new_tool(URL))
        assert result["title"] == "プリマドンナ / 星街すいせい(official)"
        assert result["duration"] == 172.0
        assert "field" in result

    def test_response_is_valid_json(self):
        info = _make_info()
        with patch.object(main_module.downloader, "download",
                          return_value=("v.mp4", "a.wav", info)):
            with patch("server.main.my_analysis", return_value={}):
                raw = my_new_tool(URL)
        json.loads(raw)  # must not raise
```

### Testing an analysis module directly

For logic in `server/tools/my_feature.py`, test the functions independently without involving the MCP layer:

```python
from server.tools.my_feature import my_analysis

def test_returns_expected_dict():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=b"output")
        result = my_analysis("fake.mp4", param="default")
    assert isinstance(result, dict)
    assert "field" in result
```

---

## pytest configuration

`pytest.ini` at the repo root configures test discovery:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short
```

`-v` shows each test name as it runs. `--tb=short` prints compact tracebacks on failure. For full tracebacks: `python -m pytest --tb=long`.

TypeScript tests (in `tests/*.test.ts`) use a separate runner (`pnpm test`) and are unaffected by `pytest.ini`.
