# 🎥 EBS Android Camera Suite

<p align="center"><strong>Android telefonu profesyonel bir kamera kaynağına dönüştüren EBS kamera ekosistemi</strong></p>

<p align="center">
<img src="https://img.shields.io/badge/Platform-Android%20%7C%20Windows-2ea44f?style=for-the-badge" alt="Platform">
<img src="https://img.shields.io/badge/Language-Python%20%7C%20Kotlin-3776AB?style=for-the-badge" alt="Language">
<img src="https://img.shields.io/badge/License-Apache%202.0-D22128?style=for-the-badge" alt="License">
<img src="https://img.shields.io/badge/Status-Active%20Development-orange?style=for-the-badge" alt="Status">
</p>

> 🚀 **EBS Android Camera Suite**, Android telefon kamerasını Windows üzerinde kullanılabilen düşük gecikmeli bir kamera kaynağına dönüştürmek için tasarlanmış Android + Windows çözümüdür.
>
> 🔒 Bu açık kaynak depoda **Desktop uygulaması ve bağımsız Android uygulaması** bulunur. **PRO v8 Builder bu repoya dahil değildir ve edilmeyecektir.**

## ✨ Özellikler

### 📱 Android Kamera Uygulaması
- 📷 **Camera2 API** ile kamera yakalama
- ⚡ **MediaCodec** ile donanımsal H.264 kodlama
- 🔌 **USB / ADB üzerinden TCP video aktarımı**
- 📡 **Wi-Fi / LAN üzerinden TCP aktarımı**
- 🎛️ Ayrı kamera kontrol kanalı
- 🔍 Uzaktan zoom kontrolü
- 🧭 Orientation / kamera durum bildirimi
- 🔄 Keyframe isteği ile decoder senkronizasyonu
- 🌐 **Browser / MJPEG yayın modu**
- 📺 **RTSP / IP Camera modu**
- 🌍 Yerel IPv4 keşfi
- 🔐 Kamera izin yönetimi
- 🖥️ Tam ekran kamera önizlemesi

### 🖥️ Windows Desktop Uygulaması
- 🎨 Modern **CustomTkinter** arayüz
- 🔌 USB / ADB bağlantısı
- 📡 Otomatik **USB → Wi-Fi fallback**
- ⚡ H.264 TCP alma + düşük gecikmeli FFmpeg çözümleme
- 🎥 **pyvirtualcam** ile sanal kamera çıkışı
- 👁️ İsteğe bağlı canlı önizleme
- 💾 MP4 kayıt
- 🎙️ İsteğe bağlı Windows DirectShow ses kaydı
- 🔍 Zoom kontrolü
- 🧭 Orientation / durum takibi
- 🔧 ADB cihaz teşhisi
- ⚙️ ADB ve FFmpeg yol ayarları
- 🎞️ Çözünürlük / FPS / port ayarları
- 🔄 Bağlantı gözetimi ve otomatik yeniden bağlanma

## 🧩 Sistem Mimarisi

```text
                    📱 ANDROID TELEFON
                           │
                    ┌──────▼──────┐
                    │   Camera2   │
                    └──────┬──────┘
                           │
                    MediaCodec / H.264
                           │
          ┌────────────────┼────────────────┐
          │                │                │
       🔌 USB/ADB       📡 Wi-Fi        🌐 LAN
          │                │                │
          └────────────────┼────────────────┘
                           │
                    TCP Video / Control
                           │
                    ┌──────▼──────┐
                    │   WINDOWS   │
                    │   DESKTOP   │
                    └──────┬──────┘
                           │
                    ⚡ FFmpeg Decode
                           │
             ┌─────────────┼─────────────┐
             │             │             │
        🎥 Virtual      👁️ Preview    💾 MP4
          Camera                       Record
             │
             ▼
      OBS / Zoom / Meet
      ve diğer kamera uygulamaları
```

## 🌐 Yayın Modları

### 🌍 Browser / MJPEG

```text
📱 Android Camera → HTTP :8080 → 🌐 Web Browser / MJPEG Client
```

### 📺 RTSP / IP Camera

```text
📱 Android Camera → RTSP :8554 → VLC / OBS / IP Camera Client
```

## 🔌 Varsayılan Portlar

| 🔧 Servis | 🔢 Port |
|---|---:|
| 🎥 Video TCP | `27183` |
| 🎛️ Control TCP | `27184` |
| 🌐 Browser / MJPEG | `8080` |
| 📺 RTSP | `8554` |

## 📂 Proje Yapısı

```text
EBS-Android-EBS-Camera-Suite/
│
├── 📱 android-app/          # Android Studio projesi
├── 🖥️ desktop/              # Windows Desktop uygulaması
├── 📚 docs/                 # Dokümantasyon
├── ⚙️ .github/              # GitHub Actions
├── 📦 requirements.txt      # Python bağımlılıkları
├── 📜 LICENSE               # Apache License 2.0
├── 📖 README.md
└── 🛡️ SECURITY.md
```

## 🐍 Python Desktop Kurulumu

Windows üzerinde Python 3.10+ önerilir.

### 1️⃣ Bağımlılıkları yükle

```bash
pip install -r requirements.txt
```

### 2️⃣ Desktop uygulamasını çalıştır

```python
python desktop/ebs_camera_suite.py
```

### 📦 Python bağımlılıkları

```python
customtkinter
Pillow
numpy
pyvirtualcam
```

> ℹ️ `tkinter`, `subprocess`, `threading`, `queue`, `socket`, `os`, `pathlib` gibi standart Python modülleri ayrıca kurulmaz.

## 📱 Android Build

Android projesini Android Studio ile açabilir veya Gradle üzerinden derleyebilirsiniz.

```bash
cd android-app
gradlew.bat :app:assembleDebug
```

APK çıktısı:

```text
android-app/app/build/outputs/apk/debug/app-debug.apk
```

### Gerekenler
- Android Studio / Android SDK
- Uyumlu JDK
- Android Platform Tools / ADB
- USB hata ayıklama yetkisi

## 🔗 Bağlantı Mantığı

### 🔌 USB / ADB

```text
Android
   │ USB
   ▼
ADB Port Forward
   ├── 27183 → 🎥 Video
   └── 27184 → 🎛️ Control
   │
   ▼
Windows Desktop
```

### 📡 Wi-Fi Fallback

```text
USB / ADB
   │
   ├── ✅ Başarılı → USB Camera
   │
   └── ❌ Başarısız → 📡 Wi-Fi → Camera Stream
```

## 🎯 Kullanım Alanları

- 🎥 OBS Studio için Android kamera
- 💻 Windows görüntülü görüşme uygulamalarında kamera
- 📺 RTSP / IP Camera senaryoları
- 🌐 Yerel ağ üzerinden browser kamera yayını
- 🎬 Düşük gecikmeli kamera aktarımı
- 💾 MP4 kayıt
- 🔍 Uzaktan zoom kontrolü
- 🧪 Kamera ve bağlantı testleri

## 🚫 PRO v8 Builder

Aşağıdaki eski Builder dosyası **bilinçli olarak repoya dahil edilmemiştir**:

```text
EBS_Huawei_Camera_Builder_PRO_v8_1_FIXED.py
```

❌ Builder repoya yüklenmeyecek.

✅ Android uygulaması bağımsız `android-app/` projesi olarak tutulacaktır.

## 🛡️ Güvenlik

HTTP, RTSP ve kontrol portlarını doğrudan Internet'e açmayın. Proje öncelikle güvenilir yerel ağ / USB bağlantısı kullanımını hedefler.

```text
⚠️ 27183 → Video
⚠️ 27184 → Control
⚠️ 8080  → MJPEG
⚠️ 8554  → RTSP
```

## 🤝 Katkıda Bulunma

```text
1. Fork
2. Yeni branch oluştur
3. Değişikliklerini yap
4. Test et
5. Pull Request gönder
```

Kod değişikliklerinde düşük gecikmeli kamera aktarım yolunun gereksiz kopyalama / preview işlemleriyle bozulmamasına dikkat edilmelidir.

## 📦 Release / APK

[📱 APK'yı İndir](https://github.com/ebubekirbastama/EBS-Android-EBS-Camera-Suite/raw/refs/heads/main/EBS-Android-EBS-Camera-Suite-v1.0.0.zip)

## 📜 Lisans

Bu proje **Apache License 2.0** altında lisanslanmıştır.

```text
Apache License 2.0
Copyright © 2026 EBS
```

Detaylar için [`LICENSE`](LICENSE) dosyasına bakabilirsiniz.

---



<p align="center"><strong>🎥 Capture · ⚡ Low Latency · 📡 Streaming · 🎛️ Control · 💾 Recording</strong></p>
<p align="center"><strong>Made with ❤️ by EBS</strong></p>
