from __future__ import annotations

import wave
from pathlib import Path

from .config import AssistantError, Config, require_import


def write_wav(path: Path, audio, cfg: Config) -> None:
    np = require_import("numpy", "python -m pip install -r requirements.txt")
    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(cfg.channels)
        wav.setsampwidth(2)
        wav.setframerate(cfg.sample_rate)
        wav.writeframes(pcm.tobytes())


def check_microphone() -> tuple[bool, str]:
    try:
        sd = require_import("sounddevice", "python -m pip install -r requirements.txt")
        device = sd.query_devices(kind="input")
        return True, f"OK ({device.get('name', 'default')})"
    except Exception as exc:
        return False, f"ERROR ({exc})"


def record_until_silence(output_path: Path, cfg: Config) -> None:
    sd = require_import("sounddevice", "python -m pip install -r requirements.txt")
    np = require_import("numpy", "python -m pip install -r requirements.txt")

    try:
        sd.query_devices(kind="input")
    except Exception as exc:
        raise AssistantError("No se pudo acceder al microfono. Revisa PipeWire/PulseAudio y el dispositivo de entrada.") from exc

    block_size = int(cfg.sample_rate * cfg.block_seconds)
    silent_blocks_needed = max(1, int(cfg.silence_seconds / cfg.block_seconds))
    max_blocks = int(cfg.max_record_seconds / cfg.block_seconds)
    min_blocks = int(cfg.min_record_seconds / cfg.block_seconds)

    blocks = []
    silent_blocks = 0

    try:
        with sd.InputStream(
            samplerate=cfg.sample_rate,
            channels=cfg.channels,
            dtype="float32",
            blocksize=block_size,
        ) as stream:
            for index in range(max_blocks):
                block, _overflowed = stream.read(block_size)
                mono = np.asarray(block).reshape(-1)
                blocks.append(mono.copy())
                rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0

                if rms < cfg.silence_threshold:
                    silent_blocks += 1
                else:
                    silent_blocks = 0

                if index >= min_blocks and silent_blocks >= silent_blocks_needed:
                    break
    except Exception as exc:
        raise AssistantError("Fallo la grabacion de audio desde el microfono.") from exc

    if not blocks:
        raise AssistantError("No se grabo audio.")

    write_wav(output_path, np.concatenate(blocks), cfg)

