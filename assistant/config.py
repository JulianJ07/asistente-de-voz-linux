from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class AssistantError(Exception):
    pass


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_dotenv_if_present(root: Path) -> None:
    env_path = root / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = clean_env_value(value)
        os.environ.setdefault(key, value)


def clean_env_value(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[1:end]
        return value.strip(quote)
    return value.split("#", 1)[0].strip()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def require_import(module_name: str, install_hint: str):
    try:
        return __import__(module_name)
    except ImportError as exc:
        raise AssistantError(f"Falta la dependencia Python '{module_name}'. Instala con: {install_hint}") from exc


def load_commands(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    try:
        yaml = require_import("yaml", "python -m pip install -r requirements.txt")
    except AssistantError:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise AssistantError(f"No pude leer {path}. Revisa la sintaxis YAML.") from exc

    if not isinstance(data, dict):
        raise AssistantError("commands.yaml debe contener un mapa YAML.")

    apps = data.get("apps", data)
    if not isinstance(apps, dict):
        raise AssistantError("La clave apps de commands.yaml debe ser un mapa.")

    return {str(key).strip().lower(): str(value).strip() for key, value in apps.items() if str(value).strip()}


@dataclass(frozen=True)
class Config:
    root: Path
    commands_file: Path
    commands: dict[str, str]
    llm_backend: str
    codex_command: str
    codex_timeout_seconds: int
    codex_model: str
    codex_reasoning_level: str
    codex_sandbox: str
    codex_bypass_approvals_and_sandbox: bool
    codex_preflight_check: bool
    assistant_language: str
    tts_engine: str
    voice_profile: str
    voice_style: str
    kokoro_lang: str
    kokoro_voice: str
    kokoro_speed: float
    kokoro_python: str
    espeak_voice: str
    piper_model: str
    piper_voice_config: str
    piper_length_scale: float
    piper_sentence_silence: float
    tts_speed: float
    tts_volume: float
    voice_effects: bool
    voice_pitch: int
    voice_tempo: float
    voice_bass: float
    voice_compress: bool
    voice_reverb: str
    require_confirmation_for_sudo: bool
    disable_safety_confirmations: bool
    whisper_model: str
    screen_path: Path
    log_dir: Path
    trigger_socket_path: Path
    daemon_poll_seconds: float
    handy_autostart: bool
    handy_process_history: bool
    handy_poll_seconds: float
    handy_appimage_path: str
    handy_history_db: Path
    handy_trigger_command: str
    direct_stt_fallback_enabled: bool
    session_type: str
    desktop: str
    sample_rate: int = 16000
    channels: int = 1
    block_seconds: float = 0.25
    max_record_seconds: int = 45
    min_record_seconds: float = 1.0
    silence_seconds: float = 1.2
    silence_threshold: float = 0.012


def load_config() -> Config:
    root = project_root()
    load_dotenv_if_present(root)
    commands_file = root / "commands.yaml"

    return Config(
        root=root,
        commands_file=commands_file,
        commands=load_commands(commands_file),
        llm_backend=os.getenv("LLM_BACKEND", "codex_cli").lower(),
        codex_command=os.getenv("CODEX_COMMAND", "codex"),
        codex_timeout_seconds=int(os.getenv("CODEX_TIMEOUT_SECONDS", "120")),
        codex_model=os.getenv("CODEX_MODEL", ""),
        codex_reasoning_level=os.getenv("CODEX_REASONING_LEVEL", ""),
        codex_sandbox=os.getenv("CODEX_SANDBOX", "read-only"),
        codex_bypass_approvals_and_sandbox=env_bool("CODEX_BYPASS_APPROVALS_AND_SANDBOX", False),
        codex_preflight_check=env_bool("CODEX_PREFLIGHT_CHECK", False),
        assistant_language=os.getenv("ASSISTANT_LANGUAGE", "es"),
        tts_engine=os.getenv("TTS_ENGINE", "kokoro").lower(),
        voice_profile=os.getenv("VOICE_PROFILE", "deep_male_latam_ai"),
        voice_style=os.getenv("VOICE_STYLE", os.getenv("VOICE_PROFILE", "deep_male_latam_ai")),
        kokoro_lang=os.getenv("KOKORO_LANG", "e"),
        kokoro_voice=os.getenv("KOKORO_VOICE", "em_alex"),
        kokoro_speed=float(os.getenv("KOKORO_SPEED", "0.82")),
        kokoro_python=os.getenv("KOKORO_PYTHON", ""),
        espeak_voice=os.getenv("ESPEAK_VOICE", "es-la+m3"),
        piper_model=os.getenv("PIPER_MODEL", ""),
        piper_voice_config=os.getenv("PIPER_CONFIG", os.getenv("PIPER_VOICE_CONFIG", "")),
        piper_length_scale=float(os.getenv("PIPER_LENGTH_SCALE", "1.25")),
        piper_sentence_silence=float(os.getenv("PIPER_SENTENCE_SILENCE", "0.45")),
        tts_speed=float(os.getenv("TTS_SPEED", "0.82")),
        tts_volume=float(os.getenv("TTS_VOLUME", "1.0")),
        voice_effects=env_bool("VOICE_EFFECTS", True),
        voice_pitch=int(os.getenv("VOICE_PITCH", "-360")),
        voice_tempo=float(os.getenv("VOICE_TEMPO", "0.90")),
        voice_bass=float(os.getenv("VOICE_BASS", "4.5")),
        voice_compress=env_bool("VOICE_COMPRESS", True),
        voice_reverb=os.getenv("VOICE_REVERB", "none").lower(),
        require_confirmation_for_sudo=env_bool("REQUIRE_CONFIRMATION_FOR_SUDO", True),
        disable_safety_confirmations=env_bool("DISABLE_SAFETY_CONFIRMATIONS", False),
        whisper_model=os.getenv("WHISPER_MODEL", "small"),
        screen_path=Path(os.getenv("SCREENSHOT_PATH", "/tmp/voice-assistant-screen.png")),
        log_dir=Path(os.getenv("ASSISTANT_LOG_DIR", str(Path.home() / ".local/share/voice-codex-assistant/logs"))),
        trigger_socket_path=Path(
            os.getenv(
                "TRIGGER_SOCKET_PATH",
                str(Path(os.getenv("XDG_RUNTIME_DIR", "/tmp")) / "voice-codex-assistant.sock"),
            )
        ),
        daemon_poll_seconds=float(os.getenv("DAEMON_POLL_SECONDS", "1.0")),
        handy_autostart=env_bool("HANDY_AUTOSTART", True),
        handy_process_history=env_bool("HANDY_PROCESS_HISTORY", True),
        handy_poll_seconds=float(os.getenv("HANDY_POLL_SECONDS", "2.0")),
        handy_appimage_path=os.getenv("HANDY_APPIMAGE_PATH", str(Path.home() / "Downloads/Handy_0.8.3_amd64.AppImage")),
        handy_history_db=Path(os.getenv("HANDY_HISTORY_DB", str(Path.home() / ".local/share/com.pais.handy/history.db"))),
        handy_trigger_command=os.getenv("HANDY_TRIGGER_COMMAND", ""),
        direct_stt_fallback_enabled=env_bool("DIRECT_STT_FALLBACK_ENABLED", False),
        session_type=os.getenv("XDG_SESSION_TYPE", ""),
        desktop=os.getenv("XDG_CURRENT_DESKTOP", ""),
    )
