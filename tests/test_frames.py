"""Tests for server/tools/frames.py."""

import base64
import json
import os
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from server.tools.frames import (
    detect_animation,
    detect_scene_timestamps,
    extract_frame_as_base64,
    format_time,
    get_keyframes,
    get_video_duration,
)


# ---------------------------------------------------------------------------
# Inject lightweight fakes for optional heavy dependencies so tests run
# without opencv-python or scenedetect installed.
# ---------------------------------------------------------------------------


def _inject_fake_scenedetect():
    """Return a context that puts a MagicMock scenedetect in sys.modules."""
    fake = MagicMock()
    return patch.dict(sys.modules, {"scenedetect": fake}), fake


def _inject_fake_cv2():
    """Return a context that puts a MagicMock cv2 in sys.modules."""
    fake = MagicMock()
    # Constants used inside detect_animation
    fake.CAP_PROP_POS_MSEC = 0
    fake.COLOR_BGR2GRAY = 6
    return patch.dict(sys.modules, {"cv2": fake}), fake


# ---------------------------------------------------------------------------
# format_time
# ---------------------------------------------------------------------------


class TestFormatTime:
    def test_zero_seconds(self):
        assert format_time(0) == "0:00"

    def test_seconds_only(self):
        assert format_time(45) == "0:45"

    def test_exactly_one_minute(self):
        assert format_time(60) == "1:00"

    def test_minutes_and_seconds(self):
        assert format_time(125) == "2:05"

    def test_exactly_one_hour(self):
        assert format_time(3600) == "1:00:00"

    def test_hours_minutes_seconds(self):
        assert format_time(3661) == "1:01:01"

    def test_large_duration(self):
        assert format_time(7384) == "2:03:04"

    def test_float_input_truncated(self):
        assert format_time(90.9) == "1:30"


# ---------------------------------------------------------------------------
# get_video_duration
# ---------------------------------------------------------------------------


class TestGetVideoDuration:
    def _ffprobe_json(self, duration: float) -> str:
        return json.dumps(
            {
                "streams": [
                    {"codec_type": "audio", "duration": "99.0"},
                    {"codec_type": "video", "duration": str(duration)},
                ]
            }
        )

    def test_returns_duration_from_video_stream(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=self._ffprobe_json(120.5), returncode=0)
            result = get_video_duration("fake.mp4")
        assert result == pytest.approx(120.5)

    def test_returns_zero_when_no_video_stream(self):
        output = json.dumps({"streams": [{"codec_type": "audio", "duration": "30.0"}]})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=output, returncode=0)
            result = get_video_duration("fake.mp4")
        assert result == 0.0

    def test_returns_zero_for_empty_stream_list(self):
        output = json.dumps({"streams": []})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=output, returncode=0)
            result = get_video_duration("fake.mp4")
        assert result == 0.0


# ---------------------------------------------------------------------------
# detect_scene_timestamps
# ---------------------------------------------------------------------------


class TestDetectSceneTimestamps:
    """detect_scene_timestamps imports scenedetect locally inside the function,
    so we inject a fake module into sys.modules rather than patching module attrs."""

    def _scene(self, start_sec: float):
        s = MagicMock()
        s.get_seconds.return_value = start_sec
        e = MagicMock()
        e.get_seconds.return_value = start_sec + 5.0
        return (s, e)

    def test_returns_sorted_scene_start_times(self):
        scenes = [self._scene(30.0), self._scene(5.0), self._scene(15.0)]
        ctx, fake_sd = _inject_fake_scenedetect()
        fake_sd.detect.return_value = scenes
        with ctx:
            result = detect_scene_timestamps("fake.mp4")
        assert result == sorted(result)
        assert len(result) == 3

    def test_empty_returns_empty_list(self):
        ctx, fake_sd = _inject_fake_scenedetect()
        fake_sd.detect.return_value = []
        with ctx:
            result = detect_scene_timestamps("fake.mp4")
        assert result == []

    def test_duplicate_start_times_deduplicated(self):
        scenes = [self._scene(10.0), self._scene(10.0)]
        ctx, fake_sd = _inject_fake_scenedetect()
        fake_sd.detect.return_value = scenes
        with ctx:
            result = detect_scene_timestamps("fake.mp4")
        assert len(result) == 1

    def test_custom_threshold_forwarded_to_detector(self):
        ctx, fake_sd = _inject_fake_scenedetect()
        fake_sd.detect.return_value = []
        with ctx:
            detect_scene_timestamps("fake.mp4", threshold=15.0)
        fake_sd.ContentDetector.assert_called_once_with(threshold=15.0)


# ---------------------------------------------------------------------------
# extract_frame_as_base64
# ---------------------------------------------------------------------------


class TestExtractFrameAsBase64:
    def test_returns_base64_string_on_success(self, tmp_path):
        fake_jpg = b"\xff\xd8\xff" + b"\xab" * 50

        tmp_jpg = str(tmp_path / "frame.jpg")

        def fake_mkstemp(suffix):
            fd = os.open(tmp_jpg, os.O_CREAT | os.O_WRONLY)
            return fd, tmp_jpg

        def fake_ffmpeg(cmd, **kwargs):
            with open(tmp_jpg, "wb") as f:
                f.write(fake_jpg)
            return MagicMock(returncode=0)

        with patch("tempfile.mkstemp", side_effect=fake_mkstemp):
            with patch("subprocess.run", side_effect=fake_ffmpeg):
                with patch("os.close"):
                    result = extract_frame_as_base64("fake.mp4", 5.0)

        assert result is not None
        assert base64.b64decode(result) == fake_jpg

    def test_returns_none_when_ffmpeg_fails(self, tmp_path):
        tmp_jpg = str(tmp_path / "fail.jpg")

        def fake_mkstemp(suffix):
            fd = os.open(tmp_jpg, os.O_CREAT | os.O_WRONLY)
            return fd, tmp_jpg

        with patch("tempfile.mkstemp", side_effect=fake_mkstemp):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                with patch("os.close"):
                    result = extract_frame_as_base64("fake.mp4", 5.0)

        assert result is None

    def test_returns_none_when_output_file_empty(self, tmp_path):
        tmp_jpg = str(tmp_path / "empty.jpg")

        def fake_mkstemp(suffix):
            fd = os.open(tmp_jpg, os.O_CREAT | os.O_WRONLY)
            return fd, tmp_jpg

        with patch("tempfile.mkstemp", side_effect=fake_mkstemp):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                with patch("os.close"):
                    with patch("os.path.getsize", return_value=0):
                        result = extract_frame_as_base64("fake.mp4", 5.0)

        assert result is None

    def test_temp_file_deleted_after_success(self, tmp_path):
        fake_jpg = b"\xff\xd8\xff" + b"\x00" * 10
        tmp_jpg = str(tmp_path / "cleanup.jpg")

        def fake_mkstemp(suffix):
            fd = os.open(tmp_jpg, os.O_CREAT | os.O_WRONLY)
            return fd, tmp_jpg

        def fake_ffmpeg(cmd, **kwargs):
            with open(tmp_jpg, "wb") as f:
                f.write(fake_jpg)
            return MagicMock(returncode=0)

        with patch("tempfile.mkstemp", side_effect=fake_mkstemp):
            with patch("subprocess.run", side_effect=fake_ffmpeg):
                with patch("os.close"):
                    extract_frame_as_base64("fake.mp4", 5.0)

        assert not os.path.exists(tmp_jpg)


# ---------------------------------------------------------------------------
# detect_animation
# ---------------------------------------------------------------------------


class TestDetectAnimation:
    """detect_animation imports cv2 locally, so we inject a fake cv2 module."""

    def _cap_with_frames(self, fake_cv2, gray_frames: list[np.ndarray]):
        """Wire up a mock VideoCapture whose reads return pre-built gray frames."""
        cap = MagicMock()
        cap.isOpened.return_value = True
        frame_iter = iter(gray_frames)

        # The raw frame returned by cap.read() can be anything; cvtColor converts it
        def fake_read():
            try:
                return True, np.zeros((1, 1, 3), dtype=np.uint8)  # dummy color frame
            except StopIteration:
                return False, None

        cap.read.side_effect = [
            (True, np.zeros((1, 1, 3), dtype=np.uint8)) for _ in gray_frames
        ] + [(False, None)] * 5

        # cvtColor returns the pre-built gray frames in sequence
        cvt_iter = iter(gray_frames)

        def fake_cvt(frame, code):
            try:
                return next(cvt_iter)
            except StopIteration:
                return np.zeros((80, 80), dtype=np.uint8)

        fake_cv2.cvtColor.side_effect = fake_cvt

        # absdiff on two numpy arrays: delegate to real numpy computation
        def fake_absdiff(a, b):
            result = MagicMock()
            result.mean.return_value = float(np.abs(a.astype(int) - b.astype(int)).mean())
            return result

        fake_cv2.absdiff.side_effect = fake_absdiff
        fake_cv2.VideoCapture.return_value = cap
        return cap

    def test_large_pixel_diff_returns_true(self):
        h, w = 80, 80
        black = np.zeros((h, w), dtype=np.uint8)
        white = np.full((h, w), 255, dtype=np.uint8)
        gray_frames = [black, white, black, white, black]

        ctx, fake_cv2 = _inject_fake_cv2()
        self._cap_with_frames(fake_cv2, gray_frames)
        with ctx:
            result = detect_animation("fake.mp4", 0.0, 10.0, samples=5)
        assert result is True

    def test_identical_frames_returns_false(self):
        h, w = 80, 80
        frame = np.full((h, w), 128, dtype=np.uint8)
        gray_frames = [frame.copy() for _ in range(5)]

        ctx, fake_cv2 = _inject_fake_cv2()
        self._cap_with_frames(fake_cv2, gray_frames)
        with ctx:
            result = detect_animation("fake.mp4", 0.0, 10.0, samples=5)
        assert result is False

    def test_video_cannot_open_returns_false(self):
        ctx, fake_cv2 = _inject_fake_cv2()
        cap = MagicMock()
        cap.isOpened.return_value = False
        fake_cv2.VideoCapture.return_value = cap
        with ctx:
            assert detect_animation("no_file.mp4", 0.0, 5.0) is False

    def test_only_one_readable_frame_returns_false(self):
        h, w = 50, 50
        gray_frames = [np.zeros((h, w), dtype=np.uint8)]  # only 1 frame

        ctx, fake_cv2 = _inject_fake_cv2()
        self._cap_with_frames(fake_cv2, gray_frames)
        with ctx:
            result = detect_animation("fake.mp4", 0.0, 5.0, samples=5)
        assert result is False


# ---------------------------------------------------------------------------
# get_keyframes
# ---------------------------------------------------------------------------


class TestGetKeyframes:
    def test_scene_strategy_includes_scene_timestamps(self):
        with (
            patch("server.tools.frames.get_video_duration", return_value=60.0),
            patch("server.tools.frames.detect_scene_timestamps", return_value=[10.0, 25.0]),
            patch("server.tools.frames.extract_frame_as_base64", return_value="B64"),
            patch("server.tools.frames.detect_animation", return_value=False),
        ):
            frames = get_keyframes("fake.mp4", strategy="scene")

        timestamps = {f["t"] for f in frames}
        assert {0.0, 10.0, 25.0}.issubset(timestamps)

    def test_interval_strategy_evenly_spaced_timestamps(self):
        with (
            patch("server.tools.frames.get_video_duration", return_value=90.0),
            patch("server.tools.frames.detect_scene_timestamps", return_value=[]),
            patch("server.tools.frames.extract_frame_as_base64", return_value="B64"),
            patch("server.tools.frames.detect_animation", return_value=False),
        ):
            frames = get_keyframes("fake.mp4", strategy="interval", interval=30)

        timestamps = {f["t"] for f in frames}
        assert {0.0, 30.0, 60.0}.issubset(timestamps)
        assert 90.0 not in timestamps  # strict: t < duration

    def test_both_strategy_unions_scene_and_interval(self):
        with (
            patch("server.tools.frames.get_video_duration", return_value=120.0),
            patch("server.tools.frames.detect_scene_timestamps", return_value=[15.0, 45.0]),
            patch("server.tools.frames.extract_frame_as_base64", return_value="B64"),
            patch("server.tools.frames.detect_animation", return_value=False),
        ):
            frames = get_keyframes("fake.mp4", strategy="both", interval=60)

        timestamps = {f["t"] for f in frames}
        assert {0.0, 15.0, 45.0, 60.0}.issubset(timestamps)

    def test_always_includes_t_zero(self):
        with (
            patch("server.tools.frames.get_video_duration", return_value=60.0),
            patch("server.tools.frames.detect_scene_timestamps", return_value=[20.0, 40.0]),
            patch("server.tools.frames.extract_frame_as_base64", return_value="B64"),
            patch("server.tools.frames.detect_animation", return_value=False),
        ):
            frames = get_keyframes("fake.mp4", strategy="scene")

        assert frames[0]["t"] == 0.0

    def test_failed_frame_extraction_skipped(self):
        # First call (t=0) returns None, rest return B64
        with (
            patch("server.tools.frames.get_video_duration", return_value=60.0),
            patch("server.tools.frames.detect_scene_timestamps", return_value=[20.0]),
            patch(
                "server.tools.frames.extract_frame_as_base64",
                side_effect=[None, "B64"],
            ),
            patch("server.tools.frames.detect_animation", return_value=False),
        ):
            frames = get_keyframes("fake.mp4", strategy="scene")

        # t=0 was skipped, only t=20 returned
        assert len(frames) == 1
        assert frames[0]["t"] == 20.0

    def test_scene_change_flag_correct(self):
        with (
            patch("server.tools.frames.get_video_duration", return_value=60.0),
            patch("server.tools.frames.detect_scene_timestamps", return_value=[20.0]),
            patch("server.tools.frames.extract_frame_as_base64", return_value="B64"),
            patch("server.tools.frames.detect_animation", return_value=False),
        ):
            frames = get_keyframes("fake.mp4", strategy="scene")

        ts_map = {f["t"]: f for f in frames}
        assert ts_map[0.0]["scene_change"] is False
        assert ts_map[20.0]["scene_change"] is True

    def test_frame_dict_has_all_required_keys(self):
        with (
            patch("server.tools.frames.get_video_duration", return_value=30.0),
            patch("server.tools.frames.detect_scene_timestamps", return_value=[]),
            patch("server.tools.frames.extract_frame_as_base64", return_value="B64"),
            patch("server.tools.frames.detect_animation", return_value=False),
        ):
            frames = get_keyframes("fake.mp4", strategy="interval", interval=30)

        assert len(frames) >= 1
        for frame in frames:
            assert set(frame.keys()) == {
                "t",
                "t_formatted",
                "keyframe",
                "scene_change",
                "animation_detected",
            }

    def test_animation_flag_propagated(self):
        with (
            patch("server.tools.frames.get_video_duration", return_value=30.0),
            patch("server.tools.frames.detect_scene_timestamps", return_value=[]),
            patch("server.tools.frames.extract_frame_as_base64", return_value="B64"),
            patch("server.tools.frames.detect_animation", return_value=True),
        ):
            frames = get_keyframes("fake.mp4", strategy="interval", interval=30)

        assert all(f["animation_detected"] is True for f in frames)

    def test_timestamps_deduplicated(self):
        # Scene at 0.0 and interval at 0.0 should produce exactly one t=0 frame
        with (
            patch("server.tools.frames.get_video_duration", return_value=60.0),
            patch("server.tools.frames.detect_scene_timestamps", return_value=[0.001]),
            patch("server.tools.frames.extract_frame_as_base64", return_value="B64"),
            patch("server.tools.frames.detect_animation", return_value=False),
        ):
            frames = get_keyframes("fake.mp4", strategy="both", interval=60)

        # All timestamps must be unique
        timestamps = [f["t"] for f in frames]
        assert len(timestamps) == len(set(timestamps))
