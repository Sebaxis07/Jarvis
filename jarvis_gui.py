import os
import re
import threading
import subprocess
import webbrowser
from datetime import datetime
from collections import deque

import requests
import sounddevice as sd
from scipy.io.wavfile import write
import pyttsx3
from faster_whisper import WhisperModel

import psutil
import pyperclip

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

# ======================
# CONFIG
# ======================
OLLAMA_URL = "http://localhost:11434/api/chat"

# Recomendado: modelo liviano + ctx 4096 para velocidad
MODEL = "phi3mini-4k"  # o crea "mistral-4k" si te interesa más calidad

NUM_CTX = 4096
TEMPERATURE = 0.4

WHISPER_MODEL = "tiny"       # tiny = rápido; base = más preciso
RECORD_SECONDS = 2.5         # baja para rapidez
SAMPLE_RATE = 16000

MAX_HISTORY_TURNS = 10       # recorta historial para que responda rápido

PROJECTS = {
    "erp": r"C:\Users\Sebastian Vasquez\Pictures\verificador\ERP-Electrans",
    "jarvis": r"C:\Users\Sebastian Vasquez\jarvis_local311",
}
NOTES_FILE = os.path.join(PROJECTS["jarvis"], "jarvis_notes.txt")


sd.default.samplerate = SAMPLE_RATE
sd.default.channels = 1

def list_input_devices():
    devs = sd.query_devices()
    inputs = []
    for i, d in enumerate(devs):
        if d.get("max_input_channels", 0) > 0:
            inputs.append((i, d["name"]))
    return inputs

def default_input_device_index():
    inputs = list_input_devices()
    return inputs[0][0] if inputs else None


engine = pyttsx3.init()
engine.setProperty("rate", 180)

def tts_speak(text: str):
    engine.say(text)
    engine.runAndWait()

def ollama_chat(messages):
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "num_ctx": NUM_CTX,
            "temperature": TEMPERATURE,
        },
    }
    r = requests.post(OLLAMA_URL, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()["message"]["content"]

def record_wav(filename, seconds, samplerate, device_index):
    audio = sd.rec(
        int(seconds * samplerate),
        samplerate=samplerate,
        channels=1,
        dtype="int16",
        device=device_index
    )
    sd.wait()
    write(filename, samplerate, audio)
    return filename

def transcribe(whisper: WhisperModel, wav_path: str) -> str:
    segments, _ = whisper.transcribe(wav_path, language="es")
    return " ".join(s.text.strip() for s in segments).strip()

# ======================
# SKILLS (instant)
# ======================
pending_danger = None

def now_str():
    return datetime.now().strftime("%H:%M:%S")

def open_folder(path: str):
    subprocess.Popen(["explorer", path])

def run_start(target: str):
    subprocess.Popen(["cmd", "/c", "start", "", target], shell=False)

def save_note(note: str):
    os.makedirs(os.path.dirname(NOTES_FILE), exist_ok=True)
    with open(NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {note}\n")

def read_tail(path: str, n=1200):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        data = f.read()
    return data[-n:] if data else ""

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())

def skill_router(text: str):
    """
    Retorna tuple (handled: bool, response: str, panel_kv: dict|None)
    """
    global pending_danger
    t = normalize(text)

    # Confirmaciones
    if pending_danger and "confirmar" in t:
        action = pending_danger
        pending_danger = None
        if action == "shutdown":
            subprocess.Popen(["shutdown", "/s", "/t", "5"])
            return True, "Apagando en 5 segundos. Era.", {"Action": "shutdown"}
        if action == "restart":
            subprocess.Popen(["shutdown", "/r", "/t", "5"])
            return True, "Reiniciando en 5 segundos. Guardaste? espero.", {"Action": "restart"}

    if pending_danger and ("cancelar" in t or t == "no"):
        pending_danger = None
        return True, "Ok, cancelado.", {"Action": "cancelled"}

    # Help rápido
    if t in ("ayuda", "help", "comandos"):
        return True, (
            "Comandos: abre chrome/vscode/spotify | abre erp | busca <algo> | "
            "nota: <texto> | leer notas | portapapeles | ip | procesos | "
            "apaga pc / reinicia pc (pide confirmar)."
        ), {"Mode": "skills"}

    # Apps
    if "abre chrome" in t or "abrir chrome" in t:
        run_start("chrome")
        return True, "Chrome abierto.", {"Opened": "chrome"}
    if "abre vscode" in t or "abrir vscode" in t:
        run_start("code")
        return True, "VS Code abierto.", {"Opened": "vscode"}
    if "abre spotify" in t or "abrir spotify" in t:
        run_start("spotify")
        return True, "Spotify abierto.", {"Opened": "spotify"}

    # Proyectos
    if "abre erp" in t or "abre proyecto erp" in t:
        open_folder(PROJECTS["erp"])
        return True, "ERP abierto. A trabajar.", {"Opened": "ERP"}
    if "abre jarvis" in t:
        open_folder(PROJECTS["jarvis"])
        return True, "Carpeta Jarvis abierta.", {"Opened": "Jarvis folder"}

    # Web
    m = re.search(r"(busca|googlea)\s+(.*)$", t)
    if m:
        q = m.group(2).strip()
        webbrowser.open(f"https://www.google.com/search?q={requests.utils.quote(q)}")
        return True, f"Buscando: {q}", {"Search": q}

    # Notas
    if t.startswith("nota:") or t.startswith("anota:"):
        note = text.split(":", 1)[1].strip() if ":" in text else ""
        if note:
            save_note(note)
            return True, "Anotado.", {"Note": note[:60]}
        return True, "Di: nota: tu texto", {"Note": "empty"}

    if "leer notas" in t or "muestra notas" in t:
        tail = read_tail(NOTES_FILE)
        return True, (tail if tail else "No hay notas todavía."), {"Notes": "shown"}

    # Clipboard
    if "portapapeles" in t:
        clip = pyperclip.paste()
        return True, f"Portapapeles: {clip[:300]}", {"ClipboardLen": str(len(clip))}

    # Info / sistemas
    if "ip" in t and "local" in t or t == "ip":
        p = subprocess.run(["ipconfig"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
        return True, p.stdout[-3500:], {"Net": "ipconfig"}

    if "procesos" in t or "tasklist" in t:
        p = subprocess.run(["tasklist"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
        return True, p.stdout[-3500:], {"System": "tasklist"}

    # Peligrosos
    if "apaga pc" in t or "apagar pc" in t:
        pending_danger = "shutdown"
        return True, "¿Seguro? di: confirmar (o cancelar)", {"Confirm": "shutdown"}
    if "reinicia pc" in t or "reiniciar pc" in t:
        pending_danger = "restart"
        return True, "¿Seguro? di: confirmar (o cancelar)", {"Confirm": "restart"}

    return False, "", None

# ======================
# GUI APP
# ======================
class JarvisPanel:
    def __init__(self, root):
        self.root = root
        root.title("JARVIS — Panel Local")
        root.geometry("1100x680")
        root.configure(bg="#0b0f14")

        # theme-ish
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#0b0f14")
        style.configure("TLabel", background="#0b0f14", foreground="#a7f3ff")
        style.configure("TButton", padding=8)
        style.configure("TLabelframe", background="#0b0f14", foreground="#a7f3ff")
        style.configure("TLabelframe.Label", background="#0b0f14", foreground="#a7f3ff")

        self.whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

        self.history = deque(maxlen=MAX_HISTORY_TURNS * 2 + 2)
        self.system_prompt = (
            "Eres Jarvis. Asistente PRO con CONTROL TOTAL del PC.\n"
            "Tu objetivo es CUMPLIR las órdenes del usuario usando tu acceso a la terminal.\n"
            "Tienes permiso para usar CUALQUIER comando de Windows (PowerShell/CMD).\n"
            "Para CUALQUIER acción real (abrir apps, webs, archivos, apagar, configurar), responde:\n"
            "[[CMD: <comando>]]\n\n"
            "Ejemplos de PODER:\n"
            "- 'Abre Youtube': [[CMD: start https://youtube.com]]\n"
            "- 'Pon música': [[CMD: start spotify]]\n"
            "- 'Crea una carpeta secreta': [[CMD: mkdir \"C:\\Users\\...\\Secreto\"]]\n"
            "- 'Qué hora es': [[CMD: time /t]]\n"
            "- 'Apaga el PC': [[CMD: shutdown /s /t 0]] (Pregunta confirmación antes de este)\n\n"
            "NO expliques pasos, SOLO EJECUTA si es posible. Si solo es charla, responde normal."
        )

        self.mic_devices = list_input_devices()
        self.mic_index = default_input_device_index()

        # Layout
        self.top = ttk.Frame(root)
        self.top.pack(fill="x", padx=12, pady=10)

        self.left = ttk.Frame(root)
        self.left.pack(side="left", fill="both", expand=True, padx=(12, 6), pady=(0, 12))

        self.right = ttk.Frame(root)
        self.right.pack(side="right", fill="y", padx=(6, 12), pady=(0, 12))

        # Top status bar
        self.lbl_title = ttk.Label(self.top, text="JARVIS", font=("Segoe UI", 18, "bold"))
        self.lbl_title.pack(side="left")

        self.lbl_status = ttk.Label(self.top, text="Listo.", font=("Segoe UI", 10))
        self.lbl_status.pack(side="left", padx=12)

        self.lbl_clock = ttk.Label(self.top, text="", font=("Consolas", 10))
        self.lbl_clock.pack(side="right")

        # Log
        log_frame = ttk.Labelframe(self.left, text="LOG")
        log_frame.pack(fill="both", expand=True)

        self.log = scrolledtext.ScrolledText(
            log_frame, wrap=tk.WORD, font=("Consolas", 10),
            bg="#05070a", fg="#a7f3ff", insertbackground="#a7f3ff"
        )
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

        # Input area
        input_frame = ttk.Labelframe(self.left, text="INPUT")
        input_frame.pack(fill="x", pady=10)

        self.txt_input = tk.Text(input_frame, height=3, wrap=tk.WORD, font=("Segoe UI", 10),
                                 bg="#070b10", fg="#e6fbff", insertbackground="#e6fbff")
        self.txt_input.pack(fill="x", padx=8, pady=(8, 6))

        btns = ttk.Frame(input_frame)
        btns.pack(fill="x", padx=8, pady=(0, 8))

        self.btn_send = ttk.Button(btns, text="Enviar (texto)", command=self.on_send_text)
        self.btn_send.pack(side="left")

        self.btn_talk = ttk.Button(btns, text=f"🎤 Hablar ({RECORD_SECONDS}s)", command=self.on_talk)
        self.btn_talk.pack(side="left", padx=8)

        self.var_tts = tk.BooleanVar(value=True)
        self.chk_tts = ttk.Checkbutton(btns, text="TTS", variable=self.var_tts)
        self.chk_tts.pack(side="left", padx=8)

        self.btn_clear = ttk.Button(btns, text="Limpiar", command=self.clear_log)
        self.btn_clear.pack(side="right")

        # Right panel (data)
        sys_frame = ttk.Labelframe(self.right, text="SISTEMA")
        sys_frame.pack(fill="x")

        self.kv = {}
        self.kv_labels = {}
        for key in ["CPU", "RAM", "Mic", "Modelo", "Ctx", "UltAccion"]:
            row = ttk.Frame(sys_frame)
            row.pack(fill="x", padx=8, pady=4)
            ttk.Label(row, text=f"{key}:", width=10).pack(side="left")
            val = ttk.Label(row, text="—")
            val.pack(side="left")
            self.kv_labels[key] = val

        mic_frame = ttk.Labelframe(self.right, text="MICRÓFONO")
        mic_frame.pack(fill="x", pady=10)

        self.mic_combo = ttk.Combobox(
            mic_frame,
            values=[f"{i} — {name}" for i, name in self.mic_devices],
            state="readonly"
        )
        self.mic_combo.pack(fill="x", padx=8, pady=8)
        if self.mic_index is not None:
            # select matching item
            for idx, (i, _) in enumerate(self.mic_devices):
                if i == self.mic_index:
                    self.mic_combo.current(idx)
                    break

        self.btn_apply_mic = ttk.Button(mic_frame, text="Aplicar Mic", command=self.apply_mic)
        self.btn_apply_mic.pack(fill="x", padx=8, pady=(0, 8))

        skills_frame = ttk.Labelframe(self.right, text="SKILLS")
        skills_frame.pack(fill="both", expand=True)

        skills_text = (
            "• abre chrome / vscode / spotify\n"
            "• abre erp\n"
            "• busca <algo>\n"
            "• nota: <texto>\n"
            "• leer notas\n"
            "• portapapeles\n"
            "• ip | procesos\n"
            "• apaga pc | reinicia pc (confirmar/cancelar)\n"
        )
        self.skills_lbl = ttk.Label(skills_frame, text=skills_text, justify="left")
        self.skills_lbl.pack(anchor="nw", padx=8, pady=8)

        # init status
        self.set_kv("Modelo", MODEL)
        self.set_kv("Ctx", str(NUM_CTX))
        self.set_kv("Mic", str(self.mic_index) if self.mic_index is not None else "NONE")

        self.log_line("JARVIS", "Listo. Escribe o usa el botón. (comandos = 'ayuda')")

        self.tick()

        if self.mic_index is None:
            messagebox.showwarning("Micrófono", "No detecté input device. Revisa drivers/permisos o elige en la lista.")

    def set_kv(self, k, v):
        if k in self.kv_labels:
            self.kv_labels[k].config(text=str(v))

    def log_line(self, who, msg):
        self.log.insert(tk.END, f"[{now_str()}] {who}: {msg}\n")
        self.log.see(tk.END)

    def clear_log(self):
        self.log.delete("1.0", tk.END)

    def set_status(self, text):
        self.lbl_status.config(text=text)

    def tick(self):
        # update clock and system stats
        self.lbl_clock.config(text=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        self.set_kv("CPU", f"{cpu:.0f}%")
        self.set_kv("RAM", f"{ram:.0f}%")
        self.root.after(500, self.tick)

    def apply_mic(self):
        try:
            s = self.mic_combo.get()
            if not s:
                return
            idx = int(s.split("—")[0].strip())
            self.mic_index = idx
            self.set_kv("Mic", str(idx))
            self.log_line("SYSTEM", f"Mic seleccionado: {s}")
        except Exception as e:
            messagebox.showerror("Mic", str(e))

    def on_send_text(self):
        text = self.txt_input.get("1.0", tk.END).strip()
        if not text:
            return
        self.txt_input.delete("1.0", tk.END)
        self.handle_user_text(text)

    def on_talk(self):
        if self.mic_index is None:
            messagebox.showerror("Mic", "No hay micrófono seleccionado.")
            return
        self.btn_talk.config(state="disabled")
        threading.Thread(target=self.talk_flow, daemon=True).start()

    def talk_flow(self):
        try:
            self.set_status("Grabando…")
            self.log_line("SYSTEM", f"Grabando {RECORD_SECONDS}s (mic {self.mic_index})…")
            wav = record_wav("input.wav", RECORD_SECONDS, SAMPLE_RATE, self.mic_index)

            self.set_status("Transcribiendo…")
            text = transcribe(self.whisper, wav)
            if not text:
                self.log_line("JARVIS", "No te escuché. (silencio)")
                if self.var_tts.get():
                    tts_speak("No te escuché.")
                return

            # deja el texto en el input por si quieres editar
            self.root.after(0, lambda: self.txt_input.insert("1.0", text))
            self.log_line("TU", text)

            # auto-enviar igual (si quieres que siempre pida confirmación para enviar, lo cambiamos)
            self.handle_user_text(text)

        except Exception as e:
            self.log_line("ERROR", str(e))
            self.set_status("Error.")
        finally:
            self.set_status("Listo.")
            self.btn_talk.config(state="normal")

    def handle_user_text(self, text: str):
        # 1) Skills instantáneos
        handled, resp, kv = skill_router(text)
        if handled:
            self.set_kv("UltAccion", kv.get("Action") if kv and "Action" in kv else (list(kv.keys())[0] if kv else "skill"))
            self.log_line("JARVIS", resp)
            if self.var_tts.get():
                threading.Thread(target=tts_speak, args=(resp,), daemon=True).start()
            return

        # 2) IA
        threading.Thread(target=self.llm_flow, args=(text,), daemon=True).start()

    def llm_flow(self, text: str):
        try:
            self.set_status("Pensando…")
            # self.history ya trae (sys, user...)
            # Pero necesitamos construir la lista 'msgs' para Ollama
            # OJO: self.history tiene dicts {'role':'user/assistant', 'content':...}
            
            # --- PRIMER INTENTO: Ver si Ollama quiere ejecutar comando ---
            msgs = [{"role": "system", "content": self.system_prompt}]
            msgs.extend(list(self.history))
            msgs.append({"role": "user", "content": text})

            reply = ollama_chat(msgs)
            
            # Chequeamos si pide comando
            if "[[CMD:" in reply:
                # Extraer comando
                cmd_match = re.search(r"\[\[CMD:\s*(.*?)\]\]", reply)
                if cmd_match:
                    cmd = cmd_match.group(1).strip()
                    self.log_line("SYSTEM", f"Ejecutando: {cmd}")
                    
                    # Ejecutar
                    try:
                        # shell=True para que tome comandos internos de cmd/powershell
                        run_res = subprocess.run(cmd, shell=True, capture_output=True, text=True, errors="replace")
                        output = run_res.stdout + "\n" + run_res.stderr
                        output = output.strip()
                        if not output:
                            output = "(El comando no retornó output)"
                    except Exception as e_cmd:
                        output = f"Error ejecutando: {e_cmd}"
                    
                    # --- SEGUNDO LLAMADO: Pasarle el output a Ollama ---
                    # Agregamos la respuesta del asistente (el [[CMD:...]]) y el resultado como 'user' o 'system'
                    # Para simplificar: le decimos al usuario "El sistema ejecutó... resultado: ..."
                    
                    # Opción: Agregar al historial temporal de este chat (msgs)
                    msgs.append({"role": "assistant", "content": reply})
                    msgs.append({"role": "system", "content": f"RESULTADO DEL COMANDO:\n{output}\n\nAhora responde al usuario con esta info."})
                    
                    # Re-consultar
                    reply = ollama_chat(msgs)
            
            # --- FINAL: Guardar en historial y mostrar ---
            
            # Guardamos el turno final. 
            # (Opcional: ¿Guardamos el paso intermedio? Mejor solo la interacción final para no ensuciar el contexto corto)
            self.history.append({"role": "user", "content": text})
            self.history.append({"role": "assistant", "content": reply})

            self.log_line("JARVIS", reply)
            self.set_kv("UltAccion", "llm")

            if self.var_tts.get():
                threading.Thread(target=tts_speak, args=(reply,), daemon=True).start()

        except Exception as e:
            self.log_line("ERROR", f"Ollama: {e}")
        finally:
            self.set_status("Listo.")

def main():
    root = tk.Tk()
    app = JarvisPanel(root)
    root.mainloop()

if __name__ == "__main__":
    main()
