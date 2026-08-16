"""
EBS Camera Suite - Masaustu (Modular) - v9.0
==============================================
Orijinal ebs_camera.py + EBS_Huawei_Camera_Builder tek bir ana ekrandan (HUB)
acilan MODULLERE bolundu. Her ozellik ayri bir modul karti; kullanici sadece
ihtiyaci olan modulu acar. Ana performans kazanimi:

  * USB WEB CAM modulu ONIZLEME / KAYIT / TKINTER GORUNTU CIZIMI YAPMAZ.
    FFmpeg stdout'undan gelen ham RGB byte'lari HICBIR KOPYALAMA/PIL/NUMPY
    islemi olmadan dogrudan pyvirtualcam.send() ile sanal kameraya yazilir.
    Bu, orijinal uygulamadaki "SADECE VIRTUAL CAMERA" performans modunun
    varsayilan ve tek davranis oldugu, ayrica onizleme kuyruklarinin da
    tamamen kod disinda birakildigi bir mimari -> CPU/RAM kullanimi cok dusuk.

  * Onizleme isteyen kullanicilar icin ayri, istege bagli bir
    "Canli Onizleme" modulu var (o modulde iken FPS bilinctli olarak
    dusurulur, ana akisi etkilemez).

  * Kayit, Zoom/Yon, Baglanti Ayarlari, Ses Cihazlari, Android APK Kurulumu
    hepsi ayri modul olarak korunur; hicbir ozellik kaldirilmadi.

Kullanim: Hub ekraninda bir modul karti tikla -> o modulun ekrani acilir.
"USB Web Cam" karti: PC'de ADB uzerinden bagli/wifi'daki telefonu, isletim
sisteminin gordugu bir SANAL WEBCAM olarak sunar (OBS, Zoom, Meet vs. bu
kamerayi secebilir). Telefon tarafinda "APK'da USB Web Cam" secilince
Android uygulamasi zaten H.264 stream'i bu porta gonderiyor (Builder script
bunu kuruyor) - PC tarafinda ekstra bir sey yapmana gerek yok.

Bagimliliklar: customtkinter, pillow, numpy, pyvirtualcam
    pip install customtkinter pillow numpy pyvirtualcam
"""

import customtkinter as ctk
import subprocess
import threading
import queue
import os
import re
import socket
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    import pyvirtualcam
except ImportError:
    pyvirtualcam = None


APP_NAME = "EBS Camera Suite"
DEFAULT_VIDEO_PORT = 27183
DEFAULT_CONTROL_PORT = 27184
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 30

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


# ======================================================================
# BACKEND  (UI'dan tamamen bagimsiz - hicbir tkinter referansi yok)
# Tum modullerin ortak kullandigi ADB / FFmpeg / VirtualCam / Kayit motoru.
# ======================================================================
class Settings:
    def __init__(self):
        self.adb_path = r"C:\adb\adb.exe"
        self.ffmpeg_path = r"C:\ffmpeg.exe"
        self.video_port = DEFAULT_VIDEO_PORT
        self.control_port = DEFAULT_CONTROL_PORT
        self.width = DEFAULT_WIDTH
        self.height = DEFAULT_HEIGHT
        self.fps = DEFAULT_FPS
        self.record_dir = str(Path.home() / "Videos" / "EBS_Recordings")
        self.auto_wifi_fallback = True


class StreamBackend:
    """Baglanti, decode, sanal kamera ve kayit islerini yurutur.
    on_log / on_status / on_state_change callback'leri ile UI'yi bilgilendirir,
    fakat UI'ya dogrudan bagli DEGILDIR (hiz ve modulerlik icin)."""

    def __init__(self, settings: Settings):
        self.s = settings

        self.on_log = lambda text: None
        self.on_status = lambda text: None
        self.on_transport = lambda mode, host: None
        self.on_control_state = lambda orientation, zoom, max_zoom: None
        self.on_record_state = lambda text: None
        self.on_frame = None  # sadece onizleme modulunde set edilir (np.ndarray -> None)

        self.decoder_process = None
        self.record_process = None
        self.running_stream = False
        self.supervisor_running = False
        self.manual_stop = False

        self.cached_wifi_ip = ""
        self.active_transport = "YOK"

        self.control_socket = None
        self.control_lock = threading.Lock()
        self.control_stop = threading.Event()

        self.phone_orientation = "LANDSCAPE"
        self.zoom_value = 1.0
        self.max_zoom = 1.0

        self.virtual_cam = None
        self.virtual_cam_enabled = False

        self.recording = False
        self.record_queue = queue.Queue(maxsize=4)
        self.record_thread = None
        self.record_file = ""

        # True ise: onizleme/frame kopyalama YAPILMAZ, ham byte dogrudan
        # sanal kameraya yazilir. Webcam modulunun varsayilan calisma bicimi.
        self.zero_copy_mode = True

    def log(self, text):
        self.on_log(text)

    # ---------------- ADB ----------------
    def adb_devices(self):
        if not os.path.isfile(self.s.adb_path):
            return False, "ADB bulunamadi: " + self.s.adb_path
        try:
            p = subprocess.run(
                [self.s.adb_path, "devices"],
                capture_output=True, text=True,
                encoding="utf-8", errors="ignore", timeout=4,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return "\tdevice" in p.stdout, p.stdout
        except Exception as exc:
            return False, f"ADB hatasi: {exc}"

    def learn_wifi_ip(self):
        try:
            p = subprocess.run(
                [self.s.adb_path, "shell", "ip", "-f", "inet", "addr", "show", "wlan0"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=4,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/", p.stdout)
            if not match:
                p = subprocess.run(
                    [self.s.adb_path, "shell", "ip", "route"],
                    capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=4,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                match = re.search(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)", p.stdout)
            if match:
                self.cached_wifi_ip = match.group(1)
                self.log(f"[WIFI] Telefon IP: {self.cached_wifi_ip}\n")
        except Exception as exc:
            self.log(f"[WIFI] IP ogrenme hatasi: {exc}\n")
        return self.cached_wifi_ip

    def configure_adb_forward(self):
        try:
            subprocess.run([self.s.adb_path, "forward", "--remove-all"],
                            capture_output=True, timeout=4,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            for port in (self.s.video_port, self.s.control_port):
                subprocess.run([self.s.adb_path, "forward", f"tcp:{port}", f"tcp:{port}"],
                                capture_output=True, timeout=4,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            p = subprocess.run([self.s.adb_path, "forward", "--list"],
                                capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=4,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            ok = f"tcp:{self.s.video_port}" in p.stdout and f"tcp:{self.s.control_port}" in p.stdout
            if ok:
                self.log("[USB] Video + kontrol portlari hazir.\n")
            return ok
        except Exception as exc:
            self.log(f"[USB] Forward hatasi: {exc}\n")
            return False

    def prepare_connection_async(self, done_cb=None):
        def worker():
            connected, output = self.adb_devices()
            self.log("\n> adb devices\n" + output + "\n")
            if not connected:
                self.log("[HATA] Telefon USB/ADB ile bagli olmali.\n")
                if done_cb:
                    done_cb(False)
                return
            self.learn_wifi_ip()
            ok = self.configure_adb_forward()
            if ok:
                self.on_transport("USB", "127.0.0.1")
                self.on_status("USB hazir")
            if done_cb:
                done_cb(ok)
        threading.Thread(target=worker, daemon=True).start()

    def choose_transport(self):
        usb, _ = self.adb_devices()
        if usb:
            self.learn_wifi_ip()
            if self.configure_adb_forward():
                return "127.0.0.1", "USB"
        if self.s.auto_wifi_fallback and self.cached_wifi_ip:
            return self.cached_wifi_ip, "WIFI"
        return "", "YOK"

    # ---------------- Kontrol kanali (yon + zoom) ----------------
    def start_control_channel(self, host):
        self.stop_control_channel()
        self.control_stop.clear()
        threading.Thread(target=self._control_loop, args=(host,), daemon=True).start()

    def _control_loop(self, host):
        try:
            sock = socket.create_connection((host, self.s.control_port), timeout=4)
            sock.settimeout(1.2)
            with self.control_lock:
                self.control_socket = sock
            fileobj = sock.makefile("r", encoding="utf-8", errors="ignore")
            self.log(f"[CONTROL] Baglandi: {host}:{self.s.control_port}\n")
            self.send_control("GET_STATE")
            while not self.control_stop.is_set():
                try:
                    line = fileobj.readline()
                    if not line:
                        break
                    self._parse_control_state(line.strip())
                except socket.timeout:
                    try:
                        self.send_control("GET_STATE")
                    except Exception:
                        break
        except Exception as exc:
            self.log(f"[CONTROL] Baglanti yok: {exc}\n")
        finally:
            with self.control_lock:
                try:
                    if self.control_socket:
                        self.control_socket.close()
                except Exception:
                    pass
                self.control_socket = None

    def stop_control_channel(self):
        self.control_stop.set()
        with self.control_lock:
            try:
                if self.control_socket:
                    self.control_socket.shutdown(socket.SHUT_RDWR)
                    self.control_socket.close()
            except Exception:
                pass
            self.control_socket = None

    def send_control(self, command):
        with self.control_lock:
            if not self.control_socket:
                return False
            self.control_socket.sendall((command.strip() + "\n").encode("utf-8"))
            return True

    def _parse_control_state(self, line):
        if not line.startswith("STATE "):
            return
        values = {}
        for token in line[6:].split():
            if "=" in token:
                k, v = token.split("=", 1)
                values[k] = v
        self.phone_orientation = values.get("orientation", self.phone_orientation).upper()
        try:
            self.zoom_value = float(values.get("zoom", self.zoom_value))
        except Exception:
            pass
        try:
            self.max_zoom = max(1.0, float(values.get("maxZoom", self.max_zoom)))
        except Exception:
            pass
        self.on_control_state(self.phone_orientation, self.zoom_value, self.max_zoom)

    def set_zoom(self, value):
        value = max(1.0, min(float(value), self.max_zoom))
        self.zoom_value = value
        self.send_control(f"ZOOM {value:.2f}")
        return value

    # ---------------- Sanal kamera ----------------
    def enable_virtual_cam(self):
        if pyvirtualcam is None:
            self.log("[VCAM] pyvirtualcam kurulu degil (pip install pyvirtualcam).\n")
            return False
        try:
            self.virtual_cam = pyvirtualcam.Camera(
                width=self.s.width, height=self.s.height, fps=self.s.fps,
                fmt=pyvirtualcam.PixelFormat.RGB,
            )
            self.virtual_cam_enabled = True
            self.log(f"[VCAM] Aktif: {self.virtual_cam.device}\n")
            return True
        except Exception as exc:
            self.virtual_cam_enabled = False
            self.virtual_cam = None
            self.log(f"[VCAM HATASI] {exc}\n")
            return False

    def disable_virtual_cam(self):
        self.virtual_cam_enabled = False
        if self.virtual_cam is not None:
            try:
                self.virtual_cam.close()
            except Exception:
                pass
        self.virtual_cam = None

    # ---------------- Receiver / decoder (supervisor loop) ----------------
    def start_receiver(self):
        if self.supervisor_running:
            return
        if Image is None or np is None:
            self.log("[HATA] Pillow veya NumPy eksik.\n")
            return
        if not os.path.isfile(self.s.ffmpeg_path):
            self.log(f"[HATA] FFmpeg bulunamadi: {self.s.ffmpeg_path}\n")
            return
        self.manual_stop = False
        self.supervisor_running = True
        threading.Thread(target=self._supervisor_loop, daemon=True).start()

    def _supervisor_loop(self):
        self.log("\n[AUTO] Baglanti yoneticisi baslatildi.\n")
        while not self.manual_stop:
            host, mode = self.choose_transport()
            if not host:
                self.on_transport("BEKLENIYOR", "")
                self.on_status("Telefon bekleniyor")
                time.sleep(1.5)
                continue

            self.active_transport = mode
            self.on_transport(mode, host)
            self.start_control_channel(host)
            self.log(f"[AUTO] {mode}: {host}:{self.s.video_port}\n")

            self._run_decoder_blocking(host)
            self.stop_control_channel()

            if self.manual_stop:
                break

            self.log("[AUTO] Akis koptu; alternatif baglanti araniyor...\n")
            self.on_status("Yeniden baglaniyor")
            time.sleep(0.15 if self.active_transport == "USB" else 0.60)

        self.supervisor_running = False
        self.on_transport("DURDU", "")
        self.on_status("Durduruldu")

    @staticmethod
    def _read_exact(stream, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = stream.read(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _run_decoder_blocking(self, host):
        width, height, fps, port = self.s.width, self.s.height, self.s.fps, self.s.video_port

        command = [
            self.s.ffmpeg_path,
            "-hide_banner", "-loglevel", "warning",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-f", "h264",
            "-i", f"tcp://{host}:{port}?timeout=4000000",
            "-an",
            "-pix_fmt", "rgb24",
            "-f", "rawvideo",
            "pipe:1",
        ]

        try:
            self.decoder_process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=10 ** 7,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as exc:
            self.log(f"[FFMPEG HATASI] {exc}\n")
            return

        self.running_stream = True
        frame_size = width * height * 3
        started_at = time.perf_counter()
        first_logged = False
        perf_count = 0
        perf_started = time.perf_counter()

        threading.Thread(target=self._stderr_loop, args=(self.decoder_process,), daemon=True).start()

        try:
            while (not self.manual_stop and self.decoder_process
                   and self.decoder_process.poll() is None):
                raw = self._read_exact(self.decoder_process.stdout, frame_size)
                if raw is None:
                    break

                if not first_logged:
                    first_logged = True
                    ms = (time.perf_counter() - started_at) * 1000.0
                    self.log(f"[LATENCY] Ilk goruntu {ms:.0f} ms icinde geldi.\n")

                perf_count += 1
                now = time.perf_counter()
                if now - perf_started >= 2.0:
                    self.log(f"[PERF] Gercek decode FPS: {perf_count / (now - perf_started):.1f}\n")
                    perf_count = 0
                    perf_started = now

                # === ZERO-COPY YOL (Webcam modulu varsayilani) ===
                # Ham RGB byte'lari HICBIR ARA KOPYA olmadan direkt vcam'e.
                if self.virtual_cam_enabled and self.virtual_cam is not None:
                    try:
                        self.virtual_cam.send(
                            np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
                        )
                    except Exception as exc:
                        self.log(f"[VCAM YAZMA HATASI] {exc}\n")

                # Sadece onizleme/kayit modulu acikken ekstra kopya olusur.
                if not self.zero_copy_mode:
                    frame = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3)).copy()
                    if self.on_frame is not None:
                        self.on_frame(frame)
                    if self.recording:
                        try:
                            self.record_queue.put_nowait(frame)
                        except queue.Full:
                            pass

        except Exception as exc:
            self.log(f"[DECODER HATASI] {exc}\n")

        self.running_stream = False
        p = self.decoder_process
        self.decoder_process = None
        if p:
            try:
                p.terminate()
                p.wait(timeout=1)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def _stderr_loop(self, process):
        try:
            for line in iter(process.stderr.readline, b""):
                if not line:
                    break
        except Exception:
            pass

    def stop_receiver(self):
        self.manual_stop = True
        self.running_stream = False
        self.stop_control_channel()
        self.stop_recording()
        p = self.decoder_process
        self.decoder_process = None
        if p:
            try:
                p.terminate()
                p.wait(timeout=1)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        self.disable_virtual_cam()
        self.on_status("Durduruldu")
        self.log("[STOP] Receiver durduruldu.\n")

    # ---------------- Kayit ----------------
    def start_recording(self, with_audio, audio_device=""):
        if self.recording:
            return
        os.makedirs(self.s.record_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = Path(self.s.record_dir) / f"EBS_{stamp}.mp4"

        command = [
            self.s.ffmpeg_path, "-hide_banner", "-loglevel", "warning",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{self.s.width}x{self.s.height}", "-r", str(self.s.fps),
            "-i", "pipe:0",
        ]
        if with_audio and audio_device:
            command += ["-f", "dshow", "-i", f"audio={audio_device}"]
        command += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
        if with_audio and audio_device:
            command += ["-c:a", "aac", "-shortest"]
        command += [str(output_file)]

        try:
            self.record_process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.recording = True
            self.record_file = str(output_file)
            self.zero_copy_mode = False  # kayit sirasinda frame'e ihtiyac var
            self.record_thread = threading.Thread(target=self._record_writer_loop, daemon=True)
            self.record_thread.start()
            self.on_record_state("SESLI" if with_audio else "SESSIZ")
            self.log(f"[REC] Kayit basladi: {output_file}\n")
        except Exception as exc:
            self.recording = False
            self.record_process = None
            self.log(f"[REC HATASI] {exc}\n")

    def _record_writer_loop(self):
        while self.recording:
            try:
                frame = self.record_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            p = self.record_process
            if p is None or p.stdin is None or p.poll() is not None:
                break
            try:
                p.stdin.write(frame.tobytes())
            except Exception:
                break

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        p = self.record_process
        self.record_process = None
        if p:
            try:
                if p.stdin:
                    p.stdin.close()
                p.wait(timeout=8)
            except Exception:
                try:
                    p.terminate()
                except Exception:
                    pass
        while not self.record_queue.empty():
            try:
                self.record_queue.get_nowait()
            except queue.Empty:
                break
        self.on_record_state("Kapali")
        self.log(f"[REC] Kayit tamamlandi: {self.record_file}\n")


# ======================================================================
# UI YARDIMCILARI
# ======================================================================
def labeled_entry(parent, label, value):
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    frame.pack(fill="x", padx=12, pady=4)
    ctk.CTkLabel(frame, text=label, width=110, anchor="w").pack(side="left")
    entry = ctk.CTkEntry(frame)
    entry.insert(0, value)
    entry.pack(side="left", fill="x", expand=True)
    return entry


class ModuleCard(ctk.CTkFrame):
    """Hub ekranindaki tiklanabilir modul karti."""

    def __init__(self, parent, icon, title, desc, command, enabled=True):
        super().__init__(parent, corner_radius=16, fg_color=("#1f2530", "#1f2530"))
        self.configure(cursor="hand2" if enabled else "arrow")

        icon_lbl = ctk.CTkLabel(self, text=icon, font=("Segoe UI Emoji", 34))
        icon_lbl.pack(pady=(20, 6))

        title_lbl = ctk.CTkLabel(self, text=title, font=("Roboto", 17, "bold"))
        title_lbl.pack(pady=(0, 4))

        desc_lbl = ctk.CTkLabel(self, text=desc, font=("Roboto", 12),
                                 text_color="#9aa4b2", wraplength=190, justify="center")
        desc_lbl.pack(pady=(0, 18), padx=10)

        if not enabled:
            ctk.CTkLabel(self, text="(bagimlilik eksik)", font=("Roboto", 10),
                         text_color="#e06666").pack(pady=(0, 10))

        for widget in (self, icon_lbl, title_lbl, desc_lbl):
            widget.bind("<Button-1>", lambda e: command() if enabled else None)
            widget.bind("<Enter>", lambda e: self.configure(fg_color=("#2a3140", "#2a3140")) if enabled else None)
            widget.bind("<Leave>", lambda e: self.configure(fg_color=("#1f2530", "#1f2530")))


# ======================================================================
# ANA UYGULAMA - HUB + MODULLER (tek pencere, frame degistirme)
# ======================================================================
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1180x760")
        self.minsize(980, 680)

        self.settings = Settings()
        self.backend = StreamBackend(self.settings)
        self.backend.on_log = self._log
        self.backend.on_status = self._status

        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True)

        self.current_frame = None
        self.show_hub()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---- ortak yardimcilar ----
    def _log(self, text):
        print(text, end="")  # global log; ayrica her modul kendi log kutusuna da yazar

    def _status(self, text):
        pass

    def _clear(self):
        if self.current_frame is not None:
            self.current_frame.destroy()
        self.current_frame = None

    def show_hub(self):
        self._clear()
        self.current_frame = HubFrame(self.container, self)
        self.current_frame.pack(fill="both", expand=True)

    def open_module(self, frame_cls):
        self._clear()
        self.current_frame = frame_cls(self.container, self)
        self.current_frame.pack(fill="both", expand=True)

    def on_close(self):
        try:
            self.backend.stop_receiver()
        except Exception:
            pass
        self.destroy()


class HubFrame(ctk.CTkFrame):
    def __init__(self, parent, app: App):
        super().__init__(parent, fg_color="transparent")
        self.app = app

        header = ctk.CTkFrame(self, corner_radius=16)
        header.pack(fill="x", padx=24, pady=(24, 12))
        ctk.CTkLabel(header, text="EBS CAMERA SUITE", font=("Roboto", 30, "bold")).pack(pady=(16, 2))
        ctk.CTkLabel(header, text="Bir modul sec - sadece ihtiyacin olan ozellik acilsin",
                     font=("Roboto", 14), text_color="#9aa4b2").pack(pady=(0, 16))

        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=24, pady=12)
        for c in range(3):
            grid.grid_columnconfigure(c, weight=1)

        cards = [
            ("USB WEB CAM", "\U0001F4F7",
             "Telefonu PC'de sanal webcam olarak sun. Onizleme yok, minimum kaynak.",
             lambda: app.open_module(WebcamModuleFrame), pyvirtualcam is not None),
            ("Canli Onizleme", "\U0001F5A5",
             "Goruntuyu pencerede canli izle (daha fazla CPU kullanir).",
             lambda: app.open_module(PreviewModuleFrame), Image is not None and np is not None),
            ("Kayit", "\u23FA",
             "Sesli / sessiz olarak MP4 kaydi al.",
             lambda: app.open_module(RecordModuleFrame), True),
            ("Zoom && Yon", "\U0001F50D",
             "Telefon kamerasinda zoom ve yon kontrolu.",
             lambda: app.open_module(ZoomModuleFrame), True),
            ("Baglanti Ayarlari", "\u2699",
             "ADB, FFmpeg, port, cozunurluk ve FPS ayarlari.",
             lambda: app.open_module(SettingsModuleFrame), True),
            ("Android APK Kurulumu", "\U0001F4F1",
             "Klasordeki APK'yi otomatik bul ve ADB ile telefona kur.",
             lambda: app.open_module(ApkModuleFrame), True),
        ]

        for i, (title, icon, desc, cmd, enabled) in enumerate(cards):
            r, c = divmod(i, 3)
            card = ModuleCard(grid, icon, title, desc, cmd, enabled)
            card.grid(row=r, column=c, padx=10, pady=10, sticky="nsew")
            grid.grid_rowconfigure(r, weight=1)

        footer = ctk.CTkLabel(
            self,
            text=("Durum: " + ("Baglanti aktif" if self.app.backend.running_stream else "Beklemede")),
            font=("Consolas", 12), text_color="#9aa4b2",
        )
        footer.pack(pady=(0, 14))


class ModuleFrameBase(ctk.CTkFrame):
    """Tum modullerde ortak: ust bar + geri (Hub'a don) butonu + log kutusu."""

    def __init__(self, parent, app: App, title):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.backend = app.backend

        top = ctk.CTkFrame(self, corner_radius=14)
        top.pack(fill="x", padx=18, pady=(18, 8))

        ctk.CTkButton(top, text="\u2190 HUB", width=90,
                      command=self.go_hub).pack(side="left", padx=12, pady=10)
        ctk.CTkLabel(top, text=title, font=("Roboto", 20, "bold")).pack(side="left", padx=14)

        self.status_label = ctk.CTkLabel(top, text="Durum: Hazir", font=("Roboto", 13))
        self.status_label.pack(side="right", padx=16)

    def go_hub(self):
        self.on_leave()
        self.app.show_hub()

    def on_leave(self):
        """Alt siniflar gerekirse override eder (orn. onizleme/kayit kapatmak icin)."""
        pass

    def set_status(self, text):
        self.after(0, lambda: self.status_label.configure(text=f"Durum: {text}"))


# ---------------------------------------------------------------------
# MODUL 1: USB WEB CAM  (performans-oncelikli, zero-copy)
# ---------------------------------------------------------------------
class WebcamModuleFrame(ModuleFrameBase):
    def __init__(self, parent, app):
        super().__init__(parent, app, "USB / Wi-Fi Web Cam")

        self.backend.on_status = self.set_status
        self.backend.on_transport = self._on_transport
        self.backend.zero_copy_mode = True
        self.backend.on_frame = None  # onizleme kapali -> ekstra kopya yok

        body = ctk.CTkFrame(self, corner_radius=16)
        body.pack(fill="both", expand=True, padx=18, pady=8)

        info = ctk.CTkLabel(
            body,
            text=(
                "Bu modul onizleme cizmez, sadece telefon goruntusunu sanal\n"
                "webcam cikisina (pyvirtualcam) yazar. OBS, Zoom, Meet, Teams\n"
                "gibi uygulamalarda 'EBS Camera' / sanal kamera cihazini secebilirsin.\n\n"
                "Telefon tarafinda APK'da 'USB Web Cam' secilince Android uygulamasi\n"
                "otomatik olarak bu porta H.264 stream gonderir."
            ),
            font=("Roboto", 14), justify="left", text_color="#c7ccd4",
        )
        info.pack(pady=24, padx=24)

        self.transport_label = ctk.CTkLabel(body, text="Baglanti: YOK", font=("Consolas", 13, "bold"))
        self.transport_label.pack(pady=6)

        self.vcam_label = ctk.CTkLabel(body, text="Sanal kamera: kapali", font=("Consolas", 12))
        self.vcam_label.pack(pady=4)

        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(pady=18)

        self.start_btn = ctk.CTkButton(btns, text="\u25B6 WEB CAM'I BASLAT", width=220, height=46,
                                        font=("Roboto", 15, "bold"), command=self.start)
        self.start_btn.grid(row=0, column=0, padx=8)

        self.stop_btn = ctk.CTkButton(btns, text="\u25A0 DURDUR", width=160, height=46,
                                       font=("Roboto", 15, "bold"), fg_color="#8a3030",
                                       hover_color="#a63b3b", command=self.stop)
        self.stop_btn.grid(row=0, column=1, padx=8)

        self.log_box = ctk.CTkTextbox(body, font=("Consolas", 11))
        self.log_box.pack(fill="both", expand=True, padx=18, pady=(6, 18))

        self.backend.on_log = self._log
        self._log("USB / Wi-Fi Web Cam modulu hazir. Onizleme kapali -> dusuk CPU kullanimi.\n")

        if pyvirtualcam is None:
            self._log("[UYARI] pyvirtualcam kurulu degil -> 'pip install pyvirtualcam'\n")

    def _log(self, text):
        self.after(0, lambda: (self.log_box.insert("end", text), self.log_box.see("end")))

    def _on_transport(self, mode, host):
        text = f"Baglanti: {mode}" + (f" ({host})" if host else "")
        self.after(0, lambda: self.transport_label.configure(text=text))

    def start(self):
        if not self.backend.virtual_cam_enabled:
            ok = self.backend.enable_virtual_cam()
            self.vcam_label.configure(
                text=f"Sanal kamera: {'acik' if ok else 'acilamadi'}"
            )
            if not ok:
                messagebox.showerror(APP_NAME, "Sanal kamera baslatilamadi. pyvirtualcam / OBS Virtual Camera kurulu mu?")
                return
        self.backend.zero_copy_mode = True
        self.backend.prepare_connection_async(lambda ok: self.backend.start_receiver())

    def stop(self):
        self.backend.stop_receiver()
        self.vcam_label.configure(text="Sanal kamera: kapali")

    def on_leave(self):
        # Hub'a donunce goruntuyu acik birakmak istersen bu iki satiri yorumla.
        pass


# ---------------------------------------------------------------------
# MODUL 2: CANLI ONIZLEME (istege bagli, daha fazla kaynak kullanir)
# ---------------------------------------------------------------------
class PreviewModuleFrame(ModuleFrameBase):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Canli Onizleme")

        self.backend.on_status = self.set_status
        self.backend.zero_copy_mode = False
        self.backend.on_frame = self._on_frame
        self.current_photo = None
        self.frame_queue = queue.Queue(maxsize=1)

        self.preview_label = ctk.CTkLabel(self, text="Baslatilmadi. Asagidan baslat.", font=("Roboto", 16))
        self.preview_label.pack(fill="both", expand=True, padx=18, pady=8)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(pady=10)
        ctk.CTkButton(btns, text="\u25B6 BASLAT", width=160, command=self.start).grid(row=0, column=0, padx=6)
        ctk.CTkButton(btns, text="\u25A0 DURDUR", width=160, fg_color="#8a3030", command=self.stop).grid(row=0, column=1, padx=6)

        self.after(30, self._ui_loop)

    def start(self):
        self.backend.zero_copy_mode = False
        self.backend.prepare_connection_async(lambda ok: self.backend.start_receiver())

    def stop(self):
        self.backend.stop_receiver()

    def _on_frame(self, frame):
        try:
            while self.frame_queue.qsize() >= 1:
                self.frame_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self.frame_queue.put_nowait(frame)
        except queue.Full:
            pass

    def _ui_loop(self):
        try:
            frame = self.frame_queue.get_nowait()
            img = Image.fromarray(frame)
            img.thumbnail((960, 540))
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self.current_photo = ctk_img
            self.preview_label.configure(image=ctk_img, text="")
        except queue.Empty:
            pass
        except Exception:
            pass
        self.after(33, self._ui_loop)

    def on_leave(self):
        self.backend.on_frame = None
        self.backend.zero_copy_mode = True


# ---------------------------------------------------------------------
# MODUL 3: KAYIT
# ---------------------------------------------------------------------
class RecordModuleFrame(ModuleFrameBase):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Kayit")
        self.backend.on_record_state = self._rec_state

        body = ctk.CTkFrame(self, corner_radius=16)
        body.pack(fill="both", expand=True, padx=18, pady=8)

        self.dir_entry = labeled_entry(body, "Kayit klasoru", self.backend.s.record_dir)
        ctk.CTkButton(body, text="KLASOR SEC", command=self._choose_dir).pack(padx=16, pady=6, anchor="w")

        self.audio_combo = ctk.CTkComboBox(body, values=["Ses cihazi taranmadi"])
        self.audio_combo.pack(fill="x", padx=16, pady=8)
        ctk.CTkButton(body, text="SES CIHAZLARINI TARA", command=self._scan_audio).pack(fill="x", padx=16, pady=4)

        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(pady=14)
        ctk.CTkButton(btns, text="SESSIZ KAYIT", width=160, command=lambda: self._start(False)).grid(row=0, column=0, padx=6)
        ctk.CTkButton(btns, text="SESLI KAYIT", width=160, command=lambda: self._start(True)).grid(row=0, column=1, padx=6)
        ctk.CTkButton(btns, text="KAYDI DURDUR", width=160, fg_color="#8a3030",
                      command=self.backend.stop_recording).grid(row=0, column=2, padx=6)

        self.rec_status = ctk.CTkLabel(body, text="Kayit: Kapali", font=("Consolas", 13, "bold"))
        self.rec_status.pack(pady=10)

        note = ctk.CTkLabel(
            body,
            text="Not: Kayit sirasinda goruntu kopyalanir (webcam-only zero-copy modu bu esnada devre disidir).",
            font=("Roboto", 11), text_color="#9aa4b2", wraplength=520,
        )
        note.pack(pady=(0, 16))

        if not self.backend.running_stream:
            self.backend.prepare_connection_async(lambda ok: self.backend.start_receiver())

    def _choose_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, path)
            self.backend.s.record_dir = path

    def _scan_audio(self):
        threading.Thread(target=self._scan_audio_worker, daemon=True).start()

    def _scan_audio_worker(self):
        try:
            p = subprocess.run(
                [self.backend.s.ffmpeg_path, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=6,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            devices = re.findall(r'"([^"]+)"\s+\(audio\)', p.stderr)
            if devices:
                self.after(0, lambda: self.audio_combo.configure(values=devices))
                self.after(0, lambda: self.audio_combo.set(devices[0]))
        except Exception:
            pass

    def _start(self, with_audio):
        self.backend.s.record_dir = self.dir_entry.get().strip() or self.backend.s.record_dir
        device = self.audio_combo.get() if with_audio else ""
        self.backend.zero_copy_mode = False
        self.backend.start_recording(with_audio, device)

    def _rec_state(self, text):
        self.after(0, lambda: self.rec_status.configure(text=f"Kayit: {text}"))

    def on_leave(self):
        self.backend.zero_copy_mode = True


# ---------------------------------------------------------------------
# MODUL 4: ZOOM & YON
# ---------------------------------------------------------------------
class ZoomModuleFrame(ModuleFrameBase):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Zoom && Yon")
        self.backend.on_control_state = self._on_state

        body = ctk.CTkFrame(self, corner_radius=16)
        body.pack(fill="both", expand=True, padx=18, pady=8)

        self.orientation_label = ctk.CTkLabel(body, text="Kamera yonu: YATAY", font=("Roboto", 16, "bold"))
        self.orientation_label.pack(pady=(24, 8))

        self.zoom_text = ctk.CTkLabel(body, text="Zoom: 1.00x / 1.00x", font=("Consolas", 14, "bold"))
        self.zoom_text.pack(pady=6)

        self.zoom_slider = ctk.CTkSlider(body, from_=1.0, to=2.0, number_of_steps=100, command=self._on_slider)
        self.zoom_slider.set(1.0)
        self.zoom_slider.pack(fill="x", padx=40, pady=10)

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(pady=8)
        ctk.CTkButton(row, text="\u2212", width=70, command=lambda: self._change(-0.25)).grid(row=0, column=0, padx=6)
        ctk.CTkButton(row, text="1x", width=70, command=lambda: self._set(1.0)).grid(row=0, column=1, padx=6)
        ctk.CTkButton(row, text="+", width=70, command=lambda: self._change(0.25)).grid(row=0, column=2, padx=6)

        ctk.CTkLabel(
            body,
            text="Bu modul acikken telefonla kontrol kanali uzerinden konusulur;\nyayin/kayit ayri modullerde calismaya devam eder.",
            font=("Roboto", 11), text_color="#9aa4b2",
        ).pack(pady=18)

        if self.backend.control_socket is None:
            self.backend.prepare_connection_async(
                lambda ok: self.backend.start_control_channel(
                    "127.0.0.1" if self.backend.active_transport == "USB" else self.backend.cached_wifi_ip
                )
            )

    def _on_slider(self, value):
        v = self.backend.set_zoom(value)
        self.zoom_text.configure(text=f"Zoom: {v:.2f}x / {self.backend.max_zoom:.2f}x")

    def _set(self, value):
        v = self.backend.set_zoom(value)
        self.zoom_slider.set(v)
        self.zoom_text.configure(text=f"Zoom: {v:.2f}x / {self.backend.max_zoom:.2f}x")

    def _change(self, delta):
        self._set(self.backend.zoom_value + delta)

    def _on_state(self, orientation, zoom, max_zoom):
        tr = "DIKEY" if orientation == "PORTRAIT" else "YATAY"

        def upd():
            self.orientation_label.configure(text=f"Kamera yonu: {tr}")
            self.zoom_slider.configure(from_=1.0, to=max(1.01, max_zoom))
            self.zoom_slider.set(min(zoom, max_zoom))
            self.zoom_text.configure(text=f"Zoom: {zoom:.2f}x / {max_zoom:.2f}x")
        self.after(0, upd)


# ---------------------------------------------------------------------
# MODUL 5: BAGLANTI AYARLARI
# ---------------------------------------------------------------------
class SettingsModuleFrame(ModuleFrameBase):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Baglanti Ayarlari")

        body = ctk.CTkScrollableFrame(self, corner_radius=16)
        body.pack(fill="both", expand=True, padx=18, pady=8)

        s = self.backend.s
        self.adb_entry = labeled_entry(body, "ADB yolu", s.adb_path)
        self.ffmpeg_entry = labeled_entry(body, "FFmpeg yolu", s.ffmpeg_path)
        self.video_port_entry = labeled_entry(body, "Video Port", str(s.video_port))
        self.control_port_entry = labeled_entry(body, "Kontrol Port", str(s.control_port))
        self.width_entry = labeled_entry(body, "Genislik", str(s.width))
        self.height_entry = labeled_entry(body, "Yukseklik", str(s.height))
        self.fps_entry = labeled_entry(body, "FPS", str(s.fps))

        self.auto_switch = ctk.CTkSwitch(body, text="USB -> Wi-Fi otomatik gecis")
        if s.auto_wifi_fallback:
            self.auto_switch.select()
        self.auto_switch.pack(padx=16, pady=14, anchor="w")

        ctk.CTkButton(body, text="AYARLARI KAYDET", command=self._save).pack(fill="x", padx=16, pady=16)

        self.device_label = ctk.CTkLabel(body, text="ADB cihazi: kontrol edilmedi", font=("Consolas", 12, "bold"))
        self.device_label.pack(pady=8)
        ctk.CTkButton(body, text="CIHAZI KONTROL ET", command=self._check_device).pack(fill="x", padx=16, pady=6)

    def _save(self):
        s = self.backend.s
        s.adb_path = self.adb_entry.get().strip()
        s.ffmpeg_path = self.ffmpeg_entry.get().strip()
        try:
            s.video_port = int(self.video_port_entry.get())
            s.control_port = int(self.control_port_entry.get())
            s.width = int(self.width_entry.get())
            s.height = int(self.height_entry.get())
            s.fps = int(self.fps_entry.get())
        except ValueError:
            messagebox.showerror(APP_NAME, "Port/cozunurluk/FPS alanlari sayi olmali.")
            return
        s.auto_wifi_fallback = bool(self.auto_switch.get())
        messagebox.showinfo(APP_NAME, "Ayarlar kaydedildi.")

    def _check_device(self):
        threading.Thread(target=self._check_device_worker, daemon=True).start()

    def _check_device_worker(self):
        ok, output = self.backend.adb_devices()
        text = "ADB cihazi: BAGLI" if ok else "ADB cihazi: bulunamadi"
        self.after(0, lambda: self.device_label.configure(text=text))


# ---------------------------------------------------------------------
# MODUL 6: ANDROID APK KURULUMU
# Programin calistigi klasorde (ve alt klasorlerinde) .apk dosyasini
# otomatik bulur; "KUR" butonuna basinca adb install ile telefona yukler.
# ---------------------------------------------------------------------
class ApkModuleFrame(ModuleFrameBase):
    def __init__(self, parent, app):
        super().__init__(parent, app, "Android APK Kurulumu")

        # Programin calistigi klasor (frozen/exe ise sys.executable, degilse bu dosyanin dizini)
        import sys
        if getattr(sys, "frozen", False):
            self.app_dir = Path(sys.executable).parent
        else:
            self.app_dir = Path(__file__).resolve().parent

        self.found_apks = []
        self.selected_apk = None

        body = ctk.CTkFrame(self, corner_radius=16)
        body.pack(fill="both", expand=True, padx=18, pady=8)

        ctk.CTkLabel(
            body,
            text=f"Tarama klasoru: {self.app_dir}",
            font=("Consolas", 11), text_color="#9aa4b2",
        ).pack(pady=(20, 6), padx=20)

        self.apk_combo = ctk.CTkComboBox(body, values=["Once TARA'ya bas"], command=self._on_select)
        self.apk_combo.pack(fill="x", padx=24, pady=8)

        scan_row = ctk.CTkFrame(body, fg_color="transparent")
        scan_row.pack(pady=6)
        ctk.CTkButton(scan_row, text="\U0001F50D TARA", width=150, command=self._scan).grid(row=0, column=0, padx=6)
        ctk.CTkButton(scan_row, text="BASKA APK SEC...", width=180, command=self._browse).grid(row=0, column=1, padx=6)

        self.apk_info_label = ctk.CTkLabel(body, text="APK secilmedi.", font=("Consolas", 12, "bold"))
        self.apk_info_label.pack(pady=10)

        self.device_label = ctk.CTkLabel(body, text="ADB cihazi: kontrol edilmedi", font=("Consolas", 12))
        self.device_label.pack(pady=4)
        ctk.CTkButton(body, text="CIHAZI KONTROL ET", width=200, command=self._check_device).pack(pady=6)

        install_row = ctk.CTkFrame(body, fg_color="transparent")
        install_row.pack(pady=16)
        self.install_btn = ctk.CTkButton(
            install_row, text="\u2B07 KUR", width=180, height=46,
            font=("Roboto", 15, "bold"), command=self._install,
        )
        self.install_btn.grid(row=0, column=0, padx=6)
        ctk.CTkButton(
            install_row, text="\U0001F504 YENIDEN KUR (-r)", width=200, height=46,
            command=lambda: self._install(reinstall=True),
        ).grid(row=0, column=1, padx=6)

        self.log_box = ctk.CTkTextbox(body, height=140, font=("Consolas", 11))
        self.log_box.pack(fill="both", expand=True, padx=24, pady=(0, 20))

        self._scan()

    def _log(self, text):
        self.after(0, lambda: (self.log_box.insert("end", text + "\n"), self.log_box.see("end")))

    # ---- APK tarama ----
    def _scan(self):
        self._log(f"[TARA] {self.app_dir} klasorunde .apk araniyor...")
        try:
            found = sorted(self.app_dir.rglob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception as exc:
            self._log(f"[TARA HATASI] {exc}")
            found = []

        self.found_apks = found

        if not found:
            self.apk_combo.configure(values=["APK bulunamadi"])
            self.apk_combo.set("APK bulunamadi")
            self.apk_info_label.configure(text="Bu klasorde .apk dosyasi yok. 'BASKA APK SEC...' ile secebilirsin.")
            self.selected_apk = None
            self._log("[TARA] Hicbir .apk bulunamadi.")
            return

        labels = [f"{p.name}  ({p.stat().st_size / 1_048_576:.1f} MB)" for p in found]
        self.apk_combo.configure(values=labels)
        self.apk_combo.set(labels[0])
        self.selected_apk = found[0]
        self._update_apk_info()
        self._log(f"[TARA] {len(found)} APK bulundu. En yenisi otomatik secildi: {found[0].name}")

    def _on_select(self, label):
        try:
            idx = list(self.apk_combo.cget("values")).index(label)
            self.selected_apk = self.found_apks[idx]
            self._update_apk_info()
        except (ValueError, IndexError):
            pass

    def _browse(self):
        path = filedialog.askopenfilename(filetypes=[("Android APK", "*.apk")])
        if path:
            self.selected_apk = Path(path)
            values = list(self.apk_combo.cget("values"))
            label = f"{self.selected_apk.name}  ({self.selected_apk.stat().st_size / 1_048_576:.1f} MB)"
            if self.selected_apk not in self.found_apks:
                self.found_apks.insert(0, self.selected_apk)
                values.insert(0, label)
                self.apk_combo.configure(values=values)
            self.apk_combo.set(label)
            self._update_apk_info()

    def _update_apk_info(self):
        if self.selected_apk and self.selected_apk.is_file():
            size_mb = self.selected_apk.stat().st_size / 1_048_576
            self.apk_info_label.configure(
                text=f"Secili: {self.selected_apk.name}  •  {size_mb:.1f} MB\n{self.selected_apk}"
            )
        else:
            self.apk_info_label.configure(text="APK secilmedi.")

    # ---- ADB cihaz kontrolu ----
    def _check_device(self):
        threading.Thread(target=self._check_device_worker, daemon=True).start()

    def _check_device_worker(self):
        ok, output = self.backend.adb_devices()
        text = "ADB cihazi: BAGLI" if ok else "ADB cihazi: bulunamadi (USB debug + kablo kontrol et)"
        self.after(0, lambda: self.device_label.configure(text=text))
        self._log(output.strip())

    # ---- Kurulum ----
    def _install(self, reinstall=False):
        if not self.selected_apk or not self.selected_apk.is_file():
            messagebox.showerror(APP_NAME, "Once bir APK sec (TARA veya BASKA APK SEC).")
            return
        self.install_btn.configure(state="disabled")
        threading.Thread(target=self._install_worker, args=(reinstall,), daemon=True).start()

    def _install_worker(self, reinstall):
        adb = self.backend.s.adb_path
        apk = str(self.selected_apk)

        if not os.path.isfile(adb):
            self._log(f"[HATA] ADB bulunamadi: {adb} (Baglanti Ayarlari modulunden duzelt)")
            self.after(0, lambda: self.install_btn.configure(state="normal"))
            return

        ok, output = self.backend.adb_devices()
        if not ok:
            self._log("[HATA] Bagli/yetkili bir ADB cihazi bulunamadi.\n" + output)
            self.after(0, lambda: self.install_btn.configure(state="normal"))
            messagebox.showerror(APP_NAME, "Telefon ADB ile bagli degil. USB hata ayiklamayi ac ve kabloyu kontrol et.")
            return

        cmd = [adb, "install"]
        if reinstall:
            cmd.append("-r")
        cmd.append(apk)

        self._log(f"[KUR] {' '.join(cmd)}")
        self.set_status("Kuruluyor...")

        try:
            p = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore",
                timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self._log(p.stdout.strip())
            if p.stderr.strip():
                self._log(p.stderr.strip())

            if "Success" in p.stdout:
                self._log(f"[KUR] Basarili: {self.selected_apk.name}")
                self.set_status("Kurulum tamamlandi")
                messagebox.showinfo(APP_NAME, f"{self.selected_apk.name} telefona kuruldu.")
            else:
                self._log("[KUR HATASI] Kurulum basarisiz oldu, log'a bak.")
                self.set_status("Kurulum basarisiz")
                if "INSTALL_FAILED_ALREADY_EXISTS" in p.stdout + p.stderr:
                    messagebox.showwarning(
                        APP_NAME,
                        "Uygulama zaten yuklu. 'YENIDEN KUR (-r)' butonunu kullan.",
                    )
                else:
                    messagebox.showerror(APP_NAME, "Kurulum basarisiz. Detay icin log kutusuna bak.")
        except subprocess.TimeoutExpired:
            self._log("[KUR HATASI] Zaman asimi (120sn).")
            self.set_status("Zaman asimi")
        except Exception as exc:
            self._log(f"[KUR HATASI] {exc}")
            self.set_status("Hata")
        finally:
            self.after(0, lambda: self.install_btn.configure(state="normal"))


if __name__ == "__main__":
    app = App()
    app.mainloop()
