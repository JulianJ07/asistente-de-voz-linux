#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from assistant import actions, codex_cli, config, daemon, handy, llm, screen, tts
from assistant.config import AssistantError


def llm_error_answer(exc: AssistantError) -> str:
    text = str(exc).lower()
    if "usage limit" in text or "limite" in text or "límite" in text:
        return "Codex esta temporalmente sin creditos. Recibi tu mensaje, pero no puedo generar respuesta inteligente ahora."
    if "login" in text or "sesion" in text or "sesión" in text:
        return "Codex necesita iniciar sesion. Ejecuta codex login para restaurar mis respuestas inteligentes."
    return "Recibi tu mensaje, pero Codex fallo al responder. Revisa los logs del asistente."


def handle_transcript(transcript: str, cfg: config.Config) -> dict[str, str]:
    transcript = transcript.strip()
    if not transcript:
        raise AssistantError("El texto recibido esta vacio.")

    print(f"Transcripción: {transcript}", flush=True)
    action_result = actions.handle_local_action(transcript, cfg)

    if action_result:
        intent = action_result.intent
        executed = action_result.action_executed
        answer = action_result.response
    elif actions.is_screen_request(transcript):
        intent = "pregunta sobre pantalla"
        screenshot_path = screen.capture_screen(cfg)
        executed = f"captura de pantalla: {screenshot_path}"
        try:
            answer = llm.ask_vision(transcript, screenshot_path, cfg)
        except AssistantError as exc:
            answer = llm_error_answer(exc)
    else:
        intent = "pregunta normal"
        executed = "ninguna"
        try:
            answer = llm.ask_text(transcript, cfg)
        except AssistantError as exc:
            answer = llm_error_answer(exc)

    print(f"Intención detectada: {intent}", flush=True)
    print(f"Acción ejecutada: {executed}", flush=True)
    print(f"Respuesta: {answer}", flush=True)
    return {"transcript": transcript, "intent": intent, "executed": executed, "answer": answer}


def process_text(text: str, cfg: config.Config, speak: bool = True) -> dict[str, str]:
    tts.stop_current_speech(cfg)
    result = handle_transcript(text, cfg)
    if speak:
        tts.speak(result["answer"], cfg)
    return result


def run_voice_fallback(cfg: config.Config) -> None:
    if not cfg.direct_stt_fallback_enabled:
        raise AssistantError("El fallback STT directo esta desactivado. Usa Handy o define DIRECT_STT_FALLBACK_ENABLED=true.")

    from assistant import audio, stt

    with tempfile.TemporaryDirectory(prefix="voice-codex-") as tmpdir:
        audio_path = Path(tmpdir) / "input.wav"
        print("Escuchando con fallback Python...", flush=True)
        audio.record_until_silence(audio_path, cfg)
        transcript = stt.transcribe_audio(audio_path, cfg)
        process_text(transcript, cfg, speak=True)


def run_test(cfg: config.Config) -> int:
    checks: list[tuple[str, str, bool]] = []

    checks.append(("Python", sys.version.split()[0], True))
    checks.append(("Sesion grafica", f"XDG_SESSION_TYPE={cfg.session_type}, desktop={cfg.desktop}", True))
    checks.append(("Backend LLM", cfg.llm_backend, cfg.llm_backend == "codex_cli"))
    checks.append(("commands.yaml", "OK" if cfg.commands_file.exists() else "FALTA", cfg.commands_file.exists()))

    for module_name, label in [("yaml", "PyYAML")]:
        try:
            __import__(module_name)
            checks.append((label, "OK", True))
        except Exception as exc:
            checks.append((label, f"FALTA ({exc})", False))

    codex_ok, codex_msg = codex_cli.check(cfg)
    checks.append(("Codex CLI", codex_msg, codex_ok))

    handy_status = handy.status(cfg)
    checks.append(("Handy", handy_status.message, handy_status.found))
    checks.append(("Handy history", str(cfg.handy_history_db), cfg.handy_history_db.exists()))

    tts_ok, tts_msg = tts.check_tts(cfg)
    checks.append(("TTS", tts_msg, tts_ok))

    screen_ok, screen_msg = screen.check_screen_capture(cfg)
    checks.append(("Captura de pantalla", screen_msg, screen_ok))

    player_ok, player_msg = actions.check_playerctl()
    checks.append(("playerctl", player_msg, player_ok))

    spotify_ok, spotify_msg = actions.check_spotify(cfg)
    checks.append(("Spotify", spotify_msg, spotify_ok))

    sudo_ok, sudo_msg = actions.check_sudo()
    checks.append(("sudo NOPASSWD", sudo_msg, sudo_ok))

    if cfg.direct_stt_fallback_enabled:
        from assistant import audio

        mic_ok, mic_msg = audio.check_microphone()
        checks.append(("Microfono fallback", mic_msg, mic_ok))

    print("Prueba de entorno")
    ok = True
    for name, message, passed in checks:
        ok = ok and passed
        print(f"{name}: {message}")

    return 0 if ok else 1


def run_voice_test(cfg: config.Config) -> int:
    samples = tts.play_voice_test(cfg)
    print("Muestras de voz generadas:")
    for name, path in samples.items():
        print(f"{name}: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Asistente de voz para Linux/Wayland usando Handy + Codex CLI.")
    parser.add_argument("--test", action="store_true", help="revisa Handy, TTS, Codex CLI, pantalla, Spotify, sudo y commands.yaml")
    parser.add_argument("--daemon", action="store_true", help="modo residente: espera texto de Handy o eventos por socket")
    parser.add_argument("--trigger", action="store_true", help="avisa al daemon para disparar Handy")
    parser.add_argument("--stop-tts", action="store_true", help="detiene la voz actual del asistente")
    parser.add_argument("--voice-test", action="store_true", help="genera muestras y prueba la voz grave tecnologica")
    parser.add_argument("--text", metavar="TEXTO", help="procesa texto ya transcrito por Handy")
    parser.add_argument("--no-speak", action="store_true", help="no leer la respuesta en voz alta")
    parser.add_argument("--voice-fallback", action="store_true", help="usar grabacion/transcripcion Python como fallback explicito")
    args = parser.parse_args()

    try:
        cfg = config.load_config()
        if args.test:
            return run_test(cfg)
        if args.voice_test:
            return run_voice_test(cfg)
        if args.daemon:
            daemon.run_daemon(cfg)
            return 0
        if args.stop_tts:
            stopped = tts.stop_current_speech(cfg)
            print(f"Voz detenida. Procesos interrumpidos: {stopped}")
            return 0
        if args.trigger:
            tts.stop_current_speech(cfg)
            daemon.send_event(cfg, "trigger")
            return 0
        if args.text is not None:
            process_text(args.text, cfg, speak=not args.no_speak)
            return 0
        if args.voice_fallback:
            run_voice_fallback(cfg)
            return 0

        parser.print_help()
        print("\nFlujo principal: Handy -> python main.py --text \"texto\" o daemon observando historial de Handy.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelado.", file=sys.stderr)
        return 130
    except AssistantError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
