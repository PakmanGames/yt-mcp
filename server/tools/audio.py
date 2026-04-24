"""librosa-based audio feature extraction: energy, tempo, music vs speech detection."""

import numpy as np


class AudioAnalyzer:
    """
    Load a WAV file once, then analyze arbitrary time segments efficiently.

    Usage:
        analyzer = AudioAnalyzer(audio_path)
        features = analyzer.analyze_segment(t_start=0, t_end=30)
    """

    SR = 16000  # matches the 16kHz WAV produced by downloader.py

    def __init__(self, audio_path: str):
        import librosa
        self.y, self.sr = librosa.load(audio_path, sr=self.SR, mono=True)

    def analyze_segment(self, t_start: float, t_end: float) -> dict:
        """
        Analyze audio in [t_start, t_end] seconds.

        Returns:
            {
                "energy": "low" | "medium" | "high",
                "music": true | false,
                "tempo_bpm": 120.0,
                "rms_db": -18.3
            }
        """
        import librosa

        start = int(t_start * self.sr)
        end = int(t_end * self.sr)
        seg = self.y[start:end]

        if len(seg) < self.sr // 10:  # < 100ms of audio — not enough data
            return {"energy": "low", "music": False, "tempo_bpm": 0.0, "rms_db": -60.0}

        # RMS energy → dB
        rms = float(librosa.feature.rms(y=seg).mean())
        rms_db = float(librosa.amplitude_to_db(np.array([rms]))[0])

        if rms_db < -35:
            energy_level = "low"
        elif rms_db < -18:
            energy_level = "medium"
        else:
            energy_level = "high"

        # Tempo
        try:
            tempo, _ = librosa.beat.beat_track(y=seg, sr=self.sr)
            tempo_bpm = float(tempo) if np.isscalar(tempo) else float(tempo[0])
        except Exception:
            tempo_bpm = 0.0

        # Music detection via harmonic content ratio + spectral flatness.
        # Speech: low harmonic energy, high flatness.
        # Music: high harmonic energy, low flatness.
        try:
            y_harmonic, _ = librosa.effects.hpss(seg)
            seg_power = float(np.mean(seg ** 2))
            harmonic_ratio = float(np.mean(y_harmonic ** 2)) / (seg_power + 1e-10)
            flatness = float(librosa.feature.spectral_flatness(y=seg).mean())
            is_music = harmonic_ratio > 0.25 and flatness < 0.15
        except Exception:
            is_music = False

        return {
            "energy": energy_level,
            "music": is_music,
            "tempo_bpm": round(tempo_bpm, 1),
            "rms_db": round(rms_db, 1),
        }

    def analyze_full(self, segment_duration: int = 30) -> list[dict]:
        """
        Analyze the entire audio in fixed-size windows.

        Returns list of:
            {
                "t_start": 0.0,
                "t_end": 30.0,
                "energy": "medium",
                "music": false,
                "tempo_bpm": 95.0,
                "rms_db": -22.1
            }
        """
        total = len(self.y) / self.sr
        results = []
        t = 0.0
        while t < total:
            t_end = min(t + segment_duration, total)
            features = self.analyze_segment(t, t_end)
            results.append({"t_start": round(t, 3), "t_end": round(t_end, 3), **features})
            t = t_end
        return results
