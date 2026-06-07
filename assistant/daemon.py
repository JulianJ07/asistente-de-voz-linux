from __future__ import annotations

import logging
import logging.handlers
import signal
import socket
import threading
import time

from . import actions, codex_cli, handy, screen, tts
from .config import AssistantError, Config


def setup_logging(cfg: Config) -> logging.Logger:
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("voice-codex-assistant")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        cfg.log_dir / "daemon.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def preflight(cfg: Config, logger: logging.Logger) -> None:
    checks: list[tuple[str, bool, str]] = []

    checks.append(("commands.yaml", cfg.commands_file.exists(), str(cfg.commands_file)))

    if cfg.codex_preflight_check:
        codex_ok, codex_msg = codex_cli.check(cfg)
        checks.append(("Codex CLI", codex_ok, codex_msg))
    else:
        checks.append(("Codex CLI", True, "omitido en daemon; usa python main.py --test para verificar"))

    player_ok, player_msg = actions.check_playerctl()
    checks.append(("playerctl", player_ok, player_msg))

    tts_ok, tts_msg = tts.check_tts(cfg)
    checks.append(("TTS", tts_ok, tts_msg))

    screen_ok, screen_msg = screen.check_screen_capture(cfg)
    checks.append(("captura", screen_ok, screen_msg))

    spotify_ok, spotify_msg = actions.check_spotify(cfg)
    checks.append(("Spotify", spotify_ok, spotify_msg))

    handy_status = handy.status(cfg)
    checks.append(("Handy", handy_status.found, handy_status.message))

    if cfg.direct_stt_fallback_enabled:
        from . import audio

        mic_ok, mic_msg = audio.check_microphone()
        checks.append(("microfono fallback", mic_ok, mic_msg))

    for name, ok, msg in checks:
        if ok:
            logger.info("Preflight %s: %s", name, msg)
        else:
            logger.error("Preflight %s: %s", name, msg)


def speak_async(text: str, cfg: Config, logger: logging.Logger) -> None:
    def _worker() -> None:
        try:
            tts.speak(text, cfg)
        except Exception as exc:
            logger.exception("Fallo TTS: %s", exc)

    thread = threading.Thread(target=_worker, name="assistant-tts", daemon=True)
    thread.start()


def process_text(text: str, cfg: Config, logger: logging.Logger, source: str) -> None:
    from main import handle_transcript

    logger.info("Texto recibido desde %s: %s", source, text)
    try:
        stopped = tts.stop_current_speech(cfg)
        if stopped:
            logger.info("Voz interrumpida antes de procesar nueva entrada (%s procesos).", stopped)
        result = handle_transcript(text, cfg)
        logger.info("Intencion detectada: %s", result["intent"])
        logger.info("Accion ejecutada: %s", result["executed"])
        logger.info("Respuesta: %s", result["answer"])
        speak_async(result["answer"], cfg, logger)
    except Exception as exc:
        logger.exception("Fallo procesando texto desde %s: %s", source, exc)


def run_voice_cycle(cfg: Config, logger: logging.Logger) -> None:
    logger.info("Disparo recibido. Entrada principal: Handy.")
    if handy.trigger_recording(cfg, logger):
        logger.info("Handy fue disparado; esperando nueva transcripcion en historial.")
    else:
        logger.error("No pude disparar Handy. Configura Alt+Z directamente en Handy o HANDY_TRIGGER_COMMAND.")


def _bind_socket(cfg: Config) -> socket.socket:
    cfg.trigger_socket_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.trigger_socket_path.unlink(missing_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(cfg.trigger_socket_path))
    sock.settimeout(cfg.daemon_poll_seconds)
    return sock


def send_event(cfg: Config, message: str) -> None:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.sendto(message.encode("utf-8"), str(cfg.trigger_socket_path))
    except FileNotFoundError as exc:
        raise AssistantError("El daemon no esta corriendo. Inicia: systemctl --user start voice-codex-assistant") from exc
    finally:
        sock.close()


def handle_event(raw: str, cfg: Config, logger: logging.Logger) -> None:
    if raw == "trigger":
        stopped = tts.stop_current_speech(cfg)
        if stopped:
            logger.info("Voz interrumpida por disparo Alt+Z (%s procesos).", stopped)
        run_voice_cycle(cfg, logger)
        return

    if raw.startswith("text:"):
        text = raw.removeprefix("text:").strip()
        if text:
            process_text(text, cfg, logger, "socket")
        return

    logger.warning("Evento desconocido: %s", raw)


def run_daemon(cfg: Config) -> None:
    logger = setup_logging(cfg)
    logger.info("Iniciando daemon voice-codex-assistant.")
    logger.info("Socket de disparo: %s", cfg.trigger_socket_path)
    logger.info("Logs: %s", cfg.log_dir)

    stop = False

    def _stop(_signum, _frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    preflight(cfg, logger)
    handy.ensure_running(cfg, logger)

    last_handy_check = 0.0
    last_handy_id = handy.latest_history_id(cfg)
    sock = _bind_socket(cfg)

    try:
        while not stop:
            try:
                data = sock.recv(65535)
                raw = data.decode("utf-8", errors="replace").strip()
                if raw:
                    handle_event(raw, cfg, logger)
            except socket.timeout:
                pass

            now = time.monotonic()
            if now - last_handy_check >= cfg.handy_poll_seconds:
                last_handy_check = now
                handy.ensure_running(cfg, logger)
                for entry_id, text in handy.new_history_entries(cfg, last_handy_id):
                    last_handy_id = max(last_handy_id, entry_id)
                    logger.info("Nueva transcripcion Handy #%s detectada.", entry_id)
                    if cfg.handy_process_history:
                        process_text(text, cfg, logger, f"handy#{entry_id}")
                    else:
                        logger.info("Handy texto ignorado porque HANDY_PROCESS_HISTORY=false: %s", text)
    finally:
        sock.close()
        cfg.trigger_socket_path.unlink(missing_ok=True)
        logger.info("Daemon detenido.")
