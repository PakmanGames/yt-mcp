"""Tests for server/tools/audio.py.

AudioAnalyzer loads a WAV file via librosa.load() in __init__.  For unit tests
we patch librosa.load to inject a synthetic numpy array, so no real audio file
is needed.  The actual librosa analysis (RMS, HPSS, beat_track, etc.) runs on
that synthetic array — this validates the classification logic without touching
the network or disk.
"""

from unittest.mock import patch

import numpy as np
import pytest

from server.tools.audio import AudioAnalyzer

SR = 16_000  # must match AudioAnalyzer.SR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_analyzer(y: np.ndarray) -> AudioAnalyzer:
    """Return an AudioAnalyzer whose audio data is the given synthetic array."""
    with patch("librosa.load", return_value=(y, SR)):
        return AudioAnalyzer("dummy.wav")


def _sine(freq: float, duration: float = 3.0, amp: float = 0.5) -> np.ndarray:
    t = np.linspace(0, duration, int(duration * SR), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _noise(duration: float = 3.0, amp: float = 0.05, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(0, amp, int(duration * SR)).astype(np.float32)


def _silence(duration: float = 3.0) -> np.ndarray:
    return np.zeros(int(duration * SR), dtype=np.float32)


# ---------------------------------------------------------------------------
# AudioAnalyzer.__init__
# ---------------------------------------------------------------------------


class TestAudioAnalyzerInit:
    def test_stores_y_array(self):
        y = _silence()
        analyzer = _make_analyzer(y)
        assert len(analyzer.y) == len(y)

    def test_stores_sample_rate(self):
        y = _silence()
        analyzer = _make_analyzer(y)
        assert analyzer.sr == SR

    def test_loads_with_correct_sr_kwarg(self):
        y = _silence()
        with patch("librosa.load", return_value=(y, SR)) as mock_load:
            AudioAnalyzer("test.wav")
        _, kwargs = mock_load.call_args
        assert kwargs.get("sr") == SR


# ---------------------------------------------------------------------------
# analyze_segment: return schema
# ---------------------------------------------------------------------------


class TestAnalyzeSegmentSchema:
    def test_returns_all_required_keys(self):
        analyzer = _make_analyzer(_noise())
        result = analyzer.analyze_segment(0.0, 1.0)
        assert set(result.keys()) == {"energy", "music", "tempo_bpm", "rms_db"}

    def test_energy_is_valid_label(self):
        analyzer = _make_analyzer(_noise())
        result = analyzer.analyze_segment(0.0, 1.0)
        assert result["energy"] in ("low", "medium", "high")

    def test_music_is_bool(self):
        analyzer = _make_analyzer(_noise())
        result = analyzer.analyze_segment(0.0, 1.0)
        assert isinstance(result["music"], bool)

    def test_tempo_bpm_is_float(self):
        analyzer = _make_analyzer(_noise())
        result = analyzer.analyze_segment(0.0, 1.0)
        assert isinstance(result["tempo_bpm"], float)

    def test_rms_db_is_float(self):
        analyzer = _make_analyzer(_noise())
        result = analyzer.analyze_segment(0.0, 1.0)
        assert isinstance(result["rms_db"], float)


# ---------------------------------------------------------------------------
# analyze_segment: energy classification
# ---------------------------------------------------------------------------


class TestEnergyClassification:
    def test_silence_is_low_energy(self):
        analyzer = _make_analyzer(_silence(3.0))
        result = analyzer.analyze_segment(0.0, 3.0)
        assert result["energy"] == "low"
        assert result["rms_db"] < -35

    def test_loud_tone_is_high_energy(self):
        # 440 Hz sine at amp=0.5 → rms ≈ 0.354, amplitude_to_db ≈ -9 dB > -18
        y = _sine(440, duration=3.0, amp=0.5)
        analyzer = _make_analyzer(y)
        result = analyzer.analyze_segment(0.0, 3.0)
        assert result["energy"] == "high"
        assert result["rms_db"] > -18

    def test_medium_amplitude_noise_is_medium_energy(self):
        # amp=0.05 → rms ≈ 0.05, amplitude_to_db ≈ -26 dB  (-35 < x < -18)
        y = _noise(duration=3.0, amp=0.05)
        analyzer = _make_analyzer(y)
        result = analyzer.analyze_segment(0.0, 3.0)
        assert result["energy"] == "medium"
        assert -35 < result["rms_db"] < -18

    def test_very_quiet_signal_is_low_energy(self):
        # amp=0.001 → amplitude_to_db ≈ -60 dB < -35
        y = _sine(440, duration=3.0, amp=0.001)
        analyzer = _make_analyzer(y)
        result = analyzer.analyze_segment(0.0, 3.0)
        assert result["energy"] == "low"


# ---------------------------------------------------------------------------
# analyze_segment: short segment edge case (< 100ms)
# ---------------------------------------------------------------------------


class TestShortSegmentDefaults:
    def test_very_short_segment_returns_default_values(self):
        y = _sine(440, duration=5.0)
        analyzer = _make_analyzer(y)
        result = analyzer.analyze_segment(0.0, 0.05)  # 50ms — below 100ms minimum
        assert result == {"energy": "low", "music": False, "tempo_bpm": 0.0, "rms_db": -60.0}

    def test_zero_length_segment_returns_default_values(self):
        y = _sine(440, duration=5.0)
        analyzer = _make_analyzer(y)
        result = analyzer.analyze_segment(1.0, 1.0)  # 0ms
        assert result["energy"] == "low"
        assert result["rms_db"] == -60.0

    def test_segment_longer_than_100ms_is_analyzed(self):
        # 200ms of a loud tone should NOT return the short-segment default
        y = _sine(440, duration=5.0, amp=0.5)
        analyzer = _make_analyzer(y)
        result = analyzer.analyze_segment(0.0, 0.2)
        assert result["rms_db"] > -60.0  # must be a real measurement


# ---------------------------------------------------------------------------
# analyze_segment: music detection
# ---------------------------------------------------------------------------


class TestMusicDetection:
    def test_white_noise_is_not_music(self):
        # White noise has high spectral flatness → not music
        rng = np.random.default_rng(7)
        y = rng.normal(0, 0.1, 3 * SR).astype(np.float32)
        analyzer = _make_analyzer(y)
        result = analyzer.analyze_segment(0.0, 3.0)
        assert result["music"] is False

    def test_music_field_present_and_bool_for_harmonic_signal(self):
        # Check the field is always a bool, regardless of content
        harmonics = sum(
            (0.15 / (i + 1)) * _sine(220 * (i + 1), duration=3.0)
            for i in range(4)
        ).astype(np.float32)
        analyzer = _make_analyzer(harmonics)
        result = analyzer.analyze_segment(0.0, 3.0)
        assert isinstance(result["music"], bool)


# ---------------------------------------------------------------------------
# analyze_full
# ---------------------------------------------------------------------------


class TestAnalyzeFull:
    def test_segment_count_matches_duration(self):
        # 6 s audio, 2 s windows → exactly 3 segments
        y = _sine(440, duration=6.0)
        analyzer = _make_analyzer(y)
        segments = analyzer.analyze_full(segment_duration=2)
        assert len(segments) == 3

    def test_segments_are_contiguous_and_non_overlapping(self):
        y = _sine(440, duration=10.0)
        analyzer = _make_analyzer(y)
        segments = analyzer.analyze_full(segment_duration=3)
        for i in range(len(segments) - 1):
            assert segments[i]["t_end"] == pytest.approx(segments[i + 1]["t_start"])

    def test_last_segment_covers_remaining_audio(self):
        # 7 s with 3 s windows: [0,3], [3,6], [6,7]
        y = _sine(440, duration=7.0)
        analyzer = _make_analyzer(y)
        segments = analyzer.analyze_full(segment_duration=3)
        expected_end = len(y) / SR
        assert segments[-1]["t_end"] == pytest.approx(expected_end, abs=0.01)

    def test_first_segment_starts_at_zero(self):
        y = _noise(duration=4.0)
        analyzer = _make_analyzer(y)
        segments = analyzer.analyze_full(segment_duration=2)
        assert segments[0]["t_start"] == pytest.approx(0.0)

    def test_each_segment_has_t_start_and_t_end(self):
        y = _noise(duration=5.0)
        analyzer = _make_analyzer(y)
        segments = analyzer.analyze_full(segment_duration=2)
        for seg in segments:
            assert "t_start" in seg
            assert "t_end" in seg
            assert seg["t_end"] > seg["t_start"]

    def test_each_segment_has_audio_feature_keys(self):
        y = _noise(duration=4.0)
        analyzer = _make_analyzer(y)
        segments = analyzer.analyze_full(segment_duration=2)
        for seg in segments:
            assert {"energy", "music", "tempo_bpm", "rms_db"}.issubset(seg.keys())

    def test_short_audio_produces_single_segment(self):
        # 1 s audio with 30 s window → exactly 1 segment covering all
        y = _sine(440, duration=1.0)
        analyzer = _make_analyzer(y)
        segments = analyzer.analyze_full(segment_duration=30)
        assert len(segments) == 1
        assert segments[0]["t_start"] == pytest.approx(0.0)
        assert segments[0]["t_end"] == pytest.approx(1.0, abs=0.01)

    def test_default_segment_duration_is_30s(self):
        # 90 s audio with default 30 s window → 3 segments
        y = _sine(440, duration=90.0)
        analyzer = _make_analyzer(y)
        segments = analyzer.analyze_full()
        assert len(segments) == 3
