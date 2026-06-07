from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path

from .config import AssistantError, Config


@dataclass(frozen=True)
class VoiceSettings:
    engine: str
    profile: str
    kokoro_lang: str
    kokoro_voice: str
    kokoro_speed: float
    piper_model: str
    piper_voice_config: str
    piper_length_scale: float
    piper_sentence_silence: float
    espeak_voice: str
    speed: float
    volume: float
    gender: str
    effects: bool
    pitch: int
    tempo: float
    bass: float
    compress: bool
    reverb: str


DEEP_MALE_PROFILE = "deep_male_latam_ai"
KOKORO_MALE_VOICE_CANDIDATES = ["em_alex", "em_santa", "am_adam", "am_michael", "bm_george"]
VOICE_TEST_PHRASES = [
    "Sistema iniciado. Voz grave, varonil y tecnológica activada.",
    "Listo, señor. Estoy preparado para asistirle.",
    "He detectado una ventana de terminal con un error de dependencias.",
    "Abriendo Spotify.",
    "Ejecutando comando autorizado.",
]


def state_path(cfg: Config) -> Path:
    return cfg.root / ".voice_state.json"


def tts_process_path(cfg: Config) -> Path:
    return cfg.root / ".tts_processes.json"


def stop_request_path(cfg: Config) -> Path:
    return cfg.root / ".tts_stop_requested"


def _read_tts_processes(cfg: Config) -> list[dict[str, object]]:
    path = tts_process_path(cfg)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _write_tts_processes(cfg: Config, processes: list[dict[str, object]]) -> None:
    tts_process_path(cfg).write_text(json.dumps(processes, indent=2), encoding="utf-8")


def _process_pid(item: dict[str, object]) -> int:
    try:
        return int(item.get("pid", -1))
    except Exception:
        return -1


def _register_tts_process(cfg: Config, pid: int, label: str) -> None:
    processes = [item for item in _read_tts_processes(cfg) if _process_pid(item) != pid]
    processes.append({"pid": pid, "label": label, "started_at": time.time()})
    _write_tts_processes(cfg, processes)


def _unregister_tts_process(cfg: Config, pid: int) -> None:
    processes = [item for item in _read_tts_processes(cfg) if _process_pid(item) != pid]
    if processes:
        _write_tts_processes(cfg, processes)
    else:
        tts_process_path(cfg).unlink(missing_ok=True)


def _kill_process_group(pid: int, sig: signal.Signals) -> bool:
    try:
        os.killpg(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        try:
            os.kill(pid, sig)
            return True
        except ProcessLookupError:
            return False
        except Exception:
            return False


def stop_current_speech(cfg: Config) -> int:
    """Stops the assistant voice currently generated/played by this project."""
    stop_request_path(cfg).write_text(str(time.time()), encoding="utf-8")
    processes = _read_tts_processes(cfg)
    stopped = 0

    for item in processes:
        try:
            pid = int(item.get("pid", 0))
        except Exception:
            continue
        if pid > 0 and _kill_process_group(pid, signal.SIGTERM):
            stopped += 1

    if stopped:
        time.sleep(0.15)
        for item in processes:
            try:
                pid = int(item.get("pid", 0))
            except Exception:
                continue
            if pid > 0:
                _kill_process_group(pid, signal.SIGKILL)

    tts_process_path(cfg).unlink(missing_ok=True)
    return stopped


def stop_requested_since(cfg: Config, started_at: float) -> bool:
    path = stop_request_path(cfg)
    if not path.exists():
        return False
    try:
        return path.stat().st_mtime >= started_at - 0.05
    except Exception:
        return False


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _state_bool(data: dict, key: str, default: bool) -> bool:
    raw = data.get(key, default)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def _sentence_end(value: str) -> str:
    value = value.rstrip(",;: ").strip()
    if not value:
        return ""
    return value if value[-1] in ".!?" else value + "."


def _split_long_sentence(sentence: str, limit: int = 150) -> list[str]:
    sentence = sentence.strip()
    if len(sentence) <= limit:
        return [sentence]
    parts = re.split(r"([,;:])", sentence)
    chunks: list[str] = []
    current = ""
    for index in range(0, len(parts), 2):
        piece = parts[index].strip()
        punctuation = parts[index + 1] if index + 1 < len(parts) else ""
        candidate = f"{current} {piece}{punctuation}".strip()
        if len(candidate) > limit and current:
            chunks.append(_sentence_end(current))
            current = f"{piece}{punctuation}".strip()
        else:
            current = candidate
    if current:
        chunks.append(_sentence_end(current))
    return chunks


def prepare_tts_text(text: str) -> str:
    cleaned = re.sub(r"```.*?```", "Incluye un bloque de codigo, no lo leere completo.", text, flags=re.DOTALL)
    cleaned = re.sub(r"`([^`]{1,80})`", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""

    if len(cleaned) > 850:
        raw_sentences = re.split(r"(?<=[.!?¿?])\s+", cleaned)
        cleaned = " ".join(raw_sentences[:3]).strip()
        cleaned += " Puedo darte el detalle completo si lo pides."

    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", cleaned) if item.strip()]
    final_sentences: list[str] = []
    for sentence in sentences:
        final_sentences.extend(_split_long_sentence(sentence))

    normalized = " ".join(final_sentences)
    if normalized and normalized[-1] not in ".!?":
        normalized += "."
    return normalized


def load_voice_settings(cfg: Config) -> VoiceSettings:
    data = {}
    path = state_path(cfg)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    return VoiceSettings(
        engine=str(data.get("engine", cfg.tts_engine)).lower(),
        profile=str(data.get("profile", cfg.voice_profile)),
        kokoro_lang=str(data.get("kokoro_lang", cfg.kokoro_lang)),
        kokoro_voice=str(data.get("kokoro_voice", cfg.kokoro_voice)),
        kokoro_speed=_clamp(float(data.get("kokoro_speed", cfg.kokoro_speed)), 0.65, 1.35),
        piper_model=str(data.get("piper_model", cfg.piper_model or autodetect_piper_model() or "")),
        piper_voice_config=str(data.get("piper_voice_config", cfg.piper_voice_config)),
        piper_length_scale=_clamp(float(data.get("piper_length_scale", cfg.piper_length_scale)), 0.75, 1.8),
        piper_sentence_silence=_clamp(float(data.get("piper_sentence_silence", cfg.piper_sentence_silence)), 0.0, 1.5),
        espeak_voice=str(data.get("espeak_voice", cfg.espeak_voice)),
        speed=_clamp(float(data.get("speed", cfg.tts_speed)), 0.65, 1.6),
        volume=_clamp(float(data.get("volume", cfg.tts_volume)), 0.2, 2.0),
        gender=str(data.get("gender", "masculina")).lower(),
        effects=_state_bool(data, "effects", cfg.voice_effects),
        pitch=int(data.get("pitch", cfg.voice_pitch)),
        tempo=_clamp(float(data.get("tempo", cfg.voice_tempo)), 0.82, 1.18),
        bass=_clamp(float(data.get("bass", cfg.voice_bass)), -8.0, 10.0),
        compress=_state_bool(data, "compress", cfg.voice_compress),
        reverb=str(data.get("reverb", cfg.voice_reverb)).lower(),
    )


def save_voice_settings(cfg: Config, settings: VoiceSettings) -> None:
    payload = {
        "engine": settings.engine,
        "profile": settings.profile,
        "kokoro_lang": settings.kokoro_lang,
        "kokoro_voice": settings.kokoro_voice,
        "kokoro_speed": settings.kokoro_speed,
        "piper_model": settings.piper_model,
        "piper_voice_config": settings.piper_voice_config,
        "piper_length_scale": settings.piper_length_scale,
        "piper_sentence_silence": settings.piper_sentence_silence,
        "espeak_voice": settings.espeak_voice,
        "speed": settings.speed,
        "volume": settings.volume,
        "gender": settings.gender,
        "effects": settings.effects,
        "pitch": settings.pitch,
        "tempo": settings.tempo,
        "bass": settings.bass,
        "compress": settings.compress,
        "reverb": settings.reverb,
    }
    state_path(cfg).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def candidate_voice_dirs() -> list[Path]:
    return [
        Path.home() / ".local/share/piper/voices",
        Path.home() / ".local/share/piper",
        Path.home() / ".local/share/voice-codex-assistant/voices",
        Path("/usr/share/piper-voices"),
        Path("/usr/share/piper/voices"),
        Path("/opt/piper/voices"),
    ]


def list_piper_voices() -> list[Path]:
    voices: list[Path] = []
    for directory in candidate_voice_dirs():
        if directory.exists():
            voices.extend(sorted(directory.rglob("*.onnx")))
    return voices


def _score_piper_voice(path: Path) -> int:
    name = path.name.lower()
    score = 0
    for token in ["es_", "es-", "spanish", "latam", "latin", "mx", "ar", "co"]:
        if token in name:
            score += 10
    for token in ["male", "masc", "hombre", "m3", "medium", "high"]:
        if token in name:
            score += 4
    for token in ["female", "fem", "mujer"]:
        if token in name:
            score -= 2
    return score


def autodetect_piper_model() -> str | None:
    voices = list_piper_voices()
    if not voices:
        return None
    return str(max(voices, key=_score_piper_voice))


def find_piper_voice(*tokens: str) -> str | None:
    normalized_tokens = [token.lower() for token in tokens if token]
    for voice in list_piper_voices():
        haystack = str(voice).lower()
        if all(token in haystack for token in normalized_tokens):
            return str(voice)
    return None


def preferred_kokoro_voice(configured_voice: str = "") -> str:
    if configured_voice:
        return configured_voice
    return KOKORO_MALE_VOICE_CANDIDATES[0]


def list_available_voices(cfg: Config) -> dict[str, list[str]]:
    voices = {
        "piper": [str(path) for path in list_piper_voices()],
        "kokoro": [],
        "espeak": [],
    }

    try:
        __import__("kokoro")
        voices["kokoro"].extend(KOKORO_MALE_VOICE_CANDIDATES)
    except Exception:
        pass
    if shutil.which("kokoro"):
        voices["kokoro"].append("cli:kokoro")
    if shutil.which("espeak-ng"):
        try:
            result = subprocess.run(["espeak-ng", "--voices=es"], capture_output=True, text=True, timeout=5)
            voices["espeak"] = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        except Exception:
            voices["espeak"] = ["es-la+m3", "es+m3", "es"]

    return voices


def deep_male_settings(cfg: Config, current: VoiceSettings | None = None) -> VoiceSettings:
    base = current or load_voice_settings(cfg)
    piper_model = find_piper_voice("davefx") or base.piper_model or cfg.piper_model or autodetect_piper_model() or ""
    config_path = f"{piper_model}.json" if piper_model and Path(f"{piper_model}.json").exists() else base.piper_voice_config
    if not config_path and cfg.piper_voice_config:
        config_path = cfg.piper_voice_config
    return replace(
        base,
        engine="kokoro",
        profile=DEEP_MALE_PROFILE,
        gender="masculina",
        kokoro_lang=cfg.kokoro_lang or "e",
        kokoro_voice=preferred_kokoro_voice(cfg.kokoro_voice),
        kokoro_speed=0.82,
        piper_model=piper_model,
        piper_voice_config=config_path,
        piper_length_scale=1.25,
        piper_sentence_silence=0.45,
        espeak_voice="es-419",
        speed=0.82,
        effects=True,
        pitch=-420,
        tempo=0.86,
        bass=6.0,
        compress=True,
        reverb="none",
    )


def activate_deep_male_profile(cfg: Config) -> VoiceSettings:
    settings = deep_male_settings(cfg)
    save_voice_settings(cfg, settings)
    return settings


def set_voice(
    cfg: Config,
    *,
    engine: str | None = None,
    profile: str | None = None,
    gender: str | None = None,
    speed: float | None = None,
    volume: float | None = None,
    kokoro_lang: str | None = None,
    kokoro_voice: str | None = None,
    kokoro_speed: float | None = None,
    piper_model: str | None = None,
    piper_voice_config: str | None = None,
    piper_length_scale: float | None = None,
    piper_sentence_silence: float | None = None,
    effects: bool | None = None,
    pitch: int | None = None,
    tempo: float | None = None,
    bass: float | None = None,
    compress: bool | None = None,
    reverb: str | None = None,
) -> VoiceSettings:
    current = load_voice_settings(cfg)
    selected_engine = (engine or current.engine).lower()
    selected_gender = (gender or current.gender).lower()
    selected_piper = piper_model if piper_model is not None else current.piper_model
    selected_espeak = current.espeak_voice

    if selected_gender.startswith("masc"):
        selected_espeak = "es-la+m3"
    elif selected_gender.startswith("fem"):
        selected_espeak = "es-la+f3"

    settings = VoiceSettings(
        engine=selected_engine,
        profile=profile if profile is not None else current.profile,
        kokoro_lang=kokoro_lang if kokoro_lang is not None else current.kokoro_lang,
        kokoro_voice=kokoro_voice if kokoro_voice is not None else current.kokoro_voice,
        kokoro_speed=_clamp(kokoro_speed if kokoro_speed is not None else current.kokoro_speed, 0.65, 1.35),
        piper_model=selected_piper,
        piper_voice_config=piper_voice_config if piper_voice_config is not None else current.piper_voice_config,
        piper_length_scale=_clamp(
            piper_length_scale if piper_length_scale is not None else current.piper_length_scale,
            0.75,
            1.8,
        ),
        piper_sentence_silence=_clamp(
            piper_sentence_silence if piper_sentence_silence is not None else current.piper_sentence_silence,
            0.0,
            1.5,
        ),
        espeak_voice=selected_espeak,
        speed=_clamp(speed if speed is not None else current.speed, 0.65, 1.6),
        volume=_clamp(volume if volume is not None else current.volume, 0.2, 2.0),
        gender=selected_gender,
        effects=effects if effects is not None else current.effects,
        pitch=int(_clamp(float(pitch if pitch is not None else current.pitch), -600, 150)),
        tempo=_clamp(tempo if tempo is not None else current.tempo, 0.82, 1.18),
        bass=_clamp(bass if bass is not None else current.bass, -8.0, 10.0),
        compress=compress if compress is not None else current.compress,
        reverb=(reverb if reverb is not None else current.reverb).lower(),
    )
    save_voice_settings(cfg, settings)
    return settings


def _run_tts_process(command: list[str], cfg: Config, label: str, *, input_text: str | None = None, timeout: int | None = None) -> bool:
    stdin = subprocess.PIPE if input_text is not None else subprocess.DEVNULL
    try:
        process = subprocess.Popen(
            command,
            stdin=stdin,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
    except Exception:
        return False

    _register_tts_process(cfg, process.pid, label)
    try:
        process.communicate(input=input_text, timeout=timeout)
        return process.returncode == 0
    except subprocess.TimeoutExpired:
        _kill_process_group(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=1)
        except Exception:
            _kill_process_group(process.pid, signal.SIGKILL)
        return False
    finally:
        _unregister_tts_process(cfg, process.pid)


def _run_file_command(command: list[str], cfg: Config, label: str, timeout: int = 45) -> bool:
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        return False

    _register_tts_process(cfg, process.pid, label)
    try:
        process.wait(timeout=timeout)
        return process.returncode == 0
    except subprocess.TimeoutExpired:
        _kill_process_group(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=1)
        except Exception:
            _kill_process_group(process.pid, signal.SIGKILL)
        return False
    finally:
        _unregister_tts_process(cfg, process.pid)


def _reverb_amount(settings: VoiceSettings) -> str:
    if settings.reverb in {"0", "false", "off", "none", "no"}:
        return "0"
    if settings.reverb in {"medium", "medio"}:
        return "14"
    if settings.reverb in {"heavy", "fuerte"}:
        return "18"
    return "10"


def _wav_sample_rate(path: str) -> int:
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "stream=sample_rate", "-of", "default=nw=1:nk=1", path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return int(result.stdout.strip().splitlines()[0])
        except Exception:
            pass
    return 22050


def _postprocess_wav(input_path: str, cfg: Config, settings: VoiceSettings) -> str:
    if not settings.effects:
        return input_path

    output_path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    sox = shutil.which("sox")
    if sox:
        command = [
            sox,
            input_path,
            output_path,
            "pitch",
            str(settings.pitch),
            "tempo",
            f"{settings.tempo:.2f}",
            "bass",
            f"{settings.bass:+.1f}",
        ]
        if settings.compress:
            command += ["compand", "0.3,1", "6:-70,-60,-20", "-5", "-90", "0.2"]
        if _reverb_amount(settings) != "0":
            command += ["reverb", _reverb_amount(settings)]
        if _run_file_command(command, cfg, "sox-effects"):
            return output_path

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        sample_rate = _wav_sample_rate(input_path)
        pitch_factor = 2 ** (settings.pitch / 1200)
        atempo = _clamp(settings.tempo, 0.82, 1.18)
        filters = [
            f"asetrate={sample_rate}*{pitch_factor:.6f}",
            f"aresample={sample_rate}",
            f"atempo={1 / pitch_factor:.6f}",
            f"atempo={atempo:.3f}",
            f"bass=g={settings.bass:.1f}:f=110:w=0.6",
        ]
        if settings.compress:
            filters.append("acompressor=threshold=-18dB:ratio=2.2:attack=18:release=180:makeup=1.6")
        if _reverb_amount(settings) != "0":
            filters.append("aecho=0.7:0.18:38:0.08")
        filters.append("loudnorm=I=-18:TP=-1.5:LRA=9")
        filters.append(f"aresample={sample_rate}")
        command = [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            input_path,
            "-af",
            ",".join(filters),
            output_path,
        ]
        if _run_file_command(command, cfg, "ffmpeg-effects"):
            return output_path

    Path(output_path).unlink(missing_ok=True)
    return input_path


def _play_wav(path: str, cfg: Config, settings: VoiceSettings) -> bool:
    mpv = shutil.which("mpv")
    ffplay = shutil.which("ffplay")
    aplay = shutil.which("aplay")
    if mpv:
        return _run_tts_process(
            [mpv, "--no-video", "--really-quiet", f"--volume={int(settings.volume * 100)}", path],
            cfg,
            "mpv",
        )
    if ffplay:
        return _run_tts_process(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", str(int(settings.volume * 100)), path],
            cfg,
            "ffplay",
        )
    if aplay:
        return _run_tts_process(["aplay", path], cfg, "aplay")
    return False


def piper_executable(cfg: Config) -> str | None:
    for candidate in [cfg.root / ".venv/bin/piper", cfg.root / ".venv/bin/piper-tts"]:
        if candidate.exists():
            return str(candidate)
    return shutil.which("piper")


def speak_with_piper(text: str, cfg: Config, settings: VoiceSettings) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        if not render_piper_wav(text, cfg, settings, wav_path):
            Path(wav_path).unlink(missing_ok=True)
            return False
        play_path = _postprocess_wav(wav_path, cfg, settings)
        played = _play_wav(play_path, cfg, settings)
        if play_path != wav_path:
            Path(play_path).unlink(missing_ok=True)
        Path(wav_path).unlink(missing_ok=True)
        return played
    except Exception:
        Path(wav_path).unlink(missing_ok=True)
        return False


def render_piper_wav(text: str, cfg: Config, settings: VoiceSettings, output_path: str) -> bool:
    piper = piper_executable(cfg)
    model = settings.piper_model or cfg.piper_model or autodetect_piper_model()
    if not piper or not model:
        return False

    command = [piper, "--model", model]
    config_path = settings.piper_voice_config or cfg.piper_voice_config
    if config_path:
        command += ["--config", config_path]

    command += [
        "--length-scale",
        f"{settings.piper_length_scale:.2f}",
        "--sentence-silence",
        f"{settings.piper_sentence_silence:.2f}",
        "--volume",
        f"{settings.volume:.2f}",
        "--output-file",
        output_path,
    ]
    return _run_tts_process(command, cfg, "piper", input_text=text, timeout=60)


def kokoro_python_executable(cfg: Config) -> str | None:
    candidates = []
    if cfg.kokoro_python:
        candidates.append(Path(cfg.kokoro_python))
    candidates.append(cfg.root / ".venv-kokoro/bin/python")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def kokoro_python_available(cfg: Config) -> str | None:
    python = kokoro_python_executable(cfg)
    if not python:
        return None
    try:
        result = subprocess.run(
            [python, "-c", "import kokoro, soundfile"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
    except Exception:
        return None
    return python if result.returncode == 0 else None


def _speak_with_kokoro_external_python(text: str, cfg: Config, settings: VoiceSettings, output_path: str) -> bool:
    python = kokoro_python_available(cfg)
    if not python:
        return False

    script = """
import sys
import numpy as np
import soundfile as sf
from kokoro import KPipeline

text, output_path, lang, voice, speed = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5])
pipeline = KPipeline(lang_code=lang)
chunks = []
for _graphemes, _phonemes, audio in pipeline(text, voice=voice, speed=speed):
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    chunks.append(audio)
if not chunks:
    raise SystemExit(2)
sf.write(output_path, np.concatenate(chunks), 24000)
"""
    return _run_tts_process(
        [python, "-c", script, text, output_path, settings.kokoro_lang, settings.kokoro_voice, f"{settings.kokoro_speed:.2f}"],
        cfg,
        "kokoro-python",
        timeout=120,
    )


def _speak_with_kokoro_python(text: str, cfg: Config, settings: VoiceSettings, output_path: str) -> bool:
    try:
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline

        pipeline = KPipeline(lang_code=settings.kokoro_lang)
        generator = pipeline(text, voice=settings.kokoro_voice, speed=settings.kokoro_speed)
        chunks = []
        for _graphemes, _phonemes, audio in generator:
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            chunks.append(audio)
        if not chunks:
            return False
        sf.write(output_path, np.concatenate(chunks), 24000)
        return True
    except Exception:
        return False


def _speak_with_kokoro_cli(text: str, cfg: Config, settings: VoiceSettings, output_path: str) -> bool:
    kokoro = shutil.which("kokoro")
    if not kokoro:
        return False

    commands = [
        [
            kokoro,
            "--text",
            text,
            "--output",
            output_path,
            "--lang",
            settings.kokoro_lang,
            "--voice",
            settings.kokoro_voice,
            "--speed",
            f"{settings.kokoro_speed:.2f}",
        ],
        [kokoro, "--text", text, "--output", output_path],
    ]
    return any(_run_tts_process(command, cfg, "kokoro", timeout=90) for command in commands)


def speak_with_kokoro(text: str, cfg: Config, settings: VoiceSettings) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        if not render_kokoro_wav(text, cfg, settings, wav_path):
            Path(wav_path).unlink(missing_ok=True)
            return False
        play_path = _postprocess_wav(wav_path, cfg, settings)
        played = _play_wav(play_path, cfg, settings)
        if play_path != wav_path:
            Path(play_path).unlink(missing_ok=True)
        Path(wav_path).unlink(missing_ok=True)
        return played
    except Exception:
        Path(wav_path).unlink(missing_ok=True)
        return False


def render_kokoro_wav(text: str, cfg: Config, settings: VoiceSettings, output_path: str) -> bool:
    return (
        _speak_with_kokoro_external_python(text, cfg, settings, output_path)
        or _speak_with_kokoro_python(text, cfg, settings, output_path)
        or _speak_with_kokoro_cli(text, cfg, settings, output_path)
    )


def speak_with_espeak(text: str, cfg: Config, settings: VoiceSettings) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        if not render_espeak_wav(text, cfg, settings, wav_path):
            Path(wav_path).unlink(missing_ok=True)
            return False
        play_path = _postprocess_wav(wav_path, cfg, settings)
        played = _play_wav(play_path, cfg, settings)
        if play_path != wav_path:
            Path(play_path).unlink(missing_ok=True)
        Path(wav_path).unlink(missing_ok=True)
        return played
    except Exception:
        Path(wav_path).unlink(missing_ok=True)
        return False


def render_espeak_wav(text: str, cfg: Config, settings: VoiceSettings, output_path: str) -> bool:
    espeak = shutil.which("espeak-ng")
    if not espeak:
        return False

    words_per_minute = int(_clamp(130 * settings.speed, 85, 190))
    amplitude = int(_clamp(100 * settings.volume, 20, 200))
    voices = []
    for voice in [settings.espeak_voice, "es-419", "es-la+m3", "es+m3", "es"]:
        if voice and voice not in voices:
            voices.append(voice)

    for voice in voices:
        command = [espeak, "-v", voice, "-s", str(words_per_minute), "-a", str(amplitude), "-p", "22", "-w", output_path, text]
        if _run_tts_process(command, cfg, "espeak", timeout=45):
            return True
    return False


def render_wav(text: str, cfg: Config, settings: VoiceSettings, output_path: str, *, apply_effects: bool = True) -> bool:
    prepared = prepare_tts_text(text)
    if not prepared:
        return False

    raw_path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    try:
        engines = [settings.engine, "kokoro", "piper", "espeak"]
        ordered: list[str] = []
        for engine in engines:
            if engine and engine not in ordered:
                ordered.append(engine)

        rendered = False
        for engine in ordered:
            if engine == "kokoro" and render_kokoro_wav(prepared, cfg, settings, raw_path):
                rendered = True
                break
            if engine == "piper" and render_piper_wav(prepared, cfg, settings, raw_path):
                rendered = True
                break
            if engine == "espeak" and render_espeak_wav(prepared, cfg, settings, raw_path):
                rendered = True
                break
        if not rendered:
            return False

        source = raw_path
        if apply_effects:
            source = _postprocess_wav(raw_path, cfg, settings)
        shutil.copyfile(source, output_path)
        if source != raw_path:
            Path(source).unlink(missing_ok=True)
        return True
    finally:
        Path(raw_path).unlink(missing_ok=True)


def create_voice_samples(cfg: Config) -> dict[str, Path]:
    sample_dir = cfg.root / "voice_samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_text = " ".join(VOICE_TEST_PHRASES)

    current = load_voice_settings(cfg)
    deep = deep_male_settings(cfg, current)
    slow = replace(deep, tempo=0.82, kokoro_speed=0.78, piper_length_scale=1.38, piper_sentence_silence=0.6)
    original = replace(deep, effects=False, pitch=0, tempo=1.0, bass=0.0, compress=False, reverb="none")

    outputs = {
        "original": sample_dir / "original.wav",
        DEEP_MALE_PROFILE: sample_dir / f"{DEEP_MALE_PROFILE}.wav",
        "slow_human_rhythm": sample_dir / "slow_human_rhythm.wav",
    }
    if not render_wav(sample_text, cfg, original, str(outputs["original"]), apply_effects=False):
        raise AssistantError("No pude generar voice_samples/original.wav.")
    if not render_wav(sample_text, cfg, deep, str(outputs[DEEP_MALE_PROFILE]), apply_effects=True):
        raise AssistantError(f"No pude generar voice_samples/{DEEP_MALE_PROFILE}.wav.")
    if not render_wav(sample_text, cfg, slow, str(outputs["slow_human_rhythm"]), apply_effects=True):
        raise AssistantError("No pude generar voice_samples/slow_human_rhythm.wav.")
    return outputs


def play_voice_test(cfg: Config) -> dict[str, Path]:
    outputs = create_voice_samples(cfg)
    settings = deep_male_settings(cfg)
    save_voice_settings(cfg, settings)
    for phrase in VOICE_TEST_PHRASES:
        speak(phrase, cfg)
    _play_wav(str(outputs[DEEP_MALE_PROFILE]), cfg, settings)
    return outputs


def effective_engine(cfg: Config, settings: VoiceSettings | None = None) -> str | None:
    settings = settings or load_voice_settings(cfg)
    if settings.engine == "kokoro" and (
        kokoro_python_available(cfg) or shutil.which("kokoro") or _module_available("kokoro")
    ):
        return "kokoro"
    if piper_executable(cfg) and (settings.piper_model or cfg.piper_model or autodetect_piper_model()):
        return "piper"
    if shutil.which("espeak-ng"):
        return "espeak"
    return None


def _module_available(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except Exception:
        return False


def speak(text: str, cfg: Config) -> None:
    started_at = time.time()
    settings = load_voice_settings(cfg)
    text = prepare_tts_text(text)
    if not text:
        return
    engines = [settings.engine, "kokoro", "piper", "espeak"]
    ordered = []
    for engine in engines:
        if engine and engine not in ordered:
            ordered.append(engine)

    for engine in ordered:
        if engine == "kokoro" and speak_with_kokoro(text, cfg, settings):
            return
        if stop_requested_since(cfg, started_at):
            return
        if engine == "piper" and speak_with_piper(text, cfg, settings):
            return
        if stop_requested_since(cfg, started_at):
            return
        if engine == "espeak" and speak_with_espeak(text, cfg, settings):
            return
        if stop_requested_since(cfg, started_at):
            return

    raise AssistantError("Fallo el TTS. Instala Kokoro, Piper o espeak-ng.")


def check_tts(cfg: Config) -> tuple[bool, str]:
    settings = load_voice_settings(cfg)
    available = []
    for command in ["piper", "kokoro", "espeak-ng", "mpv", "ffplay", "aplay", "sox", "ffmpeg"]:
        path = shutil.which(command)
        if path:
            available.append(f"{command}={path}")

    kokoro_python = kokoro_python_available(cfg)
    if kokoro_python:
        return True, f"OK (motor efectivo: Kokoro Python externo: {kokoro_python}, voice={settings.kokoro_voice})"
    if _module_available("kokoro"):
        return True, f"OK (Kokoro Python: lang={settings.kokoro_lang}, voice={settings.kokoro_voice})"
    if shutil.which("kokoro"):
        return True, f"OK (Kokoro CLI: lang={settings.kokoro_lang}, voice={settings.kokoro_voice})"
    piper_model = settings.piper_model or autodetect_piper_model()
    if piper_executable(cfg) and piper_model:
        return True, f"OK (motor efectivo: Piper fallback: {piper_model})"
    if shutil.which("espeak-ng"):
        return True, "OK (fallback espeak-ng)"
    return False, "FALTA Kokoro/Piper/espeak-ng. Disponibles: " + (", ".join(available) or "ninguno")
