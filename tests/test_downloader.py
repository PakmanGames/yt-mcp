"""Tests for server/utils/downloader.py."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from server.utils.downloader import DownloadError, VideoDownloader, VideoInfo


# ---------------------------------------------------------------------------
# VideoInfo
# ---------------------------------------------------------------------------


class TestVideoInfo:
    def test_all_fields_populated(self, fake_video_info_data):
        info = VideoInfo(fake_video_info_data)
        assert info.id == "abc123"
        assert info.title == "Test Video"
        assert info.duration == 60.0
        assert info.channel == "Test Channel"
        assert info.upload_date == "20240101"
        assert "description" in info.description
        assert info.url == "https://www.youtube.com/watch?v=abc123"

    def test_duration_string_coerced_to_float(self):
        info = VideoInfo({"id": "x", "title": "t", "duration": "120"})
        assert isinstance(info.duration, float)
        assert info.duration == 120.0

    def test_duration_none_defaults_to_zero(self):
        info = VideoInfo({"id": "x", "title": "t", "duration": None})
        assert info.duration == 0.0

    def test_missing_optional_fields_default_to_empty_string(self):
        info = VideoInfo({"id": "x", "title": "t"})
        assert info.channel == ""
        assert info.upload_date == ""
        assert info.description == ""
        assert info.url == ""

    def test_description_truncated_to_500_chars(self):
        info = VideoInfo({"id": "x", "title": "t", "description": "a" * 1000})
        assert len(info.description) == 500


# ---------------------------------------------------------------------------
# VideoDownloader — __init__
# ---------------------------------------------------------------------------


class TestVideoDownloaderInit:
    def test_custom_cache_dir_is_created(self, tmp_path):
        custom = tmp_path / "my_cache"
        assert not custom.exists()
        VideoDownloader(cache_dir=custom)
        assert custom.exists()

    def test_custom_cache_dir_is_stored(self, tmp_path):
        custom = tmp_path / "store_test"
        dl = VideoDownloader(cache_dir=custom)
        assert dl.cache_dir == custom


# ---------------------------------------------------------------------------
# VideoDownloader — download: cache hit
# ---------------------------------------------------------------------------


class TestVideoDownloaderCacheHit:
    def test_returns_correct_paths_and_info(self, cache_hit_dir, fake_video_info_data):
        cache_root, video_id = cache_hit_dir
        fake_raw = {"id": video_id, **fake_video_info_data}

        with patch.object(VideoDownloader, "_extract_info", return_value=fake_raw):
            dl = VideoDownloader(cache_dir=cache_root)
            video_path, audio_path, info = dl.download(fake_video_info_data["url"])

        assert video_path.endswith("video.mp4")
        assert audio_path.endswith("audio.wav")
        assert info.id == video_id
        assert info.title == fake_video_info_data["title"]

    def test_cache_hit_injects_url_into_info(self, cache_hit_dir, fake_video_info_data):
        cache_root, video_id = cache_hit_dir
        url = "https://www.youtube.com/watch?v=abc123"
        fake_raw = {"id": video_id, **fake_video_info_data}

        with patch.object(VideoDownloader, "_extract_info", return_value=fake_raw):
            dl = VideoDownloader(cache_dir=cache_root)
            _, _, info = dl.download(url)

        assert info.url == url

    def test_cache_hit_does_not_call_ytdlp_download(
        self, cache_hit_dir, fake_video_info_data
    ):
        cache_root, video_id = cache_hit_dir
        fake_raw = {"id": video_id, **fake_video_info_data}

        with patch.object(VideoDownloader, "_extract_info", return_value=fake_raw):
            with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
                dl = VideoDownloader(cache_dir=cache_root)
                dl.download(fake_video_info_data["url"])

        mock_ydl_cls.return_value.__enter__.return_value.download.assert_not_called()

    def test_cache_hit_does_not_call_ffmpeg(self, cache_hit_dir, fake_video_info_data):
        cache_root, video_id = cache_hit_dir
        fake_raw = {"id": video_id, **fake_video_info_data}

        with patch.object(VideoDownloader, "_extract_info", return_value=fake_raw):
            with patch("subprocess.run") as mock_run:
                dl = VideoDownloader(cache_dir=cache_root)
                dl.download(fake_video_info_data["url"])

        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# VideoDownloader — download: cache miss
# ---------------------------------------------------------------------------


class TestVideoDownloaderCacheMiss:
    """Tests for the full download path when cached files are absent."""

    _VIDEO_ID = "vid999"

    def _fake_raw(self):
        return {
            "id": self._VIDEO_ID,
            "title": "Fresh Video",
            "duration": 90,
            "uploader": "Some Channel",
            "upload_date": "20240601",
            "description": "desc",
        }

    def _slot(self, tmp_path):
        slot = tmp_path / self._VIDEO_ID
        slot.mkdir(parents=True, exist_ok=True)
        return slot

    def test_writes_info_json_after_download(self, tmp_path):
        slot = self._slot(tmp_path)

        def fake_ydl_download(urls):
            (slot / "video.mp4").write_bytes(b"MP4")

        def fake_ffmpeg(cmd, **kwargs):
            (slot / "audio.wav").write_bytes(b"WAV")
            return MagicMock(returncode=0)

        with patch.object(VideoDownloader, "_extract_info", return_value=self._fake_raw()):
            with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
                mock_ydl_cls.return_value.__enter__.return_value.download.side_effect = (
                    fake_ydl_download
                )
                with patch("subprocess.run", side_effect=fake_ffmpeg):
                    dl = VideoDownloader(cache_dir=tmp_path)
                    dl.download("https://yt.test/v=vid999")

        stored = json.loads((slot / "info.json").read_text())
        assert stored["id"] == self._VIDEO_ID
        assert stored["title"] == "Fresh Video"

    def test_ffmpeg_called_with_16khz_mono_args(self, tmp_path):
        slot = self._slot(tmp_path)

        def fake_ydl_download(urls):
            (slot / "video.mp4").write_bytes(b"MP4")

        captured_cmd = []

        def fake_ffmpeg(cmd, **kwargs):
            captured_cmd.extend(cmd)
            (slot / "audio.wav").write_bytes(b"WAV")
            return MagicMock(returncode=0)

        with patch.object(VideoDownloader, "_extract_info", return_value=self._fake_raw()):
            with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
                mock_ydl_cls.return_value.__enter__.return_value.download.side_effect = (
                    fake_ydl_download
                )
                with patch("subprocess.run", side_effect=fake_ffmpeg):
                    dl = VideoDownloader(cache_dir=tmp_path)
                    dl.download("https://yt.test/v=vid999")

        assert "16000" in captured_cmd  # -ar 16000
        assert "1" in captured_cmd      # -ac 1 (mono)
        assert "pcm_s16le" in captured_cmd

    def test_ytdlp_extract_error_raises_download_error(self, tmp_path):
        import yt_dlp

        with patch.object(
            VideoDownloader,
            "_extract_info",
            side_effect=yt_dlp.utils.DownloadError("not found"),
        ):
            dl = VideoDownloader(cache_dir=tmp_path)
            with pytest.raises(DownloadError, match="yt-dlp failed"):
                dl.download("https://www.youtube.com/watch?v=bad")

    def test_ffmpeg_nonzero_exit_raises_download_error(self, tmp_path):
        slot = self._slot(tmp_path)

        def fake_ydl_download(urls):
            (slot / "video.mp4").write_bytes(b"MP4")

        with patch.object(VideoDownloader, "_extract_info", return_value=self._fake_raw()):
            with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
                mock_ydl_cls.return_value.__enter__.return_value.download.side_effect = (
                    fake_ydl_download
                )
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=1, stderr=b"codec error")
                    dl = VideoDownloader(cache_dir=tmp_path)
                    with pytest.raises(DownloadError, match="FFmpeg audio extraction failed"):
                        dl.download("https://yt.test/v=vid999")

    def test_no_output_file_raises_download_error(self, tmp_path):
        self._slot(tmp_path)

        # yt-dlp downloads successfully but produces no file
        with patch.object(VideoDownloader, "_extract_info", return_value=self._fake_raw()):
            with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
                mock_ydl_cls.return_value.__enter__.return_value.download.return_value = None
                dl = VideoDownloader(cache_dir=tmp_path)
                with pytest.raises(DownloadError, match="no output file"):
                    dl.download("https://yt.test/v=vid999")

    def test_non_mp4_output_is_renamed_to_mp4(self, tmp_path):
        slot = self._slot(tmp_path)

        def fake_ydl_download(urls):
            (slot / "video.webm").write_bytes(b"WEBM")

        def fake_ffmpeg(cmd, **kwargs):
            (slot / "audio.wav").write_bytes(b"WAV")
            return MagicMock(returncode=0)

        with patch.object(VideoDownloader, "_extract_info", return_value=self._fake_raw()):
            with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
                mock_ydl_cls.return_value.__enter__.return_value.download.side_effect = (
                    fake_ydl_download
                )
                with patch("subprocess.run", side_effect=fake_ffmpeg):
                    dl = VideoDownloader(cache_dir=tmp_path)
                    dl.download("https://yt.test/v=vid999")

        assert (slot / "video.mp4").exists()
        assert not (slot / "video.webm").exists()


# ---------------------------------------------------------------------------
# VideoDownloader — clear_cache
# ---------------------------------------------------------------------------


class TestClearCache:
    def test_removes_cached_slot(self, tmp_path):
        slot = tmp_path / "some_id"
        slot.mkdir()
        (slot / "video.mp4").write_bytes(b"x")
        dl = VideoDownloader(cache_dir=tmp_path)
        dl.clear_cache("some_id")
        assert not slot.exists()

    def test_nonexistent_id_is_noop(self, tmp_path):
        dl = VideoDownloader(cache_dir=tmp_path)
        dl.clear_cache("ghost_id")  # must not raise

    def test_other_slots_are_unaffected(self, tmp_path):
        for vid_id in ("keep_me", "delete_me"):
            slot = tmp_path / vid_id
            slot.mkdir()
            (slot / "video.mp4").write_bytes(b"x")
        dl = VideoDownloader(cache_dir=tmp_path)
        dl.clear_cache("delete_me")
        assert not (tmp_path / "delete_me").exists()
        assert (tmp_path / "keep_me").exists()
