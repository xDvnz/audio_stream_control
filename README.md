# Audio Stream Controller 🎙️

Sebuah sistem kendali *streaming* audio terpusat yang dirancang khusus untuk manajemen penyiaran suara di ruang kelas. Sistem ini menggunakan mikrokontroler ESP32, antarmuka Python, dan protokol komunikasi MQTT untuk memastikan pengiriman data yang ringan, latensi rendah, dan reliabilitas tinggi.

Proyek ini awalnya dikembangkan sebagai Tugas Akhir pada program studi Teknik Telekomunikasi di Politeknik Negeri Malang (Polinema).

## 🚀 Fitur Utama

* **Manajemen Ruang Kelas:** Mengelola dan mengarahkan *streaming* audio ke kelas-kelas yang spesifik.
* **Komunikasi MQTT:** Memanfaatkan protokol MQTT untuk pertukaran pesan kendali secara *real-time* antara *server* dan *node* ESP32.
* **Antarmuka Kontrol Python:** Menggunakan skrip `esp32.py` sebagai pusat kendali (*controller*) untuk mengatur aliran data.
* **Node Nirkabel ESP32:** Memanfaatkan fitur Wi-Fi pada ESP32 sebagai penerima mandiri di setiap titik ruang kelas.

## 🛠️ Perangkat & Teknologi

* **Perangkat Keras:** ESP32 Development Board, [Sebutkan modul audio/amplifier yang digunakan, misal: MAX98357A I2S], [Sebutkan tipe speaker].
* **Perangkat Lunak:** Python 3.x, Arduino IDE / PlatformIO.
* **Protokol & Pustaka:** MQTT (Message Queuing Telemetry Transport), [Sebutkan library Python yang digunakan, misal: paho-mqtt], [Sebutkan library ESP32].

## 📂 Struktur Repositori

* `esp32.py`: Skrip utama Python untuk mengontrol sistem dan mempublikasikan perintah MQTT.
* `esp32-audiostream/`: Direktori berisi *source code* (C++/Arduino) yang di-*flash* ke perangkat ESP32.
* `[Nama folder/file lain]`: [Penjelasan singkat, misalnya berisi skema rangkaian].

## ⚙️ Cara Instalasi & Penggunaan

### 1. Persiapan Perangkat Keras (ESP32)
1. Buka folder `esp32-audiostream` menggunakan Arduino IDE atau PlatformIO.
2. Sesuaikan kredensial Wi-Fi (`SSID` dan `PASSWORD`) serta alamat IP *Broker* MQTT pada *source code*.
3. *Compile* dan *Upload* kode ke board ESP32.
### 2. Persiapan Perangkat Lunak (Python)
1. Pastikan Python 3 sudah terinstal di sistem Anda.
2. Instal *library* yang dibutuhkan dengan menjalankan perintah:
   ```bash
   pip install -r requirements.txt

## 🖼️ Alat
![Tampak Depan](assets/depan.png)
