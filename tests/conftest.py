"""Shared pytest fixtures for the yt-mcp Python test suite."""

import json
import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

SR = 16_000  # sample rate used throughout the project


# ---------------------------------------------------------------------------
# Audio array fixtures (numpy, no disk I/O)
# ---------------------------------------------------------------------------


@pytest.fixture
def silence_array():
    """3 seconds of silence at 16 kHz (very low energy)."""
    return np.zeros(3 * SR, dtype=np.float32)


@pytest.fixture
def loud_tone_array():
    """3 seconds of summed harmonics at amplitude ~0.55 (high energy, music-like)."""
    t = np.linspace(0, 3, 3 * SR, endpoint=False)
    y = (
        0.30 * np.sin(2 * np.pi * 440 * t)
        + 0.15 * np.sin(2 * np.pi * 880 * t)
        + 0.10 * np.sin(2 * np.pi * 1320 * t)
    ).astype(np.float32)
    return y


@pytest.fixture
def medium_noise_array():
    """3 seconds of white noise at amplitude ~0.05 (medium energy, speech-like)."""
    rng = np.random.default_rng(42)
    return rng.normal(0, 0.05, 3 * SR).astype(np.float32)


# ---------------------------------------------------------------------------
# WAV file fixtures (real PCM files on disk for AudioAnalyzer integration)
# ---------------------------------------------------------------------------


def _write_wav(path: Path, y: np.ndarray, sr: int = SR) -> None:
    """Write a float32 numpy array to a 16-bit mono PCM WAV file."""
    pcm = np.clip(y, -1.0, 1.0)
    pcm_int16 = (pcm * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm_int16.tobytes())


@pytest.fixture
def silence_wav(tmp_path, silence_array):
    path = tmp_path / "silence.wav"
    _write_wav(path, silence_array)
    return str(path)


@pytest.fixture
def tone_wav(tmp_path, loud_tone_array):
    path = tmp_path / "tone.wav"
    _write_wav(path, loud_tone_array)
    return str(path)


@pytest.fixture
def medium_wav(tmp_path, medium_noise_array):
    path = tmp_path / "medium.wav"
    _write_wav(path, medium_noise_array)
    return str(path)


# ---------------------------------------------------------------------------
# Transcript fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_transcript():
    """A minimal transcript dict matching the structure returned by get_transcript()."""
    return {
        "language": "en",
        "full_text": "Hello world. This is a test.",
        "segments": [
            {
                "t_start": 0.0,
                "t_end": 2.0,
                "text": "Hello world.",
                "words": [
                    {"word": "Hello", "start": 0.1, "end": 0.5},
                    {"word": "world", "start": 0.6, "end": 1.0},
                ],
            },
            {
                "t_start": 2.0,
                "t_end": 5.0,
                "text": "This is a test.",
                "words": [
                    {"word": "This", "start": 2.1, "end": 2.4},
                    {"word": "is", "start": 2.5, "end": 2.7},
                    {"word": "a", "start": 2.8, "end": 2.9},
                    {"word": "test", "start": 3.0, "end": 3.5},
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# VideoInfo / cache fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_video_info_data():
    return {
        "id": "abc123",
        "title": "Test Video",
        "duration": 60.0,
        "channel": "Test Channel",
        "upload_date": "20240101",
        "description": "A short description for testing purposes.",
        "url": "https://www.youtube.com/watch?v=abc123",
    }


@pytest.fixture
def cache_hit_dir(tmp_path, fake_video_info_data):
    """Temp cache directory pre-populated with all three cached files (cache-hit scenario)."""
    video_id = fake_video_info_data["id"]
    slot = tmp_path / video_id
    slot.mkdir()
    (slot / "video.mp4").write_bytes(b"FAKE_MP4_CONTENT")
    (slot / "audio.wav").write_bytes(b"FAKE_WAV_CONTENT")
    info_fields = {k: v for k, v in fake_video_info_data.items() if k != "url"}
    (slot / "info.json").write_text(json.dumps(info_fields))
    return tmp_path, video_id
