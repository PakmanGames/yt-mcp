"""yt-dlp wrapper: downloads video and extracts a 16kHz mono WAV for Whisper/librosa."""

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

import yt_dlp

CACHE_DIR = Path(os.environ.get("YT_CACHE_DIR", "/tmp/yt-analysis-cache"))


class DownloadError(Exception):
    pass


class VideoInfo:
    def __init__(self, data: dict):
        self.id: str = data["id"]
        self.title: str = data["title"]
        self.duration: float = float(data.get("duration") or 0)
        self.channel: str = data.get("channel", "")
        self.upload_date: str = data.get("upload_date", "")
        self.description: str = (data.get("description") or "")[:500]
        self.url: str = data.get("url", "")


class VideoDownloader:
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _extract_info(self, url: str) -> dict:
        opts = {"quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise DownloadError(f"Could not fetch video info for: {url}")
            return info

    def download(self, url: str) -> tuple[str, str, "VideoInfo"]:
        """
        Download video and extract audio. Returns (video_path, audio_path, info).
        Results are cached on disk by video ID — re-calling with the same URL is instant.
        """
        try:
            raw = self._extract_info(url)
        except yt_dlp.utils.DownloadError as e:
            raise DownloadError(f"yt-dlp failed: {e}") from e

        video_id = raw["id"]
        slot = self.cache_dir / video_id
        slot.mkdir(exist_ok=True)

        video_path = slot / "video.mp4"
        audio_path = slot / "audio.wav"
        info_path = slot / "info.json"

        if video_path.exists() and audio_path.exists() and info_path.exists():
            with open(info_path) as f:
                stored = json.load(f)
            stored["url"] = url
            return str(video_path), str(audio_path), VideoInfo(stored)

        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": str(slot / "video.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as e:
            raise DownloadError(f"Download failed: {e}") from e

        # yt-dlp may produce e.g. video.webm — normalize to video.mp4
        candidates = [p for p in slot.glob("video.*") if p.suffix != ".part"]
        if not candidates:
            raise DownloadError("Download produced no output file")
        actual = candidates[0]
        if actual != video_path:
            actual.rename(video_path)

        # Extract 16kHz mono WAV (optimal for Whisper + librosa)
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(video_path),
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                str(audio_path),
            ],
            capture_output=True,
        )
        if result.returncode != 0:
            raise DownloadError(
                f"FFmpeg audio extraction failed: {result.stderr.decode()}"
            )

        info_data = {
            "id": raw.get("id"),
            "title": raw.get("title", ""),
            "duration": raw.get("duration"),
            "channel": raw.get("uploader", ""),
            "upload_date": raw.get("upload_date", ""),
            "description": (raw.get("description") or "")[:500],
            "url": url,
        }
        with open(info_path, "w") as f:
            json.dump(info_data, f)

        return str(video_path), str(audio_path), VideoInfo(info_data)

    def clear_cache(self, video_id: str) -> None:
        import shutil
        slot = self.cache_dir / video_id
        if slot.exists():
            shutil.rmtree(slot)
