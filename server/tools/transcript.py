"""Whisper-based transcription with word-level timestamps."""

_model_cache: dict = {}


def _load_model(size: str):
    if size not in _model_cache:
        import whisper
        _model_cache[size] = whisper.load_model(size)
    return _model_cache[size]


def get_transcript(audio_path: str, model_size: str = "base") -> dict:
    """
    Transcribe audio using OpenAI Whisper (runs entirely locally).

    Returns:
        {
            "language": "en",
            "full_text": "...",
            "segments": [
                {
                    "t_start": 0.0,
                    "t_end": 4.5,
                    "text": "Welcome to this video.",
                    "words": [{"word": "Welcome", "start": 0.0, "end": 0.6}, ...]
                }
            ]
        }

    model_size: tiny | base | small | medium | large
    First call downloads model weights (~75MB for base, ~1.5GB for large).
    """
    model = _load_model(model_size)
    result = model.transcribe(audio_path, word_timestamps=True, verbose=False)

    segments = []
    for seg in result.get("segments", []):
        words = [
            {"word": w["word"].strip(), "start": round(w["start"], 3), "end": round(w["end"], 3)}
            for w in seg.get("words", [])
        ]
        segments.append({
            "t_start": round(seg["start"], 3),
            "t_end": round(seg["end"], 3),
            "text": seg["text"].strip(),
            "words": words,
        })

    return {
        "language": result.get("language", "unknown"),
        "full_text": result.get("text", "").strip(),
        "segments": segments,
    }


def get_text_in_range(transcript: dict, t_start: float, t_end: float) -> str:
    """Return concatenated transcript text overlapping [t_start, t_end]."""
    parts = [
        seg["text"]
        for seg in transcript["segments"]
        if seg["t_end"] > t_start and seg["t_start"] < t_end
    ]
    return " ".join(parts).strip()


def count_words_in_range(transcript: dict, t_start: float, t_end: float) -> int:
    """Count words spoken in [t_start, t_end] (for speech-rate computation)."""
    count = 0
    for seg in transcript["segments"]:
        for w in seg.get("words", []):
            if w["end"] > t_start and w["start"] < t_end:
                count += 1
    return count
