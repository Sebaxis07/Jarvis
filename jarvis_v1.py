import os
import re
import json
import time
import subprocess
import webbrowser
from datetime import datetime

import requests
import sounddevice as sd
from scipy.io.wavfile import write
import pyttsx3
from faster_whisper import WhisperModel

# Extras
import pyperclip
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL


sd.default.samplerate = 16000
sd.default.channels = 1

def pick_input_device():
    devs = sd.query_devices()
    # Elige el primer dispositivo con input_channels > 0
    for i, d in enumerate(devs):
        if d.get("max_input_channels", 0) > 0:
            return i
    return None

MIC_DEVICE = pick_input_device()
print("MIC_DEVICE:", MIC_DEVICE, sd.query_devices(MIC_DEVICE) if MIC_DEVICE is not None else "NONE")


# ======================
# CONFIG
# ======================
OLLAMA_URL = "http://localhost:11434/api/chat"

# Usa el modelo que creaste con num_ctx 4096:
MODEL = "phi3mini-4k"   # <- cambia a "mistral:7b" si creas otro 4k

# STT rápido
WHISPER_MODEL = "tiny"  # tiny/base
RECORD_SECONDS = 3
SAMPLE_RATE = 16000

# Seguridad: NO permitimos cmd: libre por defecto
ALLOW_RAW_CMD = False

# Carpetas útiles (ajusta a tu gusto)
PROJECTS = {
    "erp": r"C:\Users\Sebastian Vasquez\Pictures\verificador\ERP-Electrans",
    "jarvis": r"C:\Users\Sebastian Vasquez\jarvis_local311",
}

NOTES_FILE = os.path.join(PROJECTS["jarvis"], "jarvis_notes.txt")

# ======================
# TTS
# ======================
engine = pyttsx3.init()
engine.setProperty("rate", 175)

def speak(text: str):
    print(f"JARVIS: {text}")
    engine.say(text)
    engine.runAndWait()

# ======================
# AUDIO (STT)
# ======================
def record_wav(filename="input.wav", seconds=RECORD_SECONDS, samplerate=SAMPLE_RATE):
    if MIC_DEVICE is None:
        raise RuntimeError("No encontré micrófono (input device).")

    print(f"[Grabando {seconds}s... device={MIC_DEVICE}]")
    audio = sd.rec(
        int(seconds * samplerate),
        samplerate=samplerate,
        channels=1,
        dtype="int16",
        device=MIC_DEVICE
    )
    sd.wait()
    write(filename, samplerate, audio)
    return filename

def transcribe_whisper(model: WhisperModel, wav_path: str) -> str:
    segments, _ = model.transcribe(wav_path, language="es")
    text = " ".join([seg.text.strip() for seg in segments]).strip()
    return text

# ======================
# OLLAMA
# ======================
def ollama_chat(messages):
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "num_ctx": 4096,
            # puedes ajustar:
            # "temperature": 0.7
        }
    }
    r = requests.post(OLLAMA_URL, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()["message"]["content"]

# ======================
# WINDOWS HELPERS
# ======================
def run_start(target: str):
    subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)

def open_folder(path: str):
    subprocess.Popen(["explorer", path])

def lock_pc():
    subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])

def screenshot():
    # Guardar screenshot usando PowerShell + .NET (sin librerías extra)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(PROJECTS["jarvis"], f"screenshot_{ts}.png")
    ps = rf"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bmp)
$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bmp.Save("{out}", [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bmp.Dispose()
Write-Output "{out}"
"""
    p = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    if p.returncode == 0:
        return p.stdout.strip()
    return None

def get_ip():
    p = subprocess.run(["ipconfig"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return p.stdout[-4000:]

def list_processes():
    p = subprocess.run(["tasklist"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return p.stdout[-4000:]

def kill_process(name: str):
    # name puede ser "chrome.exe"
    p = subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return p.stdout.strip() or p.stderr.strip()

# ======================
# VOLUME CONTROL
# ======================
def set_volume_percent(percent: int):
    percent = max(0, min(100, percent))
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    volume = cast(interface, POINTER(IAudioEndpointVolume))
    # rango -65.25 a 0.0 dB, pero usamos scalar 0..1
    volume.SetMasterVolumeLevelScalar(percent / 100.0, None)

def mute(toggle: bool = True):
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    volume = cast(interface, POINTER(IAudioEndpointVolume))
    volume.SetMute(1 if toggle else 0, None)

# ======================
# COMMAND PARSING (Skills)
# ======================
pending_danger = None  # guarda acción peligrosa esperando confirmación

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())

def save_note(note: str):
    os.makedirs(os.path.dirname(NOTES_FILE), exist_ok=True)
    with open(NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {note}\n")

def run_command(intent: str):
    """
    Devuelve string si ejecutó algo, None si no.
    """
    global pending_danger

    t = normalize(intent)

    # Confirmación acciones peligrosas
    if pending_danger and ("confirmar" in t or "sí confirmar" in t or "dale confirmar" in t):
        action = pending_danger
        pending_danger = None

        if action == "shutdown":
            subprocess.Popen(["shutdown", "/s", "/t", "5"])
            return "Ya. Apagando en 5 segundos. No llores."
        if action == "restart":
            subprocess.Popen(["shutdown", "/r", "/t", "5"])
            return "Reiniciando en 5 segundos. Ojalá guardaste."
        if action == "kill_all_chrome":
            out = kill_process("chrome.exe")
            return f"Listo. Chrome muerto.\n{out[:200]}"

    if pending_danger and ("cancelar" in t or "no" == t or "negativo" in t):
        pending_danger = None
        return "Ok, cancelado. Te salvaste."

    # Salir
    if "salir" in t or "terminar" in t:
        return "__EXIT__"

    # Abrir apps
    if "abre chrome" in t or "abrir chrome" in t:
        run_start("chrome")
        return "Abriendo Chrome."
    if "abre vscode" in t or "abrir vscode" in t:
        run_start("code")
        return "Abriendo VS Code."
    if "abre spotify" in t or "abrir spotify" in t:
        run_start("spotify")
        return "Abriendo Spotify."
    if "abre explorador" in t or "abrir explorador" in t:
        run_start("explorer")
        return "Explorador abierto."

    # Abrir carpetas/proyectos
    if "abre proyecto erp" in t or "abrir proyecto erp" in t or "abre erp" in t:
        open_folder(PROJECTS["erp"])
        return "ERP abierto. A trabajar."
    if "abre jarvis" in t or "abrir jarvis" in t:
        open_folder(PROJECTS["jarvis"])
        return "Carpeta de Jarvis abierta."

    # Web search
    m = re.search(r"(busca|buscar|googlea|googlear)\s+(.*)$", t)
    if m:
        q = m.group(2).strip()
        webbrowser.open(f"https://www.google.com/search?q={requests.utils.quote(q)}")
        return f"Buscando: {q}"

    # Clipboard
    if "copia" in t and "portapapeles" in t:
        pyperclip.copy(intent)
        return "Copiado al portapapeles."
    if "qué tengo en el portapapeles" in t or "que tengo en el portapapeles" in t:
        return f"En portapapeles: {pyperclip.paste()[:200]}"

    # Notas
    if t.startswith("nota:") or t.startswith("anota:"):
        note = intent.split(":", 1)[1].strip()
        if note:
            save_note(note)
            return "Listo, anotado."
        return "¿Qué anoto? Di: nota: tu texto"

    if "leer notas" in t or "muestra notas" in t:
        if os.path.exists(NOTES_FILE):
            with open(NOTES_FILE, "r", encoding="utf-8") as f:
                return f.read()[-1200:] or "No hay notas."
        return "Aún no tienes notas."

    # Volumen
    m = re.search(r"volumen\s+(\d{1,3})", t)
    if m:
        set_volume_percent(int(m.group(1)))
        return f"Volumen al {m.group(1)}%."

    if "mute" in t or "silencio" in t:
        mute(True)
        return "Muteado."

    if "desmute" in t or "con sonido" in t:
        mute(False)
        return "Sonido de vuelta."

    # Info
    if "qué hora es" in t or "que hora es" in t:
        return datetime.now().strftime("Son las %H:%M.")
    if "qué día es" in t or "que dia es" in t or "fecha" in t:
        return datetime.now().strftime("Hoy es %d-%m-%Y.")

    if "mi ip" in t or "ip local" in t:
        return get_ip()

    if "procesos" in t or "tasklist" in t:
        return list_processes()

    m = re.search(r"(mata|cierra|kill)\s+(.*\.exe)", t)
    if m:
        exe = m.group(2).strip()
        out = kill_process(exe)
        return f"Intenté cerrar {exe}.\n{out[:400]}"

    # Acciones peligrosas (con confirmación)
    if "apaga el pc" in t or "apagar pc" in t:
        pending_danger = "shutdown"
        return "¿Seguro? Di: confirmar  (o: cancelar)"
    if "reinicia el pc" in t or "reiniciar pc" in t:
        pending_danger = "restart"
        return "¿Seguro que quieres reiniciar? Di: confirmar  (o: cancelar)"

    if "cierra chrome" in t or "mata chrome" in t:
        pending_danger = "kill_all_chrome"
        return "Te cierro Chrome completo. ¿Confirmar o cancelar?"

    if "bloquea el pc" in t or "bloquear pc" in t:
        lock_pc()
        return "PC bloqueado."

    if "screenshot" in t or "captura" in t or "pantallazo" in t:
        path = screenshot()
        return f"Captura lista: {path}" if path else "No pude sacar captura."

    # Raw cmd (opcional)
    if ALLOW_RAW_CMD and t.startswith("cmd:"):
        cmd = intent[4:].strip()
        subprocess.Popen(["cmd", "/c", cmd])
        return f"Ejecutando: {cmd}"

    return None

# ======================
# MAIN
# ======================
def main():
    speak("Listo. Enter para hablar. Di 'salir' para terminar. Y no, no soy Iron Man.")
    whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

    messages = [
        {"role": "system", "content": (
            "Eres Jarvis. Español chileno. Sarcástico, directo y útil.\n"
            "Si no tienes info, dilo. Si el usuario pide cosas peligrosas, pide confirmación.\n"
            "Responde corto."
        )}
    ]

    while True:
        input("\n[Enter para escuchar]")
        wav = record_wav(seconds=RECORD_SECONDS)
        text = transcribe_whisper(whisper, wav)
        print(f"TU: {text}")

        if not text:
            speak("No te escuché. Habla más fuerte o más cerca.")
            continue

        cmd_result = run_command(text)
        if cmd_result == "__EXIT__":
            speak("Ya, chao.")
            break
        if cmd_result:
            speak(cmd_result if len(cmd_result) < 350 else cmd_result[:350] + " ...")
            continue

        # Chat con IA (solo si no fue comando)
        messages.append({"role": "user", "content": text})
        try:
            reply = ollama_chat(messages)
        except Exception as e:
            speak("No me pude conectar a Ollama. Revisa que ollama esté corriendo.")
            print("ERROR:", e)
            continue

        messages.append({"role": "assistant", "content": reply})
        speak(reply)

if __name__ == "__main__":
    main()
