from __future__ import annotations

from pathlib import Path

from .config import AssistantError, Config


def transcribe_audio(audio_path: Path, cfg: Config) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise AssistantError("Falta faster-whisper. Instala con: python -m pip install -r requirements.txt") from exc

    try:
        model = WhisperModel(cfg.whisper_model, device="auto", compute_type="auto")
        segments, _info = model.transcribe(str(audio_path), language=cfg.assistant_language, vad_filter=True)
        text = " ".join(segment.text.strip() for segment in segments).strip()
    except Exception as exc:
        raise AssistantError("Fallo la transcripcion con faster-whisper.") from exc

    if not text:
        raise AssistantError("La transcripcion salio vacia. Habla mas cerca del microfono o baja SILENCE_THRESHOLD.")

    return text

