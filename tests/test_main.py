"""Tests for server/main.py — MCP tool handlers.

Each tool function is called directly (the @mcp.tool() decorator keeps the
original function callable). External dependencies (downloader, Whisper,
librosa, FFmpeg, PySceneDetect, OpenCV) are fully mocked so tests run
in < 1 second with no network or media-file access.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from server.utils.downloader import DownloadError, VideoInfo
import server.main as main_module
from server.main import (
    get_audio_features,
    get_full_context,
    get_video_frames,
    get_video_transcript,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_info(
    video_id="abc123",
    title="Test Video",
    duration=60.0,
    channel="Test Channel",
    description="desc",
):
    return VideoInfo(
        {
            "id": video_id,
            "title": title,
            "duration": duration,
            "channel": channel,
            "upload_date": "20240101",
            "description": description,
            "url": "https://www.youtube.com/watch?v=" + video_id,
        }
    )


def _make_transcript():
    return {
        "language": "en",
        "full_text": "Hello world.",
        "segments": [
            {
                "t_start": 0.0,
                "t_end": 5.0,
                "text": "Hello world.",
                "words": [{"word": "Hello", "start": 0.1, "end": 0.5}],
            }
        ],
    }


def _make_segments():
    return [
        {
            "t_start": 0.0,
            "t_end": 10.0,
            "transcript": "Hello.",
            "keyframe": None,
            "scene_change": False,
            "animation_detected": False,
            "audio": {
                "energy": "low",
                "speech_rate": "slow",
                "music": False,
                "tempo_bpm": 0.0,
                "rms_db": -40.0,
            },
        }
    ]


# ---------------------------------------------------------------------------
# get_video_transcript
# ---------------------------------------------------------------------------


class TestGetVideoTranscript:
    URL = "https://www.youtube.com/watch?v=abc123"

    def test_download_error_returns_error_json(self):
        with patch.object(
            main_module.downloader, "download", side_effect=DownloadError("bad url")
        ):
            result = json.loads(get_video_transcript(self.URL))
        assert "error" in result
        assert "bad url" in result["error"]

    def test_ffmpeg_not_found_returns_error_json(self):
        with patch.object(
            main_module.downloader, "download", side_effect=FileNotFoundError("ffmpeg")
        ):
            result = json.loads(get_video_transcript(self.URL))
        assert "error" in result
        assert "ffmpeg" in result["error"].lower()

    def test_transcription_failure_returns_error_json(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_transcript", side_effect=RuntimeError("OOM")):
                result = json.loads(get_video_transcript(self.URL))
        assert "error" in result
        assert "Transcription failed" in result["error"]

    def test_success_returns_correct_structure(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_transcript", return_value=_make_transcript()):
                result = json.loads(get_video_transcript(self.URL))

        assert result["title"] == "Test Video"
        assert result["duration"] == 60.0
        assert result["language"] == "en"
        assert "full_text" in result
        assert "segments" in result

    def test_default_model_size_is_base(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_transcript", return_value=_make_transcript()) as mock_t:
                get_video_transcript(self.URL)
        _, kwargs = mock_t.call_args
        assert kwargs.get("model_size") == "base"

    def test_custom_model_size_forwarded(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_transcript", return_value=_make_transcript()) as mock_t:
                get_video_transcript(self.URL, model_size="small")
        _, kwargs = mock_t.call_args
        assert kwargs.get("model_size") == "small"

    def test_response_is_valid_json(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_transcript", return_value=_make_transcript()):
                raw = get_video_transcript(self.URL)
        json.loads(raw)  # must not raise


# ---------------------------------------------------------------------------
# get_video_frames
# ---------------------------------------------------------------------------


class TestGetVideoFrames:
    URL = "https://www.youtube.com/watch?v=abc123"
    _FAKE_FRAME = {
        "t": 0.0,
        "t_formatted": "0:00",
        "keyframe": "LONGB64DATA",
        "scene_change": False,
        "animation_detected": False,
    }

    def test_download_error_returns_error_json(self):
        with patch.object(
            main_module.downloader, "download", side_effect=DownloadError("fail")
        ):
            result = json.loads(get_video_frames(self.URL))
        assert "error" in result

    def test_ffmpeg_not_found_returns_error_json(self):
        with patch.object(
            main_module.downloader, "download", side_effect=FileNotFoundError("ffmpeg")
        ):
            result = json.loads(get_video_frames(self.URL))
        assert "error" in result
        assert "ffmpeg" in result["error"].lower()

    def test_invalid_strategy_returns_error_json(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            result = json.loads(get_video_frames(self.URL, strategy="invalid"))
        assert "error" in result
        assert "strategy" in result["error"]

    def test_extraction_failure_returns_error_json(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_keyframes", side_effect=RuntimeError("fail")):
                result = json.loads(get_video_frames(self.URL))
        assert "error" in result
        assert "Frame extraction failed" in result["error"]

    def test_success_returns_correct_structure(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_keyframes", return_value=[self._FAKE_FRAME]):
                result = json.loads(get_video_frames(self.URL))

        assert result["title"] == "Test Video"
        assert result["frame_count"] == 1
        assert "frames" in result
        assert "summary" in result
        assert "duration_formatted" in result

    def test_summary_excludes_keyframe_field(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_keyframes", return_value=[self._FAKE_FRAME]):
                result = json.loads(get_video_frames(self.URL))
        assert "keyframe" not in result["summary"][0]

    def test_frames_include_keyframe_field(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_keyframes", return_value=[self._FAKE_FRAME]):
                result = json.loads(get_video_frames(self.URL))
        assert "keyframe" in result["frames"][0]

    @pytest.mark.parametrize("strategy", ["scene", "interval", "both"])
    def test_all_valid_strategies_accepted(self, strategy):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_keyframes", return_value=[]):
                result = json.loads(get_video_frames(self.URL, strategy=strategy))
        assert "error" not in result


# ---------------------------------------------------------------------------
# get_audio_features
# ---------------------------------------------------------------------------


class TestGetAudioFeatures:
    URL = "https://www.youtube.com/watch?v=abc123"

    def test_download_error_returns_error_json(self):
        with patch.object(
            main_module.downloader, "download", side_effect=DownloadError("fail")
        ):
            result = json.loads(get_audio_features(self.URL))
        assert "error" in result

    def test_ffmpeg_not_found_returns_error_json(self):
        with patch.object(
            main_module.downloader, "download", side_effect=FileNotFoundError("no ffmpeg")
        ):
            result = json.loads(get_audio_features(self.URL))
        assert "error" in result

    def test_analysis_failure_returns_error_json(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.AudioAnalyzer", side_effect=RuntimeError("load fail")):
                result = json.loads(get_audio_features(self.URL))
        assert "error" in result
        assert "Audio analysis failed" in result["error"]

    def test_success_returns_correct_structure(self):
        info = _make_info()
        mock_analyzer = MagicMock()
        mock_analyzer.analyze_full.return_value = [
            {"t_start": 0.0, "t_end": 30.0, "energy": "low", "music": False,
             "tempo_bpm": 0.0, "rms_db": -40.0}
        ]
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.AudioAnalyzer", return_value=mock_analyzer):
                result = json.loads(get_audio_features(self.URL))

        assert result["title"] == "Test Video"
        assert result["duration"] == 60.0
        assert "segment_duration" in result
        assert "segments" in result

    def test_default_segment_duration_is_30(self):
        info = _make_info()
        mock_analyzer = MagicMock()
        mock_analyzer.analyze_full.return_value = []
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.AudioAnalyzer", return_value=mock_analyzer):
                result = json.loads(get_audio_features(self.URL))
        assert result["segment_duration"] == 30

    def test_custom_segment_duration_forwarded(self):
        info = _make_info()
        mock_analyzer = MagicMock()
        mock_analyzer.analyze_full.return_value = []
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.AudioAnalyzer", return_value=mock_analyzer):
                get_audio_features(self.URL, segment_duration=60)
        mock_analyzer.analyze_full.assert_called_once_with(segment_duration=60)


# ---------------------------------------------------------------------------
# get_full_context
# ---------------------------------------------------------------------------


class TestGetFullContext:
    URL = "https://www.youtube.com/watch?v=abc123"

    def test_download_error_returns_error_json(self):
        with patch.object(
            main_module.downloader, "download", side_effect=DownloadError("fail")
        ):
            result = json.loads(get_full_context(self.URL))
        assert "error" in result

    def test_ffmpeg_not_found_returns_error_json(self):
        with patch.object(
            main_module.downloader, "download", side_effect=FileNotFoundError("no ffmpeg")
        ):
            result = json.loads(get_full_context(self.URL))
        assert "error" in result
        assert "ffmpeg" in result["error"].lower()

    def test_transcription_error_returns_error_json(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_transcript", side_effect=RuntimeError("OOM")):
                result = json.loads(get_full_context(self.URL))
        assert "error" in result
        assert "Transcription failed" in result["error"]

    def test_timeline_error_returns_error_json(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_transcript", return_value=_make_transcript()):
                with patch("server.main.build_timeline", side_effect=RuntimeError("crash")):
                    result = json.loads(get_full_context(self.URL))
        assert "error" in result
        assert "Timeline build failed" in result["error"]

    def test_success_returns_correct_structure(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_transcript", return_value=_make_transcript()):
                with patch("server.main.build_timeline", return_value=_make_segments()):
                    result = json.loads(get_full_context(self.URL))

        assert result["title"] == "Test Video"
        assert result["channel"] == "Test Channel"
        assert result["duration"] == 60.0
        assert "duration_formatted" in result
        assert result["language"] == "en"
        assert "description" in result
        assert "segments" in result

    def test_include_frames_defaults_to_false(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_transcript", return_value=_make_transcript()):
                with patch("server.main.build_timeline", return_value=[]) as mock_bt:
                    get_full_context(self.URL)
        _, kwargs = mock_bt.call_args
        assert kwargs.get("include_frames") is False

    def test_include_frames_forwarded(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_transcript", return_value=_make_transcript()):
                with patch("server.main.build_timeline", return_value=[]) as mock_bt:
                    get_full_context(self.URL, include_frames=True)
        _, kwargs = mock_bt.call_args
        assert kwargs.get("include_frames") is True

    def test_model_size_forwarded_to_get_transcript(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_transcript", return_value=_make_transcript()) as mock_t:
                with patch("server.main.build_timeline", return_value=[]):
                    get_full_context(self.URL, model_size="medium")
        _, kwargs = mock_t.call_args
        assert kwargs.get("model_size") == "medium"

    def test_response_is_valid_json(self):
        info = _make_info()
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_transcript", return_value=_make_transcript()):
                with patch("server.main.build_timeline", return_value=_make_segments()):
                    raw = get_full_context(self.URL)
        json.loads(raw)  # must not raise

    def test_duration_formatted_1_minute_30_seconds(self):
        info = _make_info(duration=90.0)
        with patch.object(
            main_module.downloader,
            "download",
            return_value=("v.mp4", "a.wav", info),
        ):
            with patch("server.main.get_transcript", return_value=_make_transcript()):
                with patch("server.main.build_timeline", return_value=[]):
                    result = json.loads(get_full_context(self.URL))
        assert result["duration_formatted"] == "1:30"
