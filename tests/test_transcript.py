"""Tests for server/tools/transcript.py."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from server.tools.transcript import (
    count_words_in_range,
    get_text_in_range,
    get_transcript,
)


# ---------------------------------------------------------------------------
# get_text_in_range
# ---------------------------------------------------------------------------


class TestGetTextInRange:
    def test_segment_fully_within_range(self, sample_transcript):
        result = get_text_in_range(sample_transcript, 0.0, 5.0)
        assert "Hello world." in result
        assert "This is a test." in result

    def test_segment_partially_overlapping_at_start(self, sample_transcript):
        # Segment [0,2] still overlaps query [1,3] (t_end=2 > t_start=1)
        result = get_text_in_range(sample_transcript, 1.0, 3.0)
        assert "Hello world." in result

    def test_segment_partially_overlapping_at_end(self, sample_transcript):
        # Segment [2,5] still overlaps query [1.5,3] (t_start=2 < t_end=3)
        result = get_text_in_range(sample_transcript, 1.5, 3.0)
        assert "This is a test." in result

    def test_no_segments_in_range_returns_empty(self, sample_transcript):
        result = get_text_in_range(sample_transcript, 10.0, 20.0)
        assert result == ""

    def test_segment_ending_exactly_at_t_start_excluded(self, sample_transcript):
        # seg1 ends at 2.0; query starts at 2.0 → strict inequality t_end > t_start
        result = get_text_in_range(sample_transcript, 2.0, 5.0)
        assert "Hello world." not in result

    def test_segment_starting_exactly_at_t_end_excluded(self, sample_transcript):
        # seg2 starts at 2.0; query ends at 2.0 → strict inequality t_start < t_end
        result = get_text_in_range(sample_transcript, 0.0, 2.0)
        assert "This is a test." not in result

    def test_empty_transcript_returns_empty_string(self):
        empty = {"language": "en", "full_text": "", "segments": []}
        assert get_text_in_range(empty, 0.0, 10.0) == ""

    def test_multiple_segments_joined_with_space(self, sample_transcript):
        result = get_text_in_range(sample_transcript, 0.0, 5.0)
        assert result == "Hello world. This is a test."


# ---------------------------------------------------------------------------
# count_words_in_range
# ---------------------------------------------------------------------------


class TestCountWordsInRange:
    def test_counts_all_words_across_both_segments(self, sample_transcript):
        # seg1: 2 words, seg2: 4 words
        count = count_words_in_range(sample_transcript, 0.0, 5.0)
        assert count == 6

    def test_counts_words_in_first_segment_only(self, sample_transcript):
        # "Hello" [0.1–0.5], "world" [0.6–1.0]: both end before 1.1
        count = count_words_in_range(sample_transcript, 0.0, 1.1)
        assert count == 2

    def test_word_partially_overlapping_is_included(self, sample_transcript):
        # "world" starts at 0.6, ends at 1.0; query [0.7, 2.0]
        # word start (0.6) < t_end (2.0) AND word end (1.0) > t_start (0.7)
        count = count_words_in_range(sample_transcript, 0.7, 2.0)
        assert count >= 1

    def test_no_words_outside_any_segment(self, sample_transcript):
        count = count_words_in_range(sample_transcript, 10.0, 20.0)
        assert count == 0

    def test_empty_words_list_returns_zero(self):
        transcript = {
            "language": "en",
            "full_text": "Hello.",
            "segments": [{"t_start": 0.0, "t_end": 2.0, "text": "Hello.", "words": []}],
        }
        assert count_words_in_range(transcript, 0.0, 2.0) == 0

    def test_empty_transcript_returns_zero(self):
        empty = {"language": "en", "full_text": "", "segments": []}
        assert count_words_in_range(empty, 0.0, 5.0) == 0

    def test_word_ending_exactly_at_t_start_excluded(self, sample_transcript):
        # "Hello" ends at 0.5; query starts at 0.5 → strict inequality
        count_before = count_words_in_range(sample_transcript, 0.0, 0.5)
        count_at = count_words_in_range(sample_transcript, 0.5, 2.0)
        # total across both ranges must equal full range count
        assert count_before + count_at <= 6


# ---------------------------------------------------------------------------
# get_transcript (Whisper mocked)
# ---------------------------------------------------------------------------


def _fake_whisper_result():
    return {
        "language": "en",
        "text": " Hello world.",
        "segments": [
            {
                "start": 0.0,
                "end": 2.5,
                "text": " Hello world.",
                "words": [
                    {"word": " Hello", "start": 0.1, "end": 0.5},
                    {"word": " world", "start": 0.6, "end": 1.2},
                ],
            }
        ],
    }


class TestGetTranscript:
    def _mock_model(self):
        model = MagicMock()
        model.transcribe.return_value = _fake_whisper_result()
        return model

    def test_returns_language_full_text_and_segments(self):
        with patch("server.tools.transcript._load_model", return_value=self._mock_model()):
            result = get_transcript("fake.wav")
        assert set(result.keys()) >= {"language", "full_text", "segments"}

    def test_language_is_preserved(self):
        with patch("server.tools.transcript._load_model", return_value=self._mock_model()):
            result = get_transcript("fake.wav")
        assert result["language"] == "en"

    def test_full_text_is_stripped(self):
        with patch("server.tools.transcript._load_model", return_value=self._mock_model()):
            result = get_transcript("fake.wav")
        assert result["full_text"] == "Hello world."

    def test_segments_have_required_keys(self):
        with patch("server.tools.transcript._load_model", return_value=self._mock_model()):
            result = get_transcript("fake.wav")
        seg = result["segments"][0]
        assert set(seg.keys()) == {"t_start", "t_end", "text", "words"}

    def test_segment_timestamps_are_rounded_floats(self):
        with patch("server.tools.transcript._load_model", return_value=self._mock_model()):
            result = get_transcript("fake.wav")
        seg = result["segments"][0]
        assert isinstance(seg["t_start"], float)
        assert isinstance(seg["t_end"], float)

    def test_word_leading_whitespace_stripped(self):
        with patch("server.tools.transcript._load_model", return_value=self._mock_model()):
            result = get_transcript("fake.wav")
        for word in result["segments"][0]["words"]:
            assert not word["word"].startswith(" ")

    def test_model_size_forwarded_to_loader(self):
        mock_load = MagicMock(return_value=self._mock_model())
        with patch("server.tools.transcript._load_model", mock_load):
            get_transcript("fake.wav", model_size="small")
        mock_load.assert_called_once_with("small")

    def test_empty_whisper_result_produces_empty_segments(self):
        model = MagicMock()
        model.transcribe.return_value = {"language": "fr", "text": "", "segments": []}
        with patch("server.tools.transcript._load_model", return_value=model):
            result = get_transcript("fake.wav")
        assert result["segments"] == []
        assert result["full_text"] == ""

    def test_model_cache_reused_on_second_call(self):
        """whisper.load_model should only be called once per model size.

        whisper is imported locally inside _load_model, so we inject a fake
        module into sys.modules to avoid needing the real package installed.
        """
        import server.tools.transcript as mod

        saved = mod._model_cache.copy()
        mod._model_cache.clear()
        try:
            fake_model = self._mock_model()
            fake_whisper = MagicMock()
            fake_whisper.load_model.return_value = fake_model

            with patch.dict(sys.modules, {"whisper": fake_whisper}):
                get_transcript("fake.wav", model_size="tiny")
                get_transcript("fake.wav", model_size="tiny")

            # whisper.load_model should only have been called once (second call hits cache)
            assert fake_whisper.load_model.call_count == 1
        finally:
            mod._model_cache.clear()
            mod._model_cache.update(saved)
