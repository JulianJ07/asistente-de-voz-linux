# Voice Codex Assistant

Asistente de voz para Linux/CachyOS en KDE/Wayland usando **Handy como entrada de voz** y **Codex CLI local** como cerebro.

Flujo principal:

```text
Handy
↓
transcribe voz
↓
voice-codex-assistant recibe texto
↓
acciones locales o Codex CLI
↓
respuesta por TTS estilo Jarvis
```

Python ya no graba ni transcribe como flujo principal. `sounddevice` y `faster-whisper` quedan solo como fallback opcional.

## Uso Por Texto

Procesar texto ya transcrito:

```bash
cd ~/voice-codex-assistant
source .venv/bin/activate
python main.py --text "abre spotify"
```

Sin voz de salida:

```bash
python main.py --text "busca en internet cachyos docker" --no-speak
```

## Daemon

Modo residente:

```bash
python main.py --daemon
```

El daemon:

- arranca/vigila Handy si está configurado
- observa el historial SQLite de Handy
- procesa nuevas transcripciones
- escucha eventos por socket local
- escribe logs en `~/.local/share/voice-codex-assistant/logs/`
- no usa grabación directa de Python

Disparar el daemon:

```bash
python main.py --trigger
```

Enviar texto al daemon o procesarlo desde scripts:

```bash
~/.local/bin/voice-codex-assistant-text "abre spotify"
```

## Instalacion

Dependencias principales:

```bash
sudo pacman -S --needed python python-pip sox ffmpeg mpv espeak-ng playerctl spectacle wl-clipboard
```

Si `sox` no esta instalado, el asistente usa `ffmpeg` para los efectos de voz.

TTS fallback minimo:

```bash
sudo pacman -S --needed espeak-ng
```

Entorno Python:

```bash
cd ~/voice-codex-assistant
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Fallback STT opcional:

```bash
python -m pip install -r requirements-fallback-stt.txt
```

Codex CLI:

```bash
codex doctor
codex login
```

## Config

Crea `.env` si quieres ajustar valores:

```bash
cp .env.example .env
nano .env
```

Variables clave:

```bash
LLM_BACKEND=codex_cli
CODEX_COMMAND=codex
CODEX_TIMEOUT_SECONDS=120
CODEX_REASONING_LEVEL=low
CODEX_SANDBOX=read-only
CODEX_BYPASS_APPROVALS_AND_SANDBOX=false

HANDY_AUTOSTART=true
HANDY_PROCESS_HISTORY=true
HANDY_APPIMAGE_PATH=/home/julian/Downloads/Handy_0.8.3_amd64.AppImage
HANDY_HISTORY_DB=/home/julian/.local/share/com.pais.handy/history.db

TTS_ENGINE=kokoro
VOICE_PROFILE=deep_male_latam_ai
KOKORO_LANG=e
KOKORO_VOICE=em_alex
KOKORO_SPEED=0.82
KOKORO_PYTHON=
VOICE_STYLE=deep_male_latam_ai
VOICE_EFFECTS=true
VOICE_PITCH=-420
VOICE_TEMPO=0.86
VOICE_BASS=6
VOICE_COMPRESS=true
VOICE_REVERB=none
PIPER_MODEL=
PIPER_CONFIG=
PIPER_LENGTH_SCALE=1.25
PIPER_SENTENCE_SILENCE=0.45

DIRECT_STT_FALLBACK_ENABLED=false
```

`CODEX_REASONING_LEVEL=low` es el modo permanente recomendado para cuidar creditos y responder rapido. Tambien puedes usar `medium` si quieres un equilibrio mayor.

Para subir el razonamiento solo una vez, dile al asistente:

```text
actualizate a modo alto
actualizate a modo extremadamente alto
actualizate a modo medio
```

Ese cambio se consume en la siguiente respuesta real de Codex y despues vuelve automaticamente al valor de `.env`. En Codex CLI, el nivel maximo configurado por el asistente es `high`; "extremadamente alto" se trata como ese maximo disponible.

### Acceso completo sin confirmaciones

Para que Codex CLI pueda leer/escribir fuera del proyecto y ejecutar comandos sin pedir aprobacion, configura `.env` asi:

```bash
CODEX_SANDBOX=danger-full-access
CODEX_BYPASS_APPROVALS_AND_SANDBOX=true
REQUIRE_CONFIRMATION_FOR_SUDO=false
DISABLE_SAFETY_CONFIRMATIONS=true
```

Esto elimina las confirmaciones internas del asistente y lanza Codex CLI con acceso completo del usuario actual. Para que los comandos con `sudo` funcionen sin pedir contrasena, tambien debes habilitar NOPASSWD en sudoers:

```bash
sudo EDITOR=nano visudo -f /etc/sudoers.d/voice-codex-assistant
```

Contenido:

```text
julian ALL=(ALL) NOPASSWD: ALL
```

Despues verifica:

```bash
sudo -n true && echo "sudo NOPASSWD OK"
```

## Handy

El daemon detecta Handy como:

1. AppImage en `HANDY_APPIMAGE_PATH`
2. AppImage en `~/Downloads` o `~/Descargas`
3. Flatpak `com.pais.handy` o `io.github.cjpais.handy`
4. comando `handy`

Para que Handy alimente al asistente hay dos opciones.

Opción estable recomendada:

1. Configura Handy para iniciar con sesión o deja `HANDY_AUTOSTART=true`.
2. Configura en Handy el atajo global `Alt+Z` para transcribir.
3. Deja Handy guardando historial.
4. El daemon detecta nuevas filas en `history.db` y procesa el texto.

Opción por script externo, si Handy permite script externo:

```bash
~/.local/bin/voice-codex-assistant-text
```

Ese script acepta texto por argumentos o por stdin.

## Inicio Automático

Servicio systemd de usuario creado en:

```text
~/.config/systemd/user/voice-codex-assistant.service
```

Habilitar:

```bash
systemctl --user daemon-reload
systemctl --user enable voice-codex-assistant
systemctl --user start voice-codex-assistant
```

Estado:

```bash
systemctl --user status voice-codex-assistant
```

Logs en journal:

```bash
journalctl --user -u voice-codex-assistant -f
```

Logs propios:

```bash
tail -f ~/.local/share/voice-codex-assistant/logs/daemon.log
```

Reiniciar:

```bash
systemctl --user restart voice-codex-assistant
```

Deshabilitar:

```bash
systemctl --user disable --now voice-codex-assistant
```

## Alt+Z En KDE/Wayland

Para que `Alt+Z` interrumpa la voz del asistente antes de escucharte, configura el atajo global de KDE apuntando al disparador del asistente:

```bash
/home/julian/.local/bin/voice-codex-assistant-trigger
```

Ese comando hace dos cosas: detiene cualquier voz activa y luego intenta disparar Handy mediante `HANDY_TRIGGER_COMMAND` o `SIGUSR1`.

Si configuras `Alt+Z` directamente en Handy, Handy puede escucharte, pero el asistente no siempre puede cortar su propia voz al instante porque no recibe primero el evento de teclado.

Prueba manual para cortar la voz:

```bash
python main.py --stop-tts
```

## Test

```bash
cd ~/voice-codex-assistant
source .venv/bin/activate
python main.py --test
```

Revisa:

- Handy
- historial de Handy
- Codex CLI
- TTS
- captura de pantalla
- playerctl
- Spotify
- sudo NOPASSWD
- commands.yaml

## Configurar voz grave, varonil y tecnológica

La voz usa un perfil llamado `deep_male_latam_ai`. Esta inspirada en un asistente IA masculino moderno, sin clonar voces reales, actores, personajes ni voces protegidas.

Prioridad TTS:

1. Kokoro TTS
2. Piper TTS
3. espeak-ng como emergencia

El motor real se puede revisar con:

```bash
cd ~/voice-codex-assistant
source .venv/bin/activate
python main.py --test
```

En esta maquina, si Kokoro no esta disponible para el Python actual, el sistema cae a Piper y lo informa como `motor efectivo: Piper fallback`.

Perfil recomendado en `.env`:

```bash
TTS_ENGINE=kokoro
VOICE_PROFILE=deep_male_latam_ai
KOKORO_LANG=e
KOKORO_VOICE=em_alex
KOKORO_SPEED=0.82
VOICE_EFFECTS=true
VOICE_PITCH=-420
VOICE_TEMPO=0.86
VOICE_BASS=6
VOICE_COMPRESS=true
VOICE_REVERB=none

PIPER_MODEL=/home/julian/.local/share/piper/voices/es_ES/davefx/medium/es_ES-davefx-medium.onnx
PIPER_CONFIG=/home/julian/.local/share/piper/voices/es_ES/davefx/medium/es_ES-davefx-medium.onnx.json
PIPER_LENGTH_SCALE=1.25
PIPER_SENTENCE_SILENCE=0.45
```

Probar la voz y generar muestras:

```bash
cd ~/voice-codex-assistant
source .venv/bin/activate
python main.py --voice-test
```

Esto crea:

```text
voice_samples/original.wav
voice_samples/deep_male_latam_ai.wav
voice_samples/slow_human_rhythm.wav
```

Hacerla mas grave:

```bash
VOICE_PITCH=-500
VOICE_BASS=7
```

Si suena demasiado grave o distorsionada:

```bash
VOICE_PITCH=-300
VOICE_BASS=4
```

Hacerla mas lenta:

```bash
VOICE_TEMPO=0.82
KOKORO_SPEED=0.78
PIPER_LENGTH_SCALE=1.35
PIPER_SENTENCE_SILENCE=0.60
```

Quitar efectos:

```bash
VOICE_EFFECTS=false
VOICE_PITCH=0
VOICE_TEMPO=1.0
VOICE_BASS=0
VOICE_COMPRESS=false
VOICE_REVERB=none
```

Cambiar de motor TTS:

```bash
TTS_ENGINE=kokoro
TTS_ENGINE=piper
TTS_ENGINE=espeak
```

Cambiar de voz:

```bash
KOKORO_VOICE=em_alex
PIPER_MODEL=/ruta/a/voz.onnx
PIPER_CONFIG=/ruta/a/voz.onnx.json
```

Instalar Kokoro en un Python compatible:

```bash
cd ~/voice-codex-assistant
python3.12 -m venv .venv-kokoro
source .venv-kokoro/bin/activate
python -m pip install --upgrade pip
python -m pip install kokoro soundfile
```

Luego en `.env`:

```bash
KOKORO_PYTHON=/home/julian/voice-codex-assistant/.venv-kokoro/bin/python
```

Dependencias de audio:

```bash
sudo pacman -S --needed sox ffmpeg mpv espeak-ng
```

Si `sox` no esta instalado, se usa `ffmpeg` para bajar pitch, graves, compresion y normalizacion.

Comandos de voz:

- "Habla mas grave"
- "Habla menos grave"
- "Habla mas lento"
- "Habla mas rapido"
- "Activa voz grave"
- "Activa voz varonil tecnologica"
- "Quita los efectos de voz"
- "Vuelve a la voz normal"
- "Lista voces disponibles"

## Ejemplos

- "Abre Spotify"
- "Reproduce Soda Stereo en Spotify"
- "Pausa la música"
- "Siguiente canción"
- "Sube volumen"
- "Volumen al 45"
- "Busca en internet cómo instalar Docker en CachyOS"
- "Busca CachyOS codecs en el navegador"
- "Abre descargas"
- "Ejecuta el comando uname -a"
- "Instala el paquete htop"
- "Mira mi pantalla y dime que ves"
- "Analiza mi pantalla y dime que error aparece"
- "Cierra Spotify"

Las busquedas web usan `xdg-open`, por eso se abren en el navegador predeterminado del sistema. Para dejar Brave como predeterminado:

```bash
xdg-settings set default-web-browser brave-browser.desktop
xdg-mime default brave-browser.desktop x-scheme-handler/http
xdg-mime default brave-browser.desktop x-scheme-handler/https
xdg-mime default brave-browser.desktop text/html
```
