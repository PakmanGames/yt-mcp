"""Tests for server/tools/timeline.py."""

from unittest.mock import MagicMock, patch

import pytest

from server.tools.timeline import _speech_rate_label, build_timeline


# ---------------------------------------------------------------------------
# _speech_rate_label
# ---------------------------------------------------------------------------


class TestSpeechRateLabel:
    def test_slow_below_100_wpm(self):
        assert _speech_rate_label(5, 300) == "slow"

    def test_slow_at_99_wpm(self):
        assert _speech_rate_label(99, 60.0) == "slow"

    def test_normal_at_exactly_100_wpm(self):
        assert _speech_rate_label(100, 60.0) == "normal"

    def test_normal_mid_range(self):
        assert _speech_rate_label(130, 60.0) == "normal"

    def test_normal_at_159_wpm(self):
        assert _speech_rate_label(159, 60.0) == "normal"

    def test_fast_at_exactly_160_wpm(self):
        assert _speech_rate_label(160, 60.0) == "fast"

    def test_fast_high_speed(self):
        assert _speech_rate_label(200, 60.0) == "fast"

    def test_zero_duration_returns_unknown(self):
        assert _speech_rate_label(100, 0.0) == "unknown"

    def test_zero_words_is_slow(self):
        assert _speech_rate_label(0, 60.0) == "slow"


# ---------------------------------------------------------------------------
# build_timeline helpers
# ---------------------------------------------------------------------------


def _make_transcript(words_in_range=2):
    """Return a minimal transcript with controllable word count."""
    words = [
        {"word": f"word{i}", "start": float(i), "end": float(i) + 0.5}
        for i in range(words_in_range)
    ]
    return {
        "language": "en",
        "full_text": " ".join(w["word"] for w in words),
        "segments": [
            {
                "t_start": 0.0,
                "t_end": 10.0,
                "text": " ".join(w["word"] for w in words),
                "words": words,
            }
        ],
    }


def _default_audio_seg():
    return {"energy": "low", "music": False, "tempo_bpm": 0.0, "rms_db": -40.0}


def _mock_audio_analyzer(audio_seg=None):
    mock = MagicMock()
    mock.analyze_segment.return_value = audio_seg or _default_audio_seg()
    return mock


# ---------------------------------------------------------------------------
# build_timeline
# ---------------------------------------------------------------------------


class TestBuildTimeline:
    def _run(
        self,
        transcript=None,
        duration=30.0,
        scene_times=None,
        frame_b64=None,
        anim=False,
        audio_seg=None,
        **kwargs,
    ):
        if transcript is None:
            transcript = _make_transcript()
        mock_analyzer = _mock_audio_analyzer(audio_seg)

        with (
            patch("server.tools.timeline.get_video_duration", return_value=duration),
            patch(
                "server.tools.timeline.detect_scene_timestamps",
                return_value=scene_times or [],
            ),
            patch(
                "server.tools.timeline.extract_frame_as_base64", return_value=frame_b64
            ),
            patch("server.tools.timeline.detect_animation", return_value=anim),
            patch("server.tools.timeline.AudioAnalyzer", return_value=mock_analyzer),
        ):
            return build_timeline(
                video_path="fake.mp4",
                audio_path="fake.wav",
                transcript=transcript,
                **kwargs,
            )

    # ---- Segment count / boundaries ----

    def test_no_cuts_produces_single_segment(self):
        result = self._run(duration=10.0, scene_times=[])
        assert len(result) == 1

    def test_single_cut_produces_two_segments(self):
        result = self._run(duration=30.0, scene_times=[10.0])
        assert len(result) == 2

    def test_two_cuts_produce_three_segments(self):
        result = self._run(duration=30.0, scene_times=[10.0, 20.0])
        assert len(result) == 3

    def test_segment_boundaries_match_cuts(self):
        result = self._run(duration=30.0, scene_times=[10.0, 20.0])
        assert result[0]["t_end"] == pytest.approx(10.0)
        assert result[1]["t_start"] == pytest.approx(10.0)
        assert result[1]["t_end"] == pytest.approx(20.0)
        assert result[2]["t_start"] == pytest.approx(20.0)

    def test_zero_duration_returns_empty_list(self):
        result = self._run(duration=0.0)
        assert result == []

    # ---- min_segment_sec ----

    def test_rapid_cuts_below_min_merged(self):
        # Cuts at 1, 3, 8 s — only 8 s is >= 5 s after previous boundary
        result = self._run(duration=20.0, scene_times=[1.0, 3.0, 8.0], min_segment_sec=5.0)
        # boundaries: [0, 8, 20] → 2 segments
        assert len(result) == 2

    def test_cut_at_exactly_min_included(self):
        result = self._run(duration=20.0, scene_times=[5.0], min_segment_sec=5.0)
        assert len(result) == 2

    def test_cut_just_below_min_excluded(self):
        # 4.9 s < 5.0 min → excluded, so only 1 segment
        result = self._run(duration=20.0, scene_times=[4.9], min_segment_sec=5.0)
        assert len(result) == 1

    # ---- Segment schema ----

    def test_segment_has_all_required_keys(self):
        result = self._run(duration=10.0)
        seg = result[0]
        assert set(seg.keys()) == {
            "t_start",
            "t_end",
            "transcript",
            "keyframe",
            "scene_change",
            "animation_detected",
            "audio",
        }

    def test_audio_dict_contains_speech_rate(self):
        result = self._run(duration=10.0)
        assert "speech_rate" in result[0]["audio"]

    def test_t_values_are_rounded_to_3_decimals(self):
        result = self._run(duration=10.123456)
        seg = result[0]
        assert seg["t_start"] == round(seg["t_start"], 3)
        assert seg["t_end"] == round(seg["t_end"], 3)

    # ---- scene_change flag ----

    def test_first_segment_scene_change_false(self):
        result = self._run(duration=30.0, scene_times=[10.0])
        assert result[0]["scene_change"] is False

    def test_subsequent_segments_scene_change_true(self):
        result = self._run(duration=30.0, scene_times=[10.0])
        assert result[1]["scene_change"] is True

    def test_all_segments_after_first_have_scene_change_true(self):
        result = self._run(duration=45.0, scene_times=[10.0, 20.0, 30.0])
        for seg in result[1:]:
            assert seg["scene_change"] is True

    # ---- include_frames ----

    def test_include_frames_false_keyframe_is_none(self):
        result = self._run(duration=10.0, frame_b64="B64DATA", include_frames=False)
        assert result[0]["keyframe"] is None

    def test_include_frames_true_keyframe_is_populated(self):
        result = self._run(duration=10.0, frame_b64="B64DATA", include_frames=True)
        assert result[0]["keyframe"] == "B64DATA"

    def test_include_frames_true_calls_extract_frame(self):
        with (
            patch("server.tools.timeline.get_video_duration", return_value=10.0),
            patch("server.tools.timeline.detect_scene_timestamps", return_value=[]),
            patch("server.tools.timeline.detect_animation", return_value=False),
            patch("server.tools.timeline.AudioAnalyzer", return_value=_mock_audio_analyzer()),
        ):
            with patch(
                "server.tools.timeline.extract_frame_as_base64", return_value="B64"
            ) as mock_frame:
                build_timeline(
                    "fake.mp4", "fake.wav", _make_transcript(), include_frames=True
                )
        mock_frame.assert_called()

    # ---- Animation ----

    def test_animation_flag_propagated(self):
        result = self._run(duration=10.0, anim=True)
        assert result[0]["animation_detected"] is True

    def test_short_segment_skips_animation_detection(self):
        # Segment < 2 s → detect_animation must not be called
        with (
            patch("server.tools.timeline.get_video_duration", return_value=1.5),
            patch("server.tools.timeline.detect_scene_timestamps", return_value=[]),
            patch("server.tools.timeline.extract_frame_as_base64", return_value=None),
            patch("server.tools.timeline.AudioAnalyzer", return_value=_mock_audio_analyzer()),
        ):
            with patch("server.tools.timeline.detect_animation") as mock_anim:
                build_timeline("fake.mp4", "fake.wav", _make_transcript())
        mock_anim.assert_not_called()

    # ---- Speech rate ----

    def test_zero_words_gives_slow_speech_rate(self):
        transcript_empty = {"language": "en", "full_text": "", "segments": []}
        result = self._run(transcript=transcript_empty, duration=60.0)
        assert result[0]["audio"]["speech_rate"] == "slow"

    def test_high_word_count_gives_fast_speech_rate(self):
        # Inject many words into a 60 s window → > 160 wpm
        many_words = [
            {"word": f"w{i}", "start": float(i) * 0.3, "end": float(i) * 0.3 + 0.2}
            for i in range(200)
        ]
        transcript = {
            "language": "en",
            "full_text": " ".join(w["word"] for w in many_words),
            "segments": [
                {
                    "t_start": 0.0,
                    "t_end": 60.0,
                    "text": " ".join(w["word"] for w in many_words),
                    "words": many_words,
                }
            ],
        }
        result = self._run(transcript=transcript, duration=60.0)
        assert result[0]["audio"]["speech_rate"] == "fast"
