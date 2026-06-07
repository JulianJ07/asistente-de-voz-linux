from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from . import safety
from . import reasoning
from . import tts
from .config import Config


@dataclass(frozen=True)
class ActionResult:
    intent: str
    action_executed: str
    response: str


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    without_accents = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(without_accents.split())


def run_shell(command: str, timeout: int = 60) -> tuple[int, str]:
    result = subprocess.run(["bash", "-lc", command], capture_output=True, text=True, timeout=timeout)
    output = (result.stdout + result.stderr).strip()
    if len(output) > 900:
        output = output[:900] + "\n..."
    return result.returncode, output


def launch(command: str) -> None:
    subprocess.Popen(["bash", "-lc", command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def execute_guarded(command: str, mode: str, cfg: Config, description: str) -> ActionResult:
    decision = safety.assess_command(command, cfg)

    if decision.uses_sudo and not safety.sudo_nopasswd_available():
        return ActionResult(
            intent="accion de sistema con sudo",
            action_executed=f"bloqueada: {command}",
            response=f"{safety.sudo_explanation()} Comando solicitado: {command}",
        )

    if decision.needs_confirmation:
        safety.save_pending(command, mode, description, decision.reason)
        return ActionResult(
            intent="confirmacion requerida",
            action_executed=f"pendiente: {command}",
            response=(
                f"Necesito confirmacion explicita antes de ejecutar esto: {command}. "
                "Si estas seguro, presiona Alt+Z y di: confirma ejecutar."
            ),
        )

    if mode == "launch":
        launch(command)
        return ActionResult("accion de sistema", command, f"Listo. Ejecute: {description}.")

    code, output = run_shell(command)
    if code == 0:
        detail = f" Resultado: {output}" if output else ""
        return ActionResult("comando de terminal", command, f"Listo. El comando termino correctamente.{detail}")
    return ActionResult("comando de terminal", command, f"El comando termino con codigo {code}. {output}")


def execute_pending_confirmation(text: str, cfg: Config) -> ActionResult | None:
    if safety.is_cancel(text):
        pending = safety.load_pending()
        safety.clear_pending()
        if pending:
            return ActionResult("confirmacion cancelada", "ninguna", "Cancelado. No ejecute la accion pendiente.")
        return None

    if not safety.is_confirmation(text):
        return None

    pending = safety.load_pending()
    if not pending:
        return ActionResult("confirmacion", "ninguna", "No hay ninguna accion pendiente para confirmar.")

    command = str(pending["command"])
    mode = str(pending.get("mode", "run"))
    description = str(pending.get("description", command))
    safety.clear_pending()

    if safety.uses_sudo(command) and not safety.sudo_nopasswd_available():
        return ActionResult("accion de sistema con sudo", f"bloqueada: {command}", safety.sudo_explanation())

    if mode == "launch":
        launch(command)
        return ActionResult("accion confirmada", command, f"Confirmado. Ejecute: {description}.")

    code, output = run_shell(command)
    if code == 0:
        detail = f" Resultado: {output}" if output else ""
        return ActionResult("accion confirmada", command, f"Confirmado. El comando termino correctamente.{detail}")
    return ActionResult("accion confirmada", command, f"El comando confirmado fallo con codigo {code}. {output}")


def is_screen_request(text: str) -> bool:
    normalized = normalize(text)
    phrases = [
        "mira mi pantalla",
        "explicame lo que aparece en pantalla",
        "explica lo que aparece en pantalla",
        "explicame mi pantalla",
        "explica mi pantalla",
        "mira la pantalla",
        "mira pantalla",
        "revisa mi pantalla",
        "revisa la pantalla",
        "analiza la pantalla",
        "analiza mi pantalla",
        "captura mi pantalla",
        "captura la pantalla",
        "toma captura",
        "toma una captura",
        "haz una captura",
        "haz captura",
        "que ves en pantalla",
        "que ves en mi pantalla",
        "que aparece en pantalla",
        "que aparece en mi pantalla",
        "que hay en pantalla",
        "que hay en mi pantalla",
        "resuelve lo que ves en pantalla",
        "resuelve lo que ves en mi pantalla",
        "que error aparece en pantalla",
        "que error ves en pantalla",
    ]
    return any(phrase in normalized for phrase in phrases)


def playerctl_command(args: str, cfg: Config) -> ActionResult:
    if not shutil.which("playerctl"):
        return ActionResult("control multimedia", "ninguna", "Falta playerctl. Instala con: sudo pacman -S playerctl")
    return execute_guarded(f"playerctl {args}", "run", cfg, f"playerctl {args}")


def spotify_command_from_system(cfg: Config) -> str | None:
    flatpak = shutil.which("flatpak")
    if flatpak:
        result = subprocess.run([flatpak, "info", "com.spotify.Client"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            return "flatpak run com.spotify.Client"
    if shutil.which("spotify"):
        return "spotify"
    return cfg.commands.get("spotify")


def handle_spotify(text: str, cfg: Config) -> ActionResult | None:
    normalized = normalize(text)
    if "spotify" not in normalized and not any(word in normalized for word in ["musica", "cancion", "reproduce", "pausa"]):
        return None

    if any(word in normalized for word in ["siguiente", "proxima", "proxima cancion"]):
        return playerctl_command("next", cfg)
    if any(word in normalized for word in ["anterior", "cancion anterior"]):
        return playerctl_command("previous", cfg)
    if any(word in normalized for word in ["pausa", "pausar", "play pause", "reanuda", "continua"]):
        return playerctl_command("play-pause", cfg)

    spotify_cmd = spotify_command_from_system(cfg)
    match = re.search(r"(reproduce|pon|busca|buscar)\s+(.+)$", text, flags=re.IGNORECASE)
    if match and ("spotify" in normalized or match.group(1).lower() in {"reproduce", "pon"}):
        query = re.sub(r"\s+en\s+spotify[.!?]*$", "", match.group(2).strip(), flags=re.IGNORECASE).strip()
        if query:
            uri = "spotify:search:" + urllib.parse.quote(query)
            if spotify_cmd:
                launch(spotify_cmd)
                time.sleep(1.0)
            return execute_guarded(f"xdg-open {shlex.quote(uri)}", "launch", cfg, f"buscar {query} en Spotify")

    if "spotify" in normalized and any(word in normalized for word in ["abre", "abrir", "inicia", "lanza"]):
        if not spotify_cmd:
            return ActionResult("abrir spotify", "ninguna", "No encontre Spotify. Instala la app o configura spotify en commands.yaml.")
        return execute_guarded(spotify_cmd, "launch", cfg, "abrir Spotify")

    return None


def handle_volume(text: str, cfg: Config) -> ActionResult | None:
    normalized = normalize(text)
    if "volumen" not in normalized and not any(word in normalized for word in ["silencia", "mute"]):
        return None

    if not shutil.which("pactl"):
        return ActionResult("control de volumen", "ninguna", "Falta pactl. En CachyOS suele venir con pipewire-pulse o pulseaudio.")

    match = re.search(r"volumen\s+(al|a)\s+(\d{1,3})", normalized)
    if match:
        percent = max(0, min(150, int(match.group(2))))
        return execute_guarded(f"pactl set-sink-volume @DEFAULT_SINK@ {percent}%", "run", cfg, f"poner volumen al {percent}%")

    if any(phrase in normalized for phrase in ["sube volumen", "subir volumen", "aumenta volumen"]):
        return execute_guarded("pactl set-sink-volume @DEFAULT_SINK@ +5%", "run", cfg, "subir volumen")
    if any(phrase in normalized for phrase in ["baja volumen", "bajar volumen", "reduce volumen"]):
        return execute_guarded("pactl set-sink-volume @DEFAULT_SINK@ -5%", "run", cfg, "bajar volumen")
    if any(word in normalized for word in ["silencia", "mute"]):
        return execute_guarded("pactl set-sink-mute @DEFAULT_SINK@ toggle", "run", cfg, "alternar silencio")

    return None


def handle_fast_reply(text: str, cfg: Config) -> ActionResult | None:
    normalized = normalize(text).strip(" .!?¿¡")
    if normalized in {"hola", "buenas", "hey", "hola jarvis", "jarvis", "oye jarvis"}:
        return ActionResult("respuesta local", "ninguna", "En linea.")
    if normalized in {"estas ahi", "sigues ahi", "me escuchas", "estas escuchando"}:
        return ActionResult("respuesta local", "ninguna", "En linea. Te escucho.")
    if normalized in {"como estas", "como vas", "estado"}:
        return ActionResult("respuesta local", "ninguna", "Operativo.")
    if normalized in {"gracias", "muchas gracias"}:
        return ActionResult("respuesta local", "ninguna", "Hecho.")
    if normalized in {"quien eres", "como te llamas"}:
        return ActionResult("respuesta local", "ninguna", "Soy J.A.R.V.I.S., tu asistente local de voz para Linux.")
    if normalized in {"que puedes hacer", "que sabes hacer"}:
        return ActionResult("respuesta local", "ninguna", "Puedo abrir apps, controlar audio, revisar pantalla, ejecutar comandos y responder consultas.")
    return None


def handle_voice_settings(text: str, cfg: Config) -> ActionResult | None:
    normalized = normalize(text)
    current = tts.load_voice_settings(cfg)

    if any(
        phrase in normalized
        for phrase in [
            "activa voz grave",
            "activa la voz grave",
            "activa voz varonil tecnologica",
            "activa la voz varonil tecnologica",
            "voz varonil tecnologica",
            "activa voz estilo asistente ia",
            "voz estilo asistente ia",
        ]
    ):
        settings = tts.activate_deep_male_profile(cfg)
        return ActionResult(
            "configurar voz",
            "tts_deep_male_latam_ai",
            f"Listo. Active el perfil {settings.profile}. Voz grave y tecnologica.",
        )

    if any(phrase in normalized for phrase in ["habla mas rapido", "habla rapido", "mas rapido"]):
        settings = tts.set_voice(
            cfg,
            speed=current.speed + 0.08,
            kokoro_speed=current.kokoro_speed + 0.04,
            tempo=current.tempo + 0.04,
            piper_length_scale=current.piper_length_scale - 0.08,
            piper_sentence_silence=current.piper_sentence_silence - 0.05,
        )
        return ActionResult("configurar voz", "tts_speed", f"Listo. Ahora hablare mas rapido. Velocidad: {settings.speed:.2f}.")

    if any(phrase in normalized for phrase in ["habla mas lento", "habla lento", "mas lento"]):
        settings = tts.set_voice(
            cfg,
            speed=current.speed - 0.08,
            kokoro_speed=current.kokoro_speed - 0.04,
            tempo=current.tempo - 0.04,
            piper_length_scale=current.piper_length_scale + 0.08,
            piper_sentence_silence=current.piper_sentence_silence + 0.05,
        )
        return ActionResult("configurar voz", "tts_speed", f"Listo. Ahora hablare mas lento. Velocidad: {settings.speed:.2f}.")

    if any(phrase in normalized for phrase in ["habla menos grave", "menos grave", "voz menos grave"]):
        settings = tts.set_voice(cfg, pitch=current.pitch + 80, bass=current.bass - 1.0, piper_length_scale=current.piper_length_scale - 0.04)
        return ActionResult("configurar voz", "tts_pitch", f"Listo. Reduje la gravedad. Pitch: {settings.pitch}.")

    if any(phrase in normalized for phrase in ["sube tu volumen", "habla mas fuerte", "sube la voz"]):
        settings = tts.set_voice(cfg, volume=current.volume + 0.15)
        return ActionResult("configurar voz", "tts_volume", f"Listo. Aumente mi volumen. Nivel: {settings.volume:.2f}.")

    if any(phrase in normalized for phrase in ["baja tu volumen", "habla mas bajo", "baja la voz"]):
        settings = tts.set_voice(cfg, volume=current.volume - 0.15)
        return ActionResult("configurar voz", "tts_volume", f"Listo. Baje mi volumen. Nivel: {settings.volume:.2f}.")

    if any(phrase in normalized for phrase in ["cambia a voz masculina", "voz masculina"]):
        model = tts.find_piper_voice("davefx") or tts.find_piper_voice("claude") or current.piper_model
        config_path = f"{model}.json" if model else current.piper_voice_config
        settings = tts.set_voice(
            cfg,
            engine="kokoro",
            gender="masculina",
            kokoro_voice="em_alex",
            profile=tts.DEEP_MALE_PROFILE,
            kokoro_speed=0.88,
            piper_model=model,
            piper_voice_config=config_path,
            piper_length_scale=1.16,
            piper_sentence_silence=0.38,
            speed=0.9,
            effects=True,
            pitch=-360,
            tempo=0.9,
            bass=4.5,
            compress=True,
            reverb="none",
        )
        return ActionResult("configurar voz", "tts_gender", f"Listo. Active el perfil de voz masculina con motor {settings.engine}.")

    if any(phrase in normalized for phrase in ["cambia a voz femenina", "voz femenina"]):
        settings = tts.set_voice(cfg, gender="femenina")
        return ActionResult("configurar voz", "tts_gender", f"Listo. Active el perfil de voz femenina con motor {settings.engine}.")

    if any(phrase in normalized for phrase in ["voz grave", "cambia a voz grave", "habla mas grave"]):
        model = tts.find_piper_voice("davefx") or tts.find_piper_voice("claude") or current.piper_model
        config_path = f"{model}.json" if model else current.piper_voice_config
        settings = tts.set_voice(
            cfg,
            engine="kokoro",
            profile=tts.DEEP_MALE_PROFILE,
            gender="masculina",
            kokoro_voice="em_alex",
            kokoro_speed=current.kokoro_speed - 0.04,
            piper_model=model,
            piper_voice_config=config_path,
            piper_length_scale=current.piper_length_scale + 0.08,
            piper_sentence_silence=current.piper_sentence_silence + 0.03,
            speed=current.speed - 0.06,
            effects=True,
            pitch=current.pitch - 80,
            tempo=current.tempo - 0.04,
            bass=current.bass + 1.0,
            compress=True,
            reverb="none",
        )
        return ActionResult("configurar voz", "tts_deep", f"Listo. Active una voz mas grave. Pitch: {settings.pitch}.")

    if any(phrase in normalized for phrase in ["voz mexicana", "voz latina", "voz mas fluida", "voz natural"]):
        model = tts.find_piper_voice("claude") or current.piper_model
        config_path = f"{model}.json" if model else current.piper_voice_config
        settings = tts.set_voice(cfg, engine="kokoro", gender="masculina", kokoro_voice="em_alex", kokoro_speed=0.96, piper_model=model, piper_voice_config=config_path, speed=1.02)
        return ActionResult("configurar voz", "tts_latam", f"Listo. Active la voz mexicana mas fluida. Velocidad: {settings.speed:.2f}.")

    if any(phrase in normalized for phrase in ["quita el efecto robotico", "quita efecto robotico", "sin efecto robotico", "desactiva efectos de voz", "quita los efectos de voz"]):
        settings = tts.set_voice(cfg, effects=False, compress=False, reverb="off", pitch=0, tempo=1.0, bass=0)
        return ActionResult("configurar voz", "tts_effects_off", f"Listo. Quite el efecto tecnologico. Motor: {settings.engine}.")

    if any(phrase in normalized for phrase in ["vuelve a la voz normal", "voz normal", "vuelve a voz normal"]):
        settings = tts.set_voice(
            cfg,
            profile="normal",
            speed=1.0,
            kokoro_speed=0.95,
            piper_length_scale=1.05,
            piper_sentence_silence=0.25,
            effects=False,
            pitch=0,
            tempo=1.0,
            bass=0,
            compress=False,
            reverb="off",
        )
        return ActionResult("configurar voz", "tts_normal", "Listo. Volvi a una voz normal sin efectos.")

    if any(phrase in normalized for phrase in ["activa efecto robotico", "activa efectos de voz"]):
        settings = tts.set_voice(
            cfg,
            engine="kokoro",
            profile=tts.DEEP_MALE_PROFILE,
            gender="masculina",
            kokoro_voice="em_alex",
            kokoro_speed=0.88,
            piper_length_scale=1.16,
            piper_sentence_silence=0.38,
            speed=0.9,
            effects=True,
            pitch=-360,
            tempo=0.9,
            bass=4.5,
            compress=True,
            reverb="none",
        )
        return ActionResult("configurar voz", "tts_ai_style", f"Listo. Active la voz estilo asistente IA. Pitch: {settings.pitch}.")

    if any(phrase in normalized for phrase in ["lista voces", "voces disponibles", "que voces tienes"]):
        voices = tts.list_available_voices(cfg)
        summary = []
        for engine, items in voices.items():
            summary.append(f"{engine}: {len(items)}")
        return ActionResult("listar voces", "tts_list_voices", "Voces disponibles. " + ", ".join(summary) + ".")

    return None


def handle_reasoning_mode(text: str, cfg: Config) -> ActionResult | None:
    normalized = normalize(text)
    if "modo" not in normalized and "razonamiento" not in normalized and "pensamiento" not in normalized:
        return None
    if not any(word in normalized for word in ["codex", "razonamiento", "pensamiento", "modo", "actualizate", "sube"]):
        return None

    level: str | None = None
    if any(phrase in normalized for phrase in ["extremadamente alto", "modo extremo", "razonamiento extremo"]):
        level = "high"
    elif any(phrase in normalized for phrase in ["modo alto", "razonamiento alto", "pensamiento alto"]):
        level = "high"
    elif any(phrase in normalized for phrase in ["modo medio", "razonamiento medio", "pensamiento medio"]):
        level = "medium"
    elif any(phrase in normalized for phrase in ["modo bajo", "razonamiento bajo", "pensamiento bajo"]):
        level = "low"

    if not level:
        return None

    saved = reasoning.save_next_override(level)
    return ActionResult(
        "modo de razonamiento temporal",
        f"siguiente respuesta: {saved}",
        f"Listo. Usare razonamiento {reasoning.display_level(saved)} solo en la siguiente respuesta de Codex.",
    )


def handle_browser(text: str, cfg: Config) -> ActionResult | None:
    normalized = normalize(text)
    if any(phrase in normalized for phrase in ["abre navegador", "abrir navegador", "abre internet"]):
        return execute_guarded("xdg-open https://www.google.com", "launch", cfg, "abrir navegador")

    # Always use xdg-open so the search opens in the user's default browser.
    patterns = [
        r"\b(?:busca|buscar)\s+en\s+(?:internet|brave|google|navegador)\s+(.+?)[.!?]*$",
        r"\bgooglea\s+(.+?)[.!?]*$",
        r"\b(?:busca|buscar)\s+(.+?)\s+en\s+(?:internet|brave|google|navegador)[.!?]*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            query = match.group(1).strip(" .!?¿¡")
            url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
            return execute_guarded(f"xdg-open {shlex.quote(url)}", "launch", cfg, f"buscar {query} en el navegador predeterminado")
    return None


def folder_path(name: str) -> Path | None:
    home = Path.home()
    options = {
        "descargas": [home / "Downloads", home / "Descargas"],
        "downloads": [home / "Downloads", home / "Descargas"],
        "documentos": [home / "Documentos", home / "Documents"],
        "escritorio": [home / "Escritorio", home / "Desktop"],
        "imagenes": [home / "Imágenes", home / "Imagenes", home / "Pictures"],
        "musica": [home / "Música", home / "Musica", home / "Music"],
        "videos": [home / "Vídeos", home / "Videos"],
        "proyectos": [home / "Proyectos", home / "Projects"],
        "home": [home],
    }
    for path in options.get(name, []):
        if path.exists():
            return path
    return None


def handle_folder(text: str, cfg: Config) -> ActionResult | None:
    normalized = normalize(text)
    if not any(word in normalized for word in ["abre", "abrir", "carpeta"]):
        return None
    for name in ["descargas", "downloads", "documentos", "escritorio", "imagenes", "musica", "videos", "proyectos", "home"]:
        if name in normalized:
            path = folder_path(name)
            if not path:
                return ActionResult("abrir carpeta", "ninguna", f"No encontre la carpeta {name}.")
            return execute_guarded(f"xdg-open {shlex.quote(str(path))}", "launch", cfg, f"abrir carpeta {name}")
    return None


def handle_terminal_command(text: str, cfg: Config) -> ActionResult | None:
    patterns = [
        r"^(ejecuta|corre|lanza)\s+(el\s+)?comando\s+(.+)$",
        r"^(ejecutar|correr)\s+(el\s+)?comando\s+(.+)$",
        r"^terminal\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text.strip(), flags=re.IGNORECASE)
        if match:
            command = match.group(match.lastindex).strip()
            return execute_guarded(command, "run", cfg, f"ejecutar comando {command}")
    return None


def handle_install_package(text: str, cfg: Config) -> ActionResult | None:
    normalized = normalize(text)
    match = re.search(r"\binstal(?:a|ar|e)\s+(?:el\s+paquete\s+|los\s+paquetes\s+|paquetes\s+)?(.+)$", normalized)
    if not match:
        return None

    raw_packages = match.group(1)
    packages = re.findall(r"[a-zA-Z0-9@._+-]+", raw_packages)
    stop_words = {"por", "favor", "con", "pacman"}
    packages = [package for package in packages if package not in stop_words]
    if not packages:
        return ActionResult("instalar paquete", "ninguna", "No identifique el nombre del paquete a instalar.")

    command = "sudo pacman -S --needed " + " ".join(shlex.quote(package) for package in packages)
    return execute_guarded(command, "run", cfg, "instalar " + ", ".join(packages))


def handle_close_app(text: str, cfg: Config) -> ActionResult | None:
    normalized = normalize(text)
    match = re.search(r"\b(cierra|cerrar|termina)\s+(.+)$", normalized)
    if not match:
        return None

    target = match.group(2).strip()
    if target in {"aplicaciones", "todo", "todas"}:
        return ActionResult("cerrar aplicacion", "ninguna", "Necesito que me digas que aplicacion especifica quieres cerrar.")

    aliases = {
        "navegador": "chrome|chromium|firefox|brave",
        "chrome": "chrome",
        "chromium": "chromium",
        "spotify": "spotify",
        "steam": "steam",
        "heroic": "heroic",
    }
    process = aliases.get(target, target)
    command = f"pkill -f {shlex.quote(process)}"
    return execute_guarded(command, "run", cfg, f"cerrar {target}")


def handle_configured_app(text: str, cfg: Config) -> ActionResult | None:
    normalized = normalize(text)
    open_words = ["abre", "abrir", "lanza", "inicia", "ejecuta", "juega"]
    if not any(word in normalized for word in open_words):
        return None

    for key, command in sorted(cfg.commands.items(), key=lambda item: len(item[0]), reverse=True):
        normalized_key = normalize(key)
        if any(f"{word} {normalized_key}" in normalized for word in open_words):
            return execute_guarded(command, "launch", cfg, f"abrir {key}")
    return None


def handle_local_action(text: str, cfg: Config) -> ActionResult | None:
    for handler in [
        execute_pending_confirmation,
        handle_fast_reply,
        handle_terminal_command,
        handle_install_package,
        handle_reasoning_mode,
        handle_voice_settings,
        handle_spotify,
        handle_volume,
        handle_browser,
        handle_folder,
        handle_close_app,
        handle_configured_app,
    ]:
        result = handler(text, cfg)
        if result:
            return result
    return None


def check_playerctl() -> tuple[bool, str]:
    path = shutil.which("playerctl")
    return (True, f"OK ({path})") if path else (False, "FALTA. Instala con: sudo pacman -S playerctl")


def check_spotify(cfg: Config) -> tuple[bool, str]:
    command = spotify_command_from_system(cfg)
    if command:
        return True, f"OK ({command})"
    return False, "No encontre spotify ni flatpak com.spotify.Client; puedes configurarlo en commands.yaml"


def check_sudo() -> tuple[bool, str]:
    if safety.sudo_nopasswd_available():
        return True, "OK (sudo -n true funciona)"
    return False, "NO configurado. Los comandos sudo se bloquearan hasta configurar NOPASSWD o ejecutarlos manualmente."
