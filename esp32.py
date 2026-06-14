import sys
import subprocess
import platform
import ssl
from PyQt5 import QtCore, QtGui, QtWidgets
import paho.mqtt.client as mqtt

# ============================================================
#  KONFIGURASI
# ============================================================
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "passwd": "",
    "db": "audio_control",
    "port": 3306,
    "charset": "utf8"
}

# MIC + ICECAST CONFIG
STREAM_CONFIG = {
    "icecast_server": "192.168.18.7",
    "icecast_port": 8000,
    "icecast_mount": "/live.mp3",
    "icecast_source_pass": "hackme",
    "bitrate": "128k",
    "volume": "0.0",
}

# MQTT CONFIGURATION - HiveMQ Cloud
MQTT_CONFIG = {
    "host": "c0099a6e70884169bfc6b2f482c29e2b.s1.eu.hivemq.cloud",
    "port": 8883,
    "username": "streamaudio",
    "password": "StreamAudio_2026",
    "use_tls": True,
    "ca_cert_path": "hivemq-cloud-ca.crt",
}

# Mode sumber audio
AUDIO_MODE_MIC = "mic"
AUDIO_MODE_LOOPBACK = "loopback"
AUDIO_MODE_MIX = "mix"


def get_connection():
    import pymysql
    return pymysql.connect(**DB_CONFIG)


def init_database():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
                CREATE TABLE IF NOT EXISTS grp (
                    id     INT AUTO_INCREMENT PRIMARY KEY,
                    nama   VARCHAR(100) NOT NULL,
                    status TINYINT(1) DEFAULT 0
                )
            """)
        cur.execute("""
                CREATE TABLE IF NOT EXISTS ruangan (
                    id      INT AUTO_INCREMENT PRIMARY KEY,
                    nama    VARCHAR(100) NOT NULL,
                    mac_id  VARCHAR(100) NOT NULL,
                    id_grp  INT,
                    FOREIGN KEY (id_grp) REFERENCES grp(id)
                        ON DELETE SET NULL ON UPDATE CASCADE
                )
            """)
        try:
            cur.execute("ALTER TABLE grp ADD COLUMN status TINYINT(1) DEFAULT 0")
        except Exception:
            pass
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        QtWidgets.QMessageBox.critical(
            None, "Koneksi Gagal",
            f"Tidak bisa konek ke MySQL XAMPP!\n\n{e}\n\n"
            "Pastikan:\n1. XAMPP MySQL sudah START\n2. Database 'audio_control' sudah dibuat"
        )
        return False


# ============================================================
#  TOGGLE SWITCH
# ============================================================
class ToggleSwitch(QtWidgets.QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(50, 26)
        self._anim = QtCore.QPropertyAnimation(self, b"_offset", self)
        self._anim.setDuration(150)
        self._offset = 4

    def _get_offset(self):
        return self.__offset

    def _set_offset(self, val):
        self.__offset = val
        self.update()

    __offset = 4
    _offset = QtCore.pyqtProperty(int, _get_offset, _set_offset)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setBrush(QtGui.QColor("#2196F3") if self.isChecked() else QtGui.QColor("#888888"))
        p.setPen(QtCore.Qt.NoPen)
        p.drawRoundedRect(0, 3, self.width(), self.height() - 6, 10, 10)
        p.setBrush(QtGui.QColor("white"))
        p.drawEllipse(self._offset, 2, self.height() - 4, self.height() - 4)
        p.end()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.isChecked():
            self._anim.setStartValue(4)
            self._anim.setEndValue(self.width() - self.height() + 2)
        else:
            self._anim.setStartValue(self.width() - self.height() + 2)
            self._anim.setEndValue(4)
        self._anim.start()

    def setChecked(self, checked):
        super().setChecked(checked)
        self.__offset = self.width() - self.height() + 2 if checked else 4
        self.update()


# ============================================================
#  AUTO-DETECT AUDIO DEVICES (PipeWire / PulseAudio)
# ============================================================
def detect_audio_sources():
    """Detect available audio sources using pactl (PipeWire/PulseAudio).
    Returns dict with 'mic' list, 'monitor' list, and 'default_source' string.
    """
    result = {"mic": [], "monitor": [], "default_source": ""}
    try:
        # Get default source
        proc = subprocess.run(
            ["pactl", "get-default-source"],
            capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0:
            result["default_source"] = proc.stdout.strip()

        # List all sources
        proc = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0:
            for line in proc.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    source_name = parts[1]
                    if ".monitor" in source_name:
                        result["monitor"].append(source_name)
                    else:
                        result["mic"].append(source_name)

        # Get friendly descriptions
        proc = subprocess.run(
            ["pactl", "list", "sources"],
            capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0:
            result["_descriptions"] = {}
            current_name = None
            for line in proc.stdout.split("\n"):
                line = line.strip()
                if line.startswith("Name:"):
                    current_name = line.split(":", 1)[1].strip()
                elif line.startswith("Description:") and current_name:
                    result["_descriptions"][current_name] = line.split(":", 1)[1].strip()

    except FileNotFoundError:
        print("⚠️ pactl not found, trying pw-cli...")
    except Exception as e:
        print(f"⚠️ Audio detection error: {e}")

    return result


# ============================================================
#  MAIN WINDOW
# ============================================================
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audio Control - Integrated System")
        self.resize(960, 640)

        self.sel_ruangan = None
        self.sel_group = None

        # Mode audio default
        self.audio_mode = AUDIO_MODE_MIC

        # Auto-detect audio devices
        self.audio_sources = detect_audio_sources()
        self.selected_mic = ""
        self.selected_monitor = ""

        # Set defaults from detected devices
        if self.audio_sources["mic"]:
            default_src = self.audio_sources["default_source"]
            if default_src in self.audio_sources["mic"]:
                self.selected_mic = default_src
            else:
                self.selected_mic = self.audio_sources["mic"][0]
        if self.audio_sources["monitor"]:
            self.selected_monitor = self.audio_sources["monitor"][0]

        self._build_ui()
        self._connect_signals()

        # MQTT Setup
        self.mqtt_client = None
        self.mqtt_connected = False
        self.published_status = {}
        self.device_status = {}

        # Streaming processes
        self.mic_publisher = None
        self.stream_relay = None
        self.is_streaming = False

        # Timer untuk debounce volume
        self.volume_timer = QtCore.QTimer()
        self.volume_timer.setSingleShot(True)
        self.volume_timer.timeout.connect(self._apply_volume_change)

        # Timer untuk reconnect MQTT
        self.mqtt_reconnect_timer = QtCore.QTimer()
        self.mqtt_reconnect_timer.setInterval(10000)
        self.mqtt_reconnect_timer.timeout.connect(self.reconnect_mqtt)

        self.setup_paths()
        self.setup_mqtt()

    # ── SETUP ─────────────────────────────────────────────────
    def setup_paths(self):
        system = platform.system()
        if system == "Windows":
            self.ffmpeg_path = r"C:\ffmpeg\bin\ffmpeg.exe"
        elif system == "Darwin":
            self.ffmpeg_path = "/usr/local/bin/ffmpeg"
        else:
            self.ffmpeg_path = "/usr/bin/ffmpeg"

        try:
            result = subprocess.run([self.ffmpeg_path, "-version"],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                print("✅ FFmpeg OK:", result.stdout.split('\n')[0])
            else:
                print("❌ FFmpeg bermasalah")
        except Exception:
            print("❌ FFmpeg tidak ditemukan di:", self.ffmpeg_path)

    def setup_mqtt(self):
        """Setup MQTT dengan TLS untuk HiveMQ Cloud"""
        try:
            self.mqtt_client = mqtt.Client(protocol=mqtt.MQTTv311)
            self.mqtt_client.username_pw_set(MQTT_CONFIG["username"], MQTT_CONFIG["password"])

            if MQTT_CONFIG["use_tls"]:
                self._setup_tls()

            self.mqtt_client.on_connect = self.on_mqtt_connect
            self.mqtt_client.on_disconnect = self.on_mqtt_disconnect
            self.mqtt_client.on_message = self.on_mqtt_message
            self.mqtt_client.on_publish = self.on_mqtt_publish

            self.mqtt_client.connect_async(MQTT_CONFIG["host"], MQTT_CONFIG["port"], 60)
            self.mqtt_client.loop_start()
            self.mqtt_reconnect_timer.start()

            print(f"🔄 Menghubungkan ke HiveMQ Cloud: {MQTT_CONFIG['host']}:{MQTT_CONFIG['port']}")
            self.statusBar().showMessage("Menghubungkan ke MQTT Server...")

        except Exception as e:
            print(f"❌ Gagal setup MQTT: {e}")

    def _setup_tls(self):
        """Setup TLS/SSL untuk koneksi MQTT"""
        try:
            import os
            ca_cert_path = MQTT_CONFIG.get("ca_cert_path", "hivemq-cloud-ca.crt")

            if os.path.exists(ca_cert_path):
                self.mqtt_client.tls_set(
                    ca_certs=ca_cert_path,
                    certfile=None,
                    keyfile=None,
                    cert_reqs=ssl.CERT_REQUIRED,
                    tls_version=ssl.PROTOCOL_TLSv1_2,
                    ciphers=None
                )
                print(f"✅ TLS configured with CA cert: {ca_cert_path}")
            else:
                print("⚠️ CA certificate not found, using system default certificates")
                self.mqtt_client.tls_set(
                    ca_certs=None,
                    certfile=None,
                    keyfile=None,
                    cert_reqs=ssl.CERT_REQUIRED,
                    tls_version=ssl.PROTOCOL_TLSv1_2
                )

            self.mqtt_client.tls_insecure_set(False)

        except Exception as e:
            print(f"❌ TLS setup failed: {e}")
            raise

    def on_mqtt_connect(self, client, userdata, flags, rc):
        """Callback saat MQTT terhubung"""
        if rc == 0:
            self.mqtt_connected = True
            self.mqtt_reconnect_timer.stop()
            print(f"✅ MQTT Connected successfully to HiveMQ Cloud!")
            self.statusBar().showMessage("✅ MQTT Connected - HiveMQ Cloud", 5000)

            # Subscribe ke berbagai topic
            self.mqtt_client.subscribe("audio/status/#", qos=1)
            self.mqtt_client.subscribe("audio/control/+/set", qos=1)
            self.mqtt_client.subscribe("audio/control/all", qos=1)
            self.mqtt_client.subscribe("audio/control/group/#", qos=1)
            self.mqtt_client.subscribe("audio/query/#", qos=1)
            
            print("📡 Subscribed to:")
            print("   - audio/status/#")
            print("   - audio/control/+/set")
            print("   - audio/control/all")
            print("   - audio/control/group/#")
            print("   - audio/query/#")

            self.mqtt_client.publish("audio/control/status", "online", qos=1, retain=True)

        else:
            error_messages = {
                1: "Connection refused - incorrect protocol version",
                2: "Connection refused - invalid client identifier",
                3: "Connection refused - server unavailable",
                4: "Connection refused - bad username or password",
                5: "Connection refused - not authorized"
            }
            error_msg = error_messages.get(rc, f"Unknown error code: {rc}")
            print(f"❌ MQTT Connection failed: {error_msg}")
            self.statusBar().showMessage(f"❌ MQTT Failed: {error_msg}", 5000)
            self.mqtt_connected = False
            self.mqtt_reconnect_timer.start()

    def on_mqtt_disconnect(self, client, userdata, rc):
        """Callback saat MQTT terputus"""
        self.mqtt_connected = False
        print(f"⚠️ MQTT Disconnected (rc: {rc})")
        self.statusBar().showMessage("⚠️ MQTT Disconnected - Trying to reconnect...", 3000)
        self.mqtt_reconnect_timer.start()

    def on_mqtt_publish(self, client, userdata, mid):
        """Callback saat publish berhasil"""
        print(f"📤 MQTT Published message ID: {mid}")

    def on_mqtt_message(self, client, userdata, msg):
        """Callback utama untuk menerima pesan MQTT"""
        topic = msg.topic
        try:
            payload = msg.payload.decode()
        except UnicodeDecodeError:
            payload = str(msg.payload)

        print(f"📥 MQTT Message - Topic: {topic}, Payload: {payload}")

        # ============================================================
        # HANDLE STATUS UPDATE DARI DEVICE (ESP32)
        # ============================================================
        if "status" in topic and "control" not in topic:
            parts = topic.split('/')
            if len(parts) >= 3:
                mac_id = parts[-1]
                self.device_status[mac_id] = payload
                print(f"✅ Device {mac_id} status updated: {payload}")
                
                if hasattr(self, 'halaman') and self.halaman.currentIndex() == 1:
                    self.load_dashboard_ruangan()

        # ============================================================
        # HANDLE CONTROL COMMAND (ON/OFF dari external)
        # ============================================================
        elif "control" in topic and "/set" in topic:
            parts = topic.split('/')
            if len(parts) >= 3:
                target = parts[-2]  # MAC ID atau identifier
                
                if payload.lower() == "on":
                    self._handle_mqtt_on(target)
                elif payload.lower() == "off":
                    self._handle_mqtt_off(target)
                elif payload.lower() == "toggle":
                    self._handle_mqtt_toggle(target)
                else:
                    print(f"⚠️ Unknown command: {payload}")

        # ============================================================
        # HANDLE BROADCAST COMMAND
        # ============================================================
        elif topic == "audio/control/all":
            if payload.lower() == "on":
                self._handle_mqtt_all_on()
            elif payload.lower() == "off":
                self._handle_mqtt_all_off()
            elif payload.lower() == "toggle":
                self._handle_mqtt_all_toggle()

        # ============================================================
        # HANDLE GROUP COMMAND
        # ============================================================
        elif "group" in topic:
            parts = topic.split('/')
            if len(parts) >= 4:
                try:
                    group_id = int(parts[-1])
                    if payload.lower() == "on":
                        self._handle_mqtt_group_on(group_id)
                    elif payload.lower() == "off":
                        self._handle_mqtt_group_off(group_id)
                except ValueError:
                    print(f"❌ Invalid group ID: {parts[-1]}")

        # ============================================================
        # HANDLE QUERY STATUS
        # ============================================================
        elif topic == "audio/query/status":
            self._handle_mqtt_query_status()

        # ============================================================
        # HANDLE STREAMING CONTROL
        # ============================================================
        elif topic == "audio/control/stream":
            if payload.lower() == "start":
                self.start_mic_to_icecast()
            elif payload.lower() == "stop":
                self.stop_all_streams()
            elif payload.lower() == "restart":
                self.start_mic_to_icecast(force_restart=True)

    # ============================================================
    #  MQTT COMMAND HANDLERS
    # ============================================================
    
    def _handle_mqtt_on(self, mac_id):
        """Handle MQTT ON command untuk device tertentu"""
        try:
            conn = get_connection()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT r.id, r.nama, r.mac_id, COALESCE(g.status, 0)
                FROM ruangan r
                LEFT JOIN grp g ON r.id_grp = g.id
                WHERE r.mac_id = %s
            """, (mac_id,))
            
            row = cur.fetchone()
            
            if row:
                room_id, room_name, device_mac, current_status = row
                
                if current_status == 0:
                    cur.execute("""
                        UPDATE grp g
                        INNER JOIN ruangan r ON r.id_grp = g.id
                        SET g.status = 1
                        WHERE r.mac_id = %s
                    """, (mac_id,))
                    conn.commit()
                    
                    print(f"✅ Device {room_name} ({mac_id}) turned ON via MQTT")
                    self.published_status[mac_id] = 1
                    
                    # Publish ke device ESP32
                    self.mqtt_client.publish(f"audio/control/{mac_id}", "on", qos=1)
                    
                    if hasattr(self, 'halaman') and self.halaman.currentIndex() == 1:
                        self.load_dashboard_ruangan()
                    
                    self.mqtt_client.publish(f"audio/response/{mac_id}", 
                                            f"ON - {room_name}", qos=1)
                    self._check_and_restart_stream()
                else:
                    print(f"ℹ️ Device {room_name} already ON")
                    self.mqtt_client.publish(f"audio/response/{mac_id}", 
                                            "ALREADY_ON", qos=1)
            else:
                print(f"❌ Device with MAC {mac_id} not found")
                self.mqtt_client.publish(f"audio/response/{mac_id}", 
                                        "DEVICE_NOT_FOUND", qos=1)
                
            cur.close()
            conn.close()
            
        except Exception as e:
            print(f"❌ Error handling MQTT ON: {e}")
            self.mqtt_client.publish(f"audio/response/{mac_id}", 
                                    f"ERROR: {str(e)}", qos=1)

    def _handle_mqtt_off(self, mac_id):
        """Handle MQTT OFF command untuk device tertentu"""
        try:
            conn = get_connection()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT r.id, r.nama, r.mac_id, COALESCE(g.status, 0)
                FROM ruangan r
                LEFT JOIN grp g ON r.id_grp = g.id
                WHERE r.mac_id = %s
            """, (mac_id,))
            
            row = cur.fetchone()
            
            if row:
                room_id, room_name, device_mac, current_status = row
                
                if current_status == 1:
                    cur.execute("""
                        UPDATE grp g
                        INNER JOIN ruangan r ON r.id_grp = g.id
                        SET g.status = 0
                        WHERE r.mac_id = %s
                    """, (mac_id,))
                    conn.commit()
                    
                    print(f"✅ Device {room_name} ({mac_id}) turned OFF via MQTT")
                    self.published_status[mac_id] = 0
                    
                    # Publish ke device ESP32
                    self.mqtt_client.publish(f"audio/control/{mac_id}", "off", qos=1)
                    
                    if hasattr(self, 'halaman') and self.halaman.currentIndex() == 1:
                        self.load_dashboard_ruangan()
                    
                    self.mqtt_client.publish(f"audio/response/{mac_id}", 
                                            f"OFF - {room_name}", qos=1)
                    self._check_and_restart_stream()
                else:
                    print(f"ℹ️ Device {room_name} already OFF")
                    self.mqtt_client.publish(f"audio/response/{mac_id}", 
                                            "ALREADY_OFF", qos=1)
            else:
                print(f"❌ Device with MAC {mac_id} not found")
                self.mqtt_client.publish(f"audio/response/{mac_id}", 
                                        "DEVICE_NOT_FOUND", qos=1)
                
            cur.close()
            conn.close()
            
        except Exception as e:
            print(f"❌ Error handling MQTT OFF: {e}")
            self.mqtt_client.publish(f"audio/response/{mac_id}", 
                                    f"ERROR: {str(e)}", qos=1)

    def _handle_mqtt_toggle(self, mac_id):
        """Handle MQTT TOGGLE command"""
        try:
            conn = get_connection()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT COALESCE(g.status, 0)
                FROM ruangan r
                LEFT JOIN grp g ON r.id_grp = g.id
                WHERE r.mac_id = %s
            """, (mac_id,))
            
            row = cur.fetchone()
            cur.close()
            conn.close()
            
            if row:
                if row[0] == 1:
                    self._handle_mqtt_off(mac_id)
                else:
                    self._handle_mqtt_on(mac_id)
                    
        except Exception as e:
            print(f"❌ Error handling MQTT TOGGLE: {e}")

    def _handle_mqtt_all_on(self):
        """Handle MQTT command untuk menyalakan semua device"""
        try:
            conn = get_connection()
            cur = conn.cursor()
            
            cur.execute("UPDATE grp SET status = 1")
            conn.commit()
            
            cur.execute("SELECT mac_id FROM ruangan")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            
            for row in rows:
                mac_id = row[0]
                self.published_status[mac_id] = 1
                self.mqtt_client.publish(f"audio/control/{mac_id}", "on", qos=1)
            
            print(f"✅ All devices turned ON via MQTT - {len(rows)} devices")
            
            if hasattr(self, 'halaman') and self.halaman.currentIndex() == 1:
                self.load_dashboard_ruangan()
            
            self.start_mic_to_icecast()
            self.mqtt_client.publish("audio/response/all", f"ALL_ON - {len(rows)} devices", qos=1)
            
        except Exception as e:
            print(f"❌ Error handling ALL ON: {e}")

    def _handle_mqtt_all_off(self):
        """Handle MQTT command untuk mematikan semua device"""
        try:
            conn = get_connection()
            cur = conn.cursor()
            
            cur.execute("UPDATE grp SET status = 0")
            conn.commit()
            
            cur.execute("SELECT mac_id FROM ruangan")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            
            for row in rows:
                mac_id = row[0]
                self.published_status[mac_id] = 0
                self.mqtt_client.publish(f"audio/control/{mac_id}", "off", qos=1)
            
            print(f"✅ All devices turned OFF via MQTT - {len(rows)} devices")
            
            if hasattr(self, 'halaman') and self.halaman.currentIndex() == 1:
                self.load_dashboard_ruangan()
            
            self.stop_all_streams()
            self.mqtt_client.publish("audio/response/all", f"ALL_OFF - {len(rows)} devices", qos=1)
            
        except Exception as e:
            print(f"❌ Error handling ALL OFF: {e}")

    def _handle_mqtt_all_toggle(self):
        """Handle MQTT command untuk toggle semua device"""
        if any(self.published_status.values()):
            self._handle_mqtt_all_off()
        else:
            self._handle_mqtt_all_on()

    def _handle_mqtt_group_on(self, group_id):
        """Handle MQTT command untuk menyalakan group tertentu"""
        try:
            conn = get_connection()
            cur = conn.cursor()
            
            cur.execute("UPDATE grp SET status = 1 WHERE id = %s", (group_id,))
            conn.commit()
            
            cur.execute("""
                SELECT r.mac_id 
                FROM ruangan r
                WHERE r.id_grp = %s
            """, (group_id,))
            
            rows = cur.fetchall()
            cur.close()
            conn.close()
            
            for row in rows:
                mac_id = row[0]
                self.published_status[mac_id] = 1
                self.mqtt_client.publish(f"audio/control/{mac_id}", "on", qos=1)
            
            print(f"✅ Group {group_id} turned ON via MQTT - {len(rows)} devices")
            
            if hasattr(self, 'halaman') and self.halaman.currentIndex() == 1:
                self.load_dashboard_ruangan()
            
            self._check_and_restart_stream()
            self.mqtt_client.publish(f"audio/response/group/{group_id}", 
                                    f"ON - {len(rows)} devices", qos=1)
            
        except Exception as e:
            print(f"❌ Error handling GROUP ON: {e}")

    def _handle_mqtt_group_off(self, group_id):
        """Handle MQTT command untuk mematikan group tertentu"""
        try:
            conn = get_connection()
            cur = conn.cursor()
            
            cur.execute("UPDATE grp SET status = 0 WHERE id = %s", (group_id,))
            conn.commit()
            
            cur.execute("""
                SELECT r.mac_id 
                FROM ruangan r
                WHERE r.id_grp = %s
            """, (group_id,))
            
            rows = cur.fetchall()
            cur.close()
            conn.close()
            
            for row in rows:
                mac_id = row[0]
                self.published_status[mac_id] = 0
                self.mqtt_client.publish(f"audio/control/{mac_id}", "off", qos=1)
            
            print(f"✅ Group {group_id} turned OFF via MQTT - {len(rows)} devices")
            
            if hasattr(self, 'halaman') and self.halaman.currentIndex() == 1:
                self.load_dashboard_ruangan()
            
            self._check_and_restart_stream()
            self.mqtt_client.publish(f"audio/response/group/{group_id}", 
                                    f"OFF - {len(rows)} devices", qos=1)
            
        except Exception as e:
            print(f"❌ Error handling GROUP OFF: {e}")

    def _handle_mqtt_query_status(self):
        """Handle query status dari semua device"""
        try:
            conn = get_connection()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT r.mac_id, r.nama, COALESCE(g.status, 0)
                FROM ruangan r
                LEFT JOIN grp g ON r.id_grp = g.id
                ORDER BY r.id
            """)
            
            rows = cur.fetchall()
            cur.close()
            conn.close()
            
            for row in rows:
                mac_id, room_name, status = row
                status_text = "ON" if status else "OFF"
                self.mqtt_client.publish(f"audio/status/{mac_id}", 
                                        status_text, qos=1, retain=True)
            
            print(f"✅ Status query responded for {len(rows)} devices")
            self.mqtt_client.publish("audio/response/status", 
                                    f"QUERY_COMPLETE - {len(rows)} devices", qos=1)
            
        except Exception as e:
            print(f"❌ Error handling status query: {e}")

    def _check_and_restart_stream(self):
        """Cek apakah ada device yang ON, jika ada restart stream"""
        if any(self.published_status.values()):
            if not self.is_streaming:
                self.start_mic_to_icecast()
            else:
                self.start_mic_to_icecast(force_restart=True)
        else:
            if self.is_streaming:
                self.stop_all_streams()

    def reconnect_mqtt(self):
        """Reconnect MQTT jika terputus"""
        if not self.mqtt_connected and self.mqtt_client:
            try:
                print("🔄 Attempting to reconnect MQTT...")
                self.mqtt_client.reconnect()
            except Exception as e:
                print(f"❌ MQTT reconnect failed: {e}")

    # ── BUILD UI (LANJUTAN) ─────────────────────────────────
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        sidebar = QtWidgets.QWidget()
        sidebar.setFixedWidth(190)
        sidebar.setStyleSheet("background-color: #f0f0f0; border-right: 1px solid #ddd;")
        sb_lay = QtWidgets.QVBoxLayout(sidebar)
        sb_lay.setContentsMargins(16, 20, 16, 20)
        sb_lay.setSpacing(0)

        title = QtWidgets.QLabel("Audio Control")
        title.setStyleSheet("font-size: 14pt; font-weight: bold; color: #222; margin-bottom: 20px;")
        sb_lay.addWidget(title)
        sb_lay.addSpacing(16)

        menu_style = """
            QPushButton {
                background: transparent; color: #333; border: none;
                text-align: left; padding: 10px 4px; font-size: 10pt;
            }
            QPushButton:hover { color: #000; font-weight: bold; }
            QPushButton:pressed { color: #2196F3; }
        """
        self.btn_dash_group = QtWidgets.QPushButton("Dashboard Group")
        self.btn_dash_ruangan = QtWidgets.QPushButton("Dashboard Ruangan")
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet("color: #ccc; margin: 8px 0;")
        self.btn_set_ruangan = QtWidgets.QPushButton("Setting Ruangan")
        self.btn_set_group = QtWidgets.QPushButton("Setting Group")

        for btn in [self.btn_dash_group, self.btn_dash_ruangan,
                    self.btn_set_ruangan, self.btn_set_group]:
            btn.setStyleSheet(menu_style)
            btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
            sb_lay.addWidget(btn)

        sb_lay.insertWidget(3, sep)

        # MQTT Status Indicator
        sb_lay.addSpacing(16)
        sep3 = QtWidgets.QFrame()
        sep3.setFrameShape(QtWidgets.QFrame.HLine)
        sep3.setStyleSheet("color: #ccc; margin: 4px 0;")
        sb_lay.addWidget(sep3)
        sb_lay.addSpacing(8)

        self.mqtt_status_label = QtWidgets.QLabel("📡 MQTT: Connecting...")
        self.mqtt_status_label.setStyleSheet("font-size: 8pt; color: #666; padding: 4px;")
        self.mqtt_status_label.setWordWrap(True)
        sb_lay.addWidget(self.mqtt_status_label)

        self.mqtt_status_timer = QtCore.QTimer()
        self.mqtt_status_timer.setInterval(1000)
        self.mqtt_status_timer.timeout.connect(self.update_mqtt_status_display)
        self.mqtt_status_timer.start()

        # Audio Source Panel
        sb_lay.addSpacing(16)
        sep2 = QtWidgets.QFrame()
        sep2.setFrameShape(QtWidgets.QFrame.HLine)
        sep2.setStyleSheet("color: #ccc; margin: 4px 0;")
        sb_lay.addWidget(sep2)
        sb_lay.addSpacing(8)

        src_label = QtWidgets.QLabel("🎙 Sumber Audio")
        src_label.setStyleSheet("font-size: 9pt; font-weight: bold; color: #555;")
        sb_lay.addWidget(src_label)
        sb_lay.addSpacing(6)

        combo_style = (
            "QComboBox { background: white; color: #222; padding: 4px;"
            " border: 1px solid #ccc; border-radius: 4px; font-size: 8pt; }"
            " QComboBox::drop-down { border: none; }"
            " QComboBox QAbstractItemView { background: white; color: #222;"
            " selection-background-color: #e3f2fd; selection-color: black; }"
        )

        # Mode selector
        self.combo_audio_mode = QtWidgets.QComboBox()
        self.combo_audio_mode.addItem("🎤 Microphone", AUDIO_MODE_MIC)
        self.combo_audio_mode.addItem("💻 System Audio", AUDIO_MODE_LOOPBACK)
        self.combo_audio_mode.addItem("🎛️ Mic + System Mix", AUDIO_MODE_MIX)
        self.combo_audio_mode.setStyleSheet(combo_style)
        sb_lay.addWidget(self.combo_audio_mode)
        sb_lay.addSpacing(4)

        # Device selector
        dev_label = QtWidgets.QLabel("🔌 Input Device")
        dev_label.setStyleSheet("font-size: 8pt; color: #666;")
        sb_lay.addWidget(dev_label)

        self.combo_audio_device = QtWidgets.QComboBox()
        self.combo_audio_device.setStyleSheet(combo_style)
        self.combo_audio_device.setMaximumWidth(170)
        sb_lay.addWidget(self.combo_audio_device)

        # Populate device list based on default mode
        self._populate_device_combo()

        # Refresh button
        self.btn_refresh_audio = QtWidgets.QPushButton("🔄 Refresh Devices")
        self.btn_refresh_audio.setStyleSheet(
            "QPushButton { background: #e8e8e8; color: #333; border: none;"
            " border-radius: 4px; padding: 4px 8px; font-size: 8pt; }"
            " QPushButton:hover { background: #ddd; }"
        )
        self.btn_refresh_audio.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        sb_lay.addWidget(self.btn_refresh_audio)

        sb_lay.addSpacing(10)

        vol_lbl = QtWidgets.QLabel("🔊 Volume Gain")
        vol_lbl.setStyleSheet("font-size: 9pt; font-weight: bold; color: #555;")
        sb_lay.addWidget(vol_lbl)

        self.vol_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.vol_slider.setMinimum(0)
        self.vol_slider.setMaximum(300)
        self.vol_slider.setValue(0)
        self.vol_slider.setStyleSheet("""
            QSlider::groove:horizontal { height: 4px; background: #ccc; border-radius: 2px; }
            QSlider::handle:horizontal { width: 14px; height: 14px; margin: -5px 0;
                                         background: #2196F3; border-radius: 7px; }
            QSlider::sub-page:horizontal { background: #2196F3; border-radius: 2px; }
        """)
        sb_lay.addWidget(self.vol_slider)

        vol_input_lay = QtWidgets.QHBoxLayout()
        vol_input_lay.setSpacing(4)
        vol_prefix = QtWidgets.QLabel("Vol:")
        vol_prefix.setStyleSheet("font-size: 8pt; color: #888;")
        self.vol_spinbox = QtWidgets.QDoubleSpinBox()
        self.vol_spinbox.setRange(0.0, 300.0)
        self.vol_spinbox.setSingleStep(5.0)
        self.vol_spinbox.setDecimals(1)
        self.vol_spinbox.setValue(0.0)
        self.vol_spinbox.setSuffix(" %")
        self.vol_spinbox.setStyleSheet(
            "QDoubleSpinBox { background: white; color: #222; border: 1px solid #ccc;"
            " border-radius: 4px; padding: 2px 4px; font-size: 8pt; }"
            " QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {"
            " width: 12px; border: none; }"
        )
        self.vol_spinbox.setFixedWidth(70)
        vol_input_lay.addWidget(vol_prefix)
        vol_input_lay.addWidget(self.vol_spinbox)
        vol_input_lay.addStretch()
        sb_lay.addLayout(vol_input_lay)

        sb_lay.addSpacing(10)

        desc = self.audio_sources.get("_descriptions", {})
        mic_desc = desc.get(self.selected_mic, self.selected_mic) if self.selected_mic else "None"
        self.lbl_device_info = QtWidgets.QLabel(f"Device:\n{mic_desc}")
        self.lbl_device_info.setStyleSheet(
            "font-size: 8pt; color: #999; background: #e8e8e8; "
            "border-radius: 4px; padding: 4px 6px;"
        )
        self.lbl_device_info.setWordWrap(True)
        sb_lay.addWidget(self.lbl_device_info)

        sb_lay.addStretch()
        root.addWidget(sidebar)

        # Halaman utama
        self.halaman = QtWidgets.QStackedWidget()
        self.halaman.setStyleSheet("""
            QStackedWidget { background-color: #f5f5f5; }
            QLabel { color: #222; }
            QLineEdit {
                color: #222; background: white;
                padding: 4px; border: 1px solid #ccc; border-radius: 4px;
            }
            QComboBox {
                color: #222; background: white;
                padding: 4px; border: 1px solid #ccc; border-radius: 4px;
            }
            QComboBox QAbstractItemView {
                color: #222; background: white;
                selection-background-color: #e3f2fd; selection-color: black;
            }
            QListWidget { color: #222; background: white; }
            QListWidget::item { color: #222; }
            QListWidget::item:selected { background: #e3f2fd; color: black; }
        """)
        root.addWidget(self.halaman, stretch=1)

        self._build_page_dash_group()
        self._build_page_dash_ruangan()
        self._build_page_set_ruangan()
        self._build_page_set_group()

        self.halaman.setCurrentIndex(0)

    def update_mqtt_status_display(self):
        """Update MQTT status indicator"""
        if self.mqtt_connected:
            self.mqtt_status_label.setText("✅ MQTT: Connected to HiveMQ")
            self.mqtt_status_label.setStyleSheet("font-size: 8pt; color: #27ae60; padding: 4px;")
        else:
            self.mqtt_status_label.setText("❌ MQTT: Disconnected")
            self.mqtt_status_label.setStyleSheet("font-size: 8pt; color: #e74c3c; padding: 4px;")

    # ── PAGE BUILD METHODS ─────────────────────────────────
    def _build_page_dash_group(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        lbl = QtWidgets.QLabel("Dashboard Group")
        lbl.setStyleSheet("font-size: 16pt; font-weight: bold; color: #222;")
        lay.addWidget(lbl)

        card = QtWidgets.QFrame()
        card.setStyleSheet("QFrame { background: white; border: 1px solid #ddd; border-radius: 8px; }")
        card_lay = QtWidgets.QVBoxLayout(card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)

        header = QtWidgets.QWidget()
        header.setStyleSheet("background: transparent; border-bottom: 1px solid #eee;")
        header.setFixedHeight(36)
        h_lay = QtWidgets.QHBoxLayout(header)
        h_lay.setContentsMargins(16, 0, 16, 0)
        for text, stretch, align in [
            ("ID", 1, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
            ("Nama Grup", 6, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
            ("ON / OFF", 2, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter),
        ]:
            lh = QtWidgets.QLabel(text)
            lh.setAlignment(align)
            lh.setStyleSheet("font-weight: bold; color: #555; font-size: 9pt;")
            h_lay.addWidget(lh, stretch)
        card_lay.addWidget(header)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.grp_list_widget = QtWidgets.QWidget()
        self.grp_list_widget.setStyleSheet("background: transparent;")
        self.grp_list_layout = QtWidgets.QVBoxLayout(self.grp_list_widget)
        self.grp_list_layout.setContentsMargins(0, 0, 0, 0)
        self.grp_list_layout.setSpacing(0)
        self.grp_list_layout.addStretch()
        scroll.setWidget(self.grp_list_widget)
        card_lay.addWidget(scroll)
        lay.addWidget(card, stretch=1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        self.publish = QtWidgets.QPushButton("Publish")
        self.publish.setFixedSize(100, 36)
        self.publish.setStyleSheet("""
            QPushButton { background-color: #aaff00; color: black; font-weight: bold;
                          font-size: 11pt; border-radius: 6px; border: none; }
            QPushButton:hover { background-color: #88dd00; }
        """)
        self.publish.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        btn_row.addWidget(self.publish)
        lay.addLayout(btn_row)
        self.halaman.addWidget(page)

    def _build_page_dash_ruangan(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        lbl = QtWidgets.QLabel("Dashboard Ruangan")
        lbl.setStyleSheet("font-size: 16pt; font-weight: bold; color: #222;")
        lay.addWidget(lbl)

        card = QtWidgets.QFrame()
        card.setStyleSheet("QFrame { background: white; border: 1px solid #ddd; border-radius: 8px; }")
        card_lay = QtWidgets.QVBoxLayout(card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)

        header = QtWidgets.QWidget()
        header.setFixedHeight(36)
        header.setStyleSheet("background: transparent; border-bottom: 1px solid #eee;")
        h_lay = QtWidgets.QHBoxLayout(header)
        h_lay.setContentsMargins(16, 0, 16, 0)
        for text, stretch, align in [
            ("ID", 1, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
            ("Nama Ruangan", 3, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
            ("Nama Group", 3, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
            ("MAC ID", 3, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
            ("Status", 1, QtCore.Qt.AlignCenter | QtCore.Qt.AlignVCenter),
            ("Device", 1, QtCore.Qt.AlignCenter | QtCore.Qt.AlignVCenter),
        ]:
            lh = QtWidgets.QLabel(text)
            lh.setAlignment(align)
            lh.setStyleSheet("font-weight: bold; color: #555; font-size: 9pt;")
            h_lay.addWidget(lh, stretch)
        card_lay.addWidget(header)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.ruangan_list_widget = QtWidgets.QWidget()
        self.ruangan_list_widget.setStyleSheet("background: transparent;")
        self.ruangan_list_layout = QtWidgets.QVBoxLayout(self.ruangan_list_widget)
        self.ruangan_list_layout.setContentsMargins(0, 0, 0, 0)
        self.ruangan_list_layout.setSpacing(0)
        self.ruangan_list_layout.addStretch()
        scroll.setWidget(self.ruangan_list_widget)
        card_lay.addWidget(scroll)
        lay.addWidget(card, stretch=1)

        self.halaman.addWidget(page)

    def _build_page_set_ruangan(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        lbl = QtWidgets.QLabel("Setting Ruangan")
        lbl.setStyleSheet("font-size: 16pt; font-weight: bold; color: #222;")
        lay.addWidget(lbl)

        form = QtWidgets.QHBoxLayout()
        form.setSpacing(10)

        col1 = QtWidgets.QVBoxLayout()
        col1.addWidget(QtWidgets.QLabel("Group:"))
        self.pilihgroup = QtWidgets.QComboBox()
        self.pilihgroup.setStyleSheet(
            "background: white; color: #222; padding: 4px; border: 1px solid #ccc; border-radius: 4px;")
        self.pilihgroup.setMinimumWidth(160)
        col1.addWidget(self.pilihgroup)
        form.addLayout(col1)

        col2 = QtWidgets.QVBoxLayout()
        col2.addWidget(QtWidgets.QLabel("Nama Ruangan:"))
        self.editnama = QtWidgets.QLineEdit()
        self.editnama.setStyleSheet(
            "background: white; padding: 4px; border: 1px solid #ccc; border-radius: 4px;")
        self.editnama.setPlaceholderText("Nama ruangan...")
        col2.addWidget(self.editnama)
        form.addLayout(col2, stretch=1)

        col3 = QtWidgets.QVBoxLayout()
        col3.addWidget(QtWidgets.QLabel("MAC ID:"))
        self.editmacid = QtWidgets.QLineEdit()
        self.editmacid.setStyleSheet(
            "background: white; padding: 4px; border: 1px solid #ccc; border-radius: 4px;")
        self.editmacid.setPlaceholderText("xx:xx:xx:xx:xx:xx")
        col3.addWidget(self.editmacid)
        form.addLayout(col3, stretch=1)

        col4 = QtWidgets.QVBoxLayout()
        col4.addWidget(QtWidgets.QLabel(""))
        r1 = QtWidgets.QHBoxLayout()
        r2 = QtWidgets.QHBoxLayout()
        self.simpanruangan = self._make_btn("Simpan", "#00c800", "black")
        self.hapusruangan = self._make_btn("Hapus", "#ff5050", "white")
        self.updateruangan = self._make_btn("Update", "#0096ff", "white")
        self.refreshruangan = self._make_btn("Refresh", "#b0b0b0", "black")
        r1.addWidget(self.simpanruangan)
        r1.addWidget(self.hapusruangan)
        r2.addWidget(self.updateruangan)
        r2.addWidget(self.refreshruangan)
        col4.addLayout(r1)
        col4.addLayout(r2)
        form.addLayout(col4)
        lay.addLayout(form)

        self.listruangan = QtWidgets.QListWidget()
        self.listruangan.setStyleSheet("""
            QListWidget { background: white; border: 1px solid #ddd; border-radius: 8px; }
            QListWidget::item { padding: 8px; border-bottom: 1px solid #f0f0f0; color: #222; }
            QListWidget::item:selected { background: #e3f2fd; color: black; }
        """)
        lay.addWidget(self.listruangan, stretch=1)
        self.halaman.addWidget(page)

    def _build_page_set_group(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        lbl = QtWidgets.QLabel("Setting Group")
        lbl.setStyleSheet("font-size: 16pt; font-weight: bold; color: #222;")
        lay.addWidget(lbl)

        form = QtWidgets.QHBoxLayout()
        form.setSpacing(10)

        col1 = QtWidgets.QVBoxLayout()
        col1.addWidget(QtWidgets.QLabel("Nama Group:"))
        self.editgroup = QtWidgets.QLineEdit()
        self.editgroup.setStyleSheet(
            "background: white; padding: 4px; border: 1px solid #ccc; border-radius: 4px;")
        self.editgroup.setPlaceholderText("Nama group...")
        col1.addWidget(self.editgroup)
        form.addLayout(col1, stretch=1)

        col2 = QtWidgets.QVBoxLayout()
        col2.addWidget(QtWidgets.QLabel(""))
        r1 = QtWidgets.QHBoxLayout()
        r2 = QtWidgets.QHBoxLayout()
        self.simpangroup = self._make_btn("Simpan", "#00c800", "black")
        self.hapusgroup = self._make_btn("Hapus", "#ff5050", "white")
        self.updategroup = self._make_btn("Update", "#0096ff", "white")
        self.refreshgroup = self._make_btn("Refresh", "#b0b0b0", "black")
        r1.addWidget(self.simpangroup)
        r1.addWidget(self.hapusgroup)
        r2.addWidget(self.updategroup)
        r2.addWidget(self.refreshgroup)
        col2.addLayout(r1)
        col2.addLayout(r2)
        form.addLayout(col2)
        lay.addLayout(form)

        self.listgroup = QtWidgets.QListWidget()
        self.listgroup.setStyleSheet("""
            QListWidget { background: white; border: 1px solid #ddd; border-radius: 8px; }
            QListWidget::item { padding: 8px; border-bottom: 1px solid #f0f0f0; color: #222; }
            QListWidget::item:selected { background: #e3f2fd; color: black; }
        """)
        lay.addWidget(self.listgroup, stretch=1)
        self.halaman.addWidget(page)

    def _make_btn(self, text, bg, fg):
        btn = QtWidgets.QPushButton(text)
        btn.setStyleSheet(f"""
            QPushButton {{ background-color: {bg}; color: {fg}; border: none;
                           border-radius: 4px; padding: 5px 12px; font-size: 9pt; }}
            QPushButton:hover {{ opacity: 0.85; }}
        """)
        btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        return btn

    def _add_group_row(self, grp_id, grp_nama, grp_status):
        row = QtWidgets.QWidget()
        row.setFixedHeight(44)
        row.setStyleSheet("""
            QWidget { background: white; border-bottom: 1px solid #f0f0f0; }
            QWidget:hover { background: #fafafa; }
        """)
        r_lay = QtWidgets.QHBoxLayout(row)
        r_lay.setContentsMargins(16, 0, 16, 0)

        lbl_id = QtWidgets.QLabel(str(grp_id))
        lbl_nama = QtWidgets.QLabel(grp_nama)
        lbl_id.setStyleSheet("color: #555; font-size: 10pt;")
        lbl_nama.setStyleSheet("color: #222; font-size: 10pt;")

        toggle = ToggleSwitch()
        toggle.setChecked(bool(grp_status))
        toggle.setProperty("grp_id", grp_id)
        toggle.clicked.connect(self._on_toggle_changed)

        r_lay.addWidget(lbl_id, 1)
        r_lay.addWidget(lbl_nama, 6)
        tog_wrap = QtWidgets.QHBoxLayout()
        tog_wrap.addStretch()
        tog_wrap.addWidget(toggle)
        r_lay.addLayout(tog_wrap, 2)
        self.grp_list_layout.insertWidget(self.grp_list_layout.count() - 1, row)

    def _add_ruangan_row(self, r_id, r_nama, grp_nama, mac_id, status, device_status):
        row = QtWidgets.QWidget()
        row.setFixedHeight(48)
        row.setStyleSheet("""
            QWidget { background: white; border-bottom: 1px solid #f0f0f0; }
            QWidget:hover { background: #fafafa; }
        """)
        r_lay = QtWidgets.QHBoxLayout(row)
        r_lay.setContentsMargins(16, 0, 16, 0)

        lbl_id = QtWidgets.QLabel(str(r_id))
        lbl_nama = QtWidgets.QLabel(r_nama)
        lbl_grp = QtWidgets.QLabel(grp_nama)
        lbl_mac = QtWidgets.QLabel(mac_id)
        lbl_id.setStyleSheet("color: #555; font-size: 10pt;")
        lbl_nama.setStyleSheet("color: #222; font-size: 10pt;")
        lbl_grp.setStyleSheet("color: #222; font-size: 10pt;")
        lbl_mac.setStyleSheet("color: #777; font-size: 10pt;")

        btn_status = QtWidgets.QPushButton("AKTIF" if status else "MATI")
        btn_status.setFixedSize(72, 30)
        btn_status.setEnabled(False)
        btn_status.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; "
            "font-size: 9pt; border-radius: 4px; border: none; }"
            if status else
            "QPushButton { background-color: #aaaaaa; color: white; font-weight: bold; "
            "font-size: 9pt; border-radius: 4px; border: none; }"
        )

        device_label = QtWidgets.QLabel(device_status)
        device_label.setAlignment(QtCore.Qt.AlignCenter)
        if device_status == "online":
            device_label.setStyleSheet("color: #27ae60; font-weight: bold;")
        elif device_status == "playing":
            device_label.setStyleSheet("color: #2196F3; font-weight: bold;")
        elif device_status == "offline":
            device_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
        else:
            device_label.setStyleSheet("color: #95a5a6;")

        r_lay.addWidget(lbl_id, 1)
        r_lay.addWidget(lbl_nama, 3)
        r_lay.addWidget(lbl_grp, 3)
        r_lay.addWidget(lbl_mac, 3)
        r_lay.addWidget(btn_status, 1)
        r_lay.addWidget(device_label, 1)
        self.ruangan_list_layout.insertWidget(self.ruangan_list_layout.count() - 1, row)

    def _on_toggle_changed(self):
        toggle = self.sender()
        grp_id = toggle.property("grp_id")
        status = 1 if toggle.isChecked() else 0
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("UPDATE grp SET status=%s WHERE id=%s", (status, grp_id))
            conn.commit()
            cur.close()
            conn.close()
            print(f"Grup {grp_id} diubah ke {status}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    # ── CONNECT SIGNALS ───────────────────────────────────────
    def _connect_signals(self):
        self.btn_dash_group.clicked.connect(self.buka_dash_group)
        self.btn_dash_ruangan.clicked.connect(self.buka_dash_ruangan)
        self.btn_set_ruangan.clicked.connect(self.buka_setting_ruangan)
        self.btn_set_group.clicked.connect(self.buka_setting_group)

        self.publish.clicked.connect(self.on_publish)

        self.simpangroup.clicked.connect(self.tambah_group)
        self.updategroup.clicked.connect(self.update_group)
        self.hapusgroup.clicked.connect(self.hapus_group)
        self.refreshgroup.clicked.connect(self.load_group)
        self.listgroup.itemClicked.connect(self.pilih_group)

        self.simpanruangan.clicked.connect(self.tambah_ruangan)
        self.updateruangan.clicked.connect(self.update_ruangan)
        self.hapusruangan.clicked.connect(self.hapus_ruangan)
        self.refreshruangan.clicked.connect(self.load_ruangan)
        self.listruangan.itemClicked.connect(self.pilih_ruangan)

        self.combo_audio_mode.currentIndexChanged.connect(self._on_audio_mode_changed)
        self.combo_audio_device.currentIndexChanged.connect(self._on_audio_device_changed)
        self.btn_refresh_audio.clicked.connect(self._refresh_audio_devices)

        self.vol_slider.valueChanged.connect(self._on_slider_volume_changed)
        self.vol_spinbox.valueChanged.connect(self._on_spinbox_volume_changed)

    # ── Audio Mode ────────────────────────────────────────────
    def _populate_device_combo(self):
        """Populate the device dropdown based on current audio mode."""
        self.combo_audio_device.blockSignals(True)
        self.combo_audio_device.clear()
        desc = self.audio_sources.get("_descriptions", {})

        if self.audio_mode == AUDIO_MODE_MIC:
            if self.audio_sources["mic"]:
                for src in self.audio_sources["mic"]:
                    label = desc.get(src, src)
                    self.combo_audio_device.addItem(f"🎤 {label}", src)
                # Select the current mic
                for i in range(self.combo_audio_device.count()):
                    if self.combo_audio_device.itemData(i) == self.selected_mic:
                        self.combo_audio_device.setCurrentIndex(i)
                        break
            else:
                self.combo_audio_device.addItem("⚠️ No mic found", "")

        elif self.audio_mode == AUDIO_MODE_LOOPBACK:
            if self.audio_sources["monitor"]:
                for src in self.audio_sources["monitor"]:
                    label = desc.get(src, src)
                    self.combo_audio_device.addItem(f"💻 {label}", src)
                for i in range(self.combo_audio_device.count()):
                    if self.combo_audio_device.itemData(i) == self.selected_monitor:
                        self.combo_audio_device.setCurrentIndex(i)
                        break
            else:
                self.combo_audio_device.addItem("⚠️ No monitor found", "")

        elif self.audio_mode == AUDIO_MODE_MIX:
            # Show mic selection for mix mode (monitor is auto-selected)
            if self.audio_sources["mic"]:
                for src in self.audio_sources["mic"]:
                    label = desc.get(src, src)
                    self.combo_audio_device.addItem(f"🎤 {label}", src)
                for i in range(self.combo_audio_device.count()):
                    if self.combo_audio_device.itemData(i) == self.selected_mic:
                        self.combo_audio_device.setCurrentIndex(i)
                        break
            else:
                self.combo_audio_device.addItem("⚠️ No mic found", "")

        self.combo_audio_device.blockSignals(False)

    def _on_audio_mode_changed(self, index):
        mode = self.combo_audio_mode.itemData(index)
        if mode:
            self.audio_mode = mode

        self._populate_device_combo()
        self._update_device_info_label()

        if self.is_streaming:
            print(f"🔄 Mode berubah ke {self.audio_mode}, restart stream...")
            self.start_mic_to_icecast(force_restart=True)

    def _on_audio_device_changed(self, index):
        device = self.combo_audio_device.itemData(index)
        if not device:
            return

        if self.audio_mode == AUDIO_MODE_MIC:
            self.selected_mic = device
        elif self.audio_mode == AUDIO_MODE_LOOPBACK:
            self.selected_monitor = device
        elif self.audio_mode == AUDIO_MODE_MIX:
            self.selected_mic = device

        self._update_device_info_label()

        if self.is_streaming:
            print(f"🔄 Device berubah, restart stream...")
            self.start_mic_to_icecast(force_restart=True)

    def _update_device_info_label(self):
        desc = self.audio_sources.get("_descriptions", {})
        if self.audio_mode == AUDIO_MODE_MIC:
            dev = desc.get(self.selected_mic, self.selected_mic)
            self.lbl_device_info.setText(f"Device:\n🎤 {dev}")
        elif self.audio_mode == AUDIO_MODE_LOOPBACK:
            dev = desc.get(self.selected_monitor, self.selected_monitor)
            self.lbl_device_info.setText(f"Device:\n💻 {dev}")
        else:
            mic = desc.get(self.selected_mic, self.selected_mic)
            mon = desc.get(self.selected_monitor, self.selected_monitor)
            self.lbl_device_info.setText(f"Device:\n🎤 {mic}\n💻 {mon}")

    def _refresh_audio_devices(self):
        """Re-detect audio devices."""
        self.audio_sources = detect_audio_sources()

        # Re-validate current selections
        if self.selected_mic and self.selected_mic not in self.audio_sources["mic"]:
            self.selected_mic = self.audio_sources["mic"][0] if self.audio_sources["mic"] else ""
        if self.selected_monitor and self.selected_monitor not in self.audio_sources["monitor"]:
            self.selected_monitor = self.audio_sources["monitor"][0] if self.audio_sources["monitor"] else ""

        self._populate_device_combo()
        self._update_device_info_label()

        mic_count = len(self.audio_sources["mic"])
        mon_count = len(self.audio_sources["monitor"])
        self.statusBar().showMessage(
            f"🔄 Audio devices refreshed: {mic_count} mic(s), {mon_count} monitor(s)", 3000
        )

    def _on_slider_volume_changed(self, value):
        """Slider changed → update spinbox and config."""
        vol_percent = float(value)
        self.vol_spinbox.blockSignals(True)
        self.vol_spinbox.setValue(vol_percent)
        self.vol_spinbox.blockSignals(False)
        STREAM_CONFIG["volume"] = str(vol_percent)
        self._debounce_volume()

    def _on_spinbox_volume_changed(self, value):
        """Spinbox changed (typed or arrows) → update slider and config."""
        slider_val = int(round(value))
        self.vol_slider.blockSignals(True)
        self.vol_slider.setValue(slider_val)
        self.vol_slider.blockSignals(False)
        STREAM_CONFIG["volume"] = str(value)
        self._debounce_volume()

    def _debounce_volume(self):
        if self.volume_timer.isActive():
            self.volume_timer.stop()
        if self.is_streaming:
            self.volume_timer.start(500)

    def _apply_volume_change(self):
        if self.is_streaming:
            print(f"🔄 Menerapkan perubahan volume ke {STREAM_CONFIG['volume']}x...")
            self.start_mic_to_icecast(force_restart=True)

    # ── Navigasi ─────────────────────────────────────────────
    def buka_dash_group(self):
        self.halaman.setCurrentIndex(0)
        self.load_dashboard_group()

    def buka_dash_ruangan(self):
        self.halaman.setCurrentIndex(1)
        self.load_dashboard_ruangan()

    def buka_setting_ruangan(self):
        self.halaman.setCurrentIndex(2)
        self.load_combo_group()
        self.load_ruangan()

    def buka_setting_group(self):
        self.halaman.setCurrentIndex(3)
        self.load_group()

    # ── Publish MQTT ──────────────────────────────────────────
    def on_publish(self):
        if not self.mqtt_connected:
            QtWidgets.QMessageBox.warning(
                self, "MQTT Not Connected",
                "MQTT server tidak terhubung!\n\n"
                "Pastikan koneksi internet aktif dan server HiveMQ dapat diakses."
            )
            return

        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT r.mac_id, COALESCE(g.status, 0)
                FROM ruangan r
                LEFT JOIN grp g ON r.id_grp = g.id
                ORDER BY r.id
            """)
            rows = cur.fetchall()

            if not rows:
                QtWidgets.QMessageBox.warning(self, "Publish", "Tidak ada data ruangan.")
                return

            count_on = 0
            count_off = 0
            updated_cache = {}

            for mac_id, status in rows:
                topic = f"audio/control/{mac_id}/set"
                payload = "on" if status else "off"
                result = self.mqtt_client.publish(topic, payload=payload, qos=1, retain=False)
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    updated_cache[mac_id] = status
                    if status == 1:
                        count_on += 1
                    else:
                        count_off += 1
                    print(f"📤 Published to {topic}: {payload}")
                else:
                    print(f"❌ Failed to publish to {topic}")

            self.published_status.update(updated_cache)
            cur.close()
            conn.close()

            if count_on > 0:
                self.start_mic_to_icecast()
            else:
                self.stop_all_streams()

            self.load_dashboard_ruangan()

            mode_label = {
                AUDIO_MODE_MIC: "🎤 Microphone",
                AUDIO_MODE_LOOPBACK: "💻 System Audio (Loopback)",
                AUDIO_MODE_MIX: "🎛️ Mix (Mic + System Audio)",
            }.get(self.audio_mode, "Unknown")

            msg = (f"✅ PUBLISHED: {count_on} ruangan ON, {count_off} ruangan OFF\n\n"
                   f"Sumber Audio : {mode_label}\n"
                   f"Volume Gain  : {STREAM_CONFIG['volume']}x\n\n"
                   f"MQTT Server  : {MQTT_CONFIG['host']}:{MQTT_CONFIG['port']}\n\n")

            if self.is_streaming:
                msg += (f"🎵 Streaming Icecast AKTIF!\n"
                        f"📡 {STREAM_CONFIG['icecast_server']}:{STREAM_CONFIG['icecast_port']}"
                        f"{STREAM_CONFIG['icecast_mount']}")
            else:
                msg += "⏹️ Streaming tidak aktif (semua ruangan OFF)"

            QtWidgets.QMessageBox.information(self, "Publish & Streaming", msg)

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    # ── Dashboard Load Methods ─────────────────────────────────
    def load_dashboard_group(self):
        while self.grp_list_layout.count() > 1:
            item = self.grp_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, nama, status FROM grp ORDER BY id")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            for row in rows:
                self._add_group_row(row[0], row[1], row[2])
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def load_dashboard_ruangan(self):
        while self.ruangan_list_layout.count() > 1:
            item = self.ruangan_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT r.id, r.nama, COALESCE(g.nama, '-'), r.mac_id
                FROM ruangan r
                LEFT JOIN grp g ON r.id_grp = g.id
                ORDER BY r.id
            """)
            rows = cur.fetchall()
            cur.close()
            conn.close()
            for row in rows:
                r_id, r_nama, grp_nama, mac_id = row
                display_status = self.published_status.get(mac_id, 0)
                device_status = self.device_status.get(mac_id, "unknown")
                self._add_ruangan_row(r_id, r_nama, grp_nama, mac_id, display_status, device_status)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    # ── Streaming Methods ────────────────────────────────────
    def _build_ffmpeg_cmd(self):
        gain_percent = float(STREAM_CONFIG.get("volume", "0.0"))
        vol = 1.0 + (gain_percent / 100.0)
        dest = (f"icecast://source:{STREAM_CONFIG['icecast_source_pass']}"
                f"@{STREAM_CONFIG['icecast_server']}:{STREAM_CONFIG['icecast_port']}"
                f"{STREAM_CONFIG['icecast_mount']}")
        enc = ["-c:a", "libmp3lame", "-b:a", STREAM_CONFIG["bitrate"],
               "-ar", "44100", "-ac", "2", "-f", "mp3"]

        system = platform.system()

        if system == "Linux":
            # Linux: use PulseAudio/PipeWire via -f pulse
            if self.audio_mode == AUDIO_MODE_MIC:
                source = self.selected_mic or "default"
                return [
                    self.ffmpeg_path,
                    "-f", "pulse",
                    "-i", source,
                    "-af", f"volume={vol},dynaudnorm=g=5:f=250",
                    *enc, dest
                ]
            elif self.audio_mode == AUDIO_MODE_LOOPBACK:
                source = self.selected_monitor or "default"
                return [
                    self.ffmpeg_path,
                    "-f", "pulse",
                    "-i", source,
                    "-af", f"volume={vol}",
                    *enc, dest
                ]
            else:  # MIX
                mic_source = self.selected_mic or "default"
                monitor_source = self.selected_monitor or "default"
                filter_complex = (
                    f"[0:a]volume={vol}[a0];"
                    f"[1:a]volume={vol}[a1];"
                    f"[a0][a1]amix=inputs=2:duration=longest:dropout_transition=3[aout]"
                )
                return [
                    self.ffmpeg_path,
                    "-f", "pulse", "-i", mic_source,
                    "-f", "pulse", "-i", monitor_source,
                    "-filter_complex", filter_complex,
                    "-map", "[aout]",
                    *enc, dest
                ]

        elif system == "Windows":
            if self.audio_mode == AUDIO_MODE_MIC:
                return [
                    self.ffmpeg_path,
                    "-f", "dshow",
                    "-i", f"audio={self.selected_mic or 'Microphone'}",
                    "-af", f"volume={vol},dynaudnorm=g=5:f=250",
                    *enc, dest
                ]
            elif self.audio_mode == AUDIO_MODE_LOOPBACK:
                return [
                    self.ffmpeg_path,
                    "-f", "wasapi",
                    "-i", "loopback",
                    "-af", f"volume={vol}",
                    *enc, dest
                ]
            else:
                filter_complex = (
                    f"[0:a]volume={vol}[a0];"
                    f"[1:a]volume={vol}[a1];"
                    f"[a0][a1]amix=inputs=2:duration=longest:dropout_transition=3[aout]"
                )
                return [
                    self.ffmpeg_path,
                    "-f", "dshow", "-i", f"audio={self.selected_mic or 'Microphone'}",
                    "-f", "wasapi", "-i", "loopback",
                    "-filter_complex", filter_complex,
                    "-map", "[aout]",
                    *enc, dest
                ]
        else:
            # macOS or other
            return [self.ffmpeg_path, "-version"]

    def start_mic_to_icecast(self, force_restart=False):
        if self.is_streaming and not force_restart:
            print("Streaming already active")
            return

        self.stop_all_streams()

        cmd = self._build_ffmpeg_cmd()
        print("▶ FFmpeg command:", " ".join(cmd[:5]), "...")

        self.mic_publisher = QtCore.QProcess(self)
        self.mic_publisher.start(cmd[0], cmd[1:])

        self.mic_publisher.errorOccurred.connect(self._on_process_error)
        self.mic_publisher.readyReadStandardError.connect(self._on_process_stderr)

        QtCore.QTimer.singleShot(100, self._check_stream_started)

    def _check_stream_started(self):
        if self.mic_publisher and self.mic_publisher.state() == QtCore.QProcess.Running:
            self.is_streaming = True
            mode_label = {
                AUDIO_MODE_MIC: "🎤 Mic",
                AUDIO_MODE_LOOPBACK: "💻 System Audio",
                AUDIO_MODE_MIX: "🎛️ Mix (Mic + System)",
            }.get(self.audio_mode, "")

            self.statusBar().showMessage(
                f"{mode_label} streaming aktif → "
                f"{STREAM_CONFIG['icecast_server']}:{STREAM_CONFIG['icecast_port']}"
                f"{STREAM_CONFIG['icecast_mount']}  |  Vol: {STREAM_CONFIG['volume']}x"
            )
            print(f"✅ Streaming started [{self.audio_mode}]")
        elif self.mic_publisher and self.mic_publisher.state() == QtCore.QProcess.NotRunning:
            print("❌ Streaming failed to start")

    def _on_process_error(self, error):
        print(f"❌ Process error: {error}")

    def _on_process_stderr(self):
        if self.mic_publisher:
            data = self.mic_publisher.readAllStandardError()
            error_text = bytes(data).decode('utf-8', errors='ignore')
            if "error" in error_text.lower() or "fail" in error_text.lower():
                print("FFmpeg error:", error_text[:200])

    def stop_all_streams(self):
        if self.mic_publisher:
            self.mic_publisher.terminate()
            if not self.mic_publisher.waitForFinished(2000):
                self.mic_publisher.kill()
            self.mic_publisher = None
        self.is_streaming = False
        self.statusBar().showMessage("⏹️ Streaming dihentikan")
        print("⏹️ All streams stopped")

    # ── CRUD GROUP Methods ───────────────────────────────────
    def load_group(self):
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, nama FROM grp ORDER BY id")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            self.listgroup.clear()
            for row in rows:
                item = QtWidgets.QListWidgetItem(f"[{row[0]}] {row[1]}")
                item.setData(QtCore.Qt.UserRole, row[0])
                self.listgroup.addItem(item)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def pilih_group(self, item):
        self.sel_group = item.data(QtCore.Qt.UserRole)
        self.editgroup.setText(item.text().split("] ", 1)[1])

    def tambah_group(self):
        nama = self.editgroup.text().strip()
        if not nama:
            QtWidgets.QMessageBox.warning(self, "Peringatan", "Nama group tidak boleh kosong!")
            return
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("INSERT INTO grp (nama, status) VALUES (%s, 0)", (nama,))
            conn.commit()
            cur.close()
            conn.close()
            self.editgroup.clear()
            self.sel_group = None
            self.load_group()
            QtWidgets.QMessageBox.information(self, "Sukses", f"Group '{nama}' ditambahkan!")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def update_group(self):
        if self.sel_group is None:
            QtWidgets.QMessageBox.warning(self, "Peringatan", "Pilih group dari list!")
            return
        nama = self.editgroup.text().strip()
        if not nama:
            QtWidgets.QMessageBox.warning(self, "Peringatan", "Nama group tidak boleh kosong!")
            return
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("UPDATE grp SET nama=%s WHERE id=%s", (nama, self.sel_group))
            conn.commit()
            cur.close()
            conn.close()
            self.editgroup.clear()
            self.sel_group = None
            self.load_group()
            QtWidgets.QMessageBox.information(self, "Sukses", "Group diupdate!")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def hapus_group(self):
        if self.sel_group is None:
            QtWidgets.QMessageBox.warning(self, "Peringatan", "Pilih group dari list!")
            return
        if QtWidgets.QMessageBox.question(
                self, "Konfirmasi", "Hapus group ini?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        ) == QtWidgets.QMessageBox.Yes:
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute("DELETE FROM grp WHERE id=%s", (self.sel_group,))
                conn.commit()
                cur.close()
                conn.close()
                self.editgroup.clear()
                self.sel_group = None
                self.load_group()
                QtWidgets.QMessageBox.information(self, "Sukses", "Group dihapus!")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error", str(e))

    # ── CRUD RUANGAN Methods ─────────────────────────────────
    def load_combo_group(self):
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, nama FROM grp ORDER BY id")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            self.pilihgroup.clear()
            self.pilihgroup.addItem("-- Pilih Group --", None)
            for row in rows:
                self.pilihgroup.addItem(row[1], row[0])
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def load_ruangan(self):
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT r.id, r.nama, r.mac_id, COALESCE(g.nama,'-')
                FROM ruangan r
                LEFT JOIN grp g ON r.id_grp = g.id
                ORDER BY r.id
            """)
            rows = cur.fetchall()
            cur.close()
            conn.close()
            self.listruangan.clear()
            for row in rows:
                text = f"[{row[0]}] {row[1]}  |  MAC: {row[2]}  |  Group: {row[3]}"
                item = QtWidgets.QListWidgetItem(text)
                item.setData(QtCore.Qt.UserRole, row[0])
                self.listruangan.addItem(item)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def pilih_ruangan(self, item):
        self.sel_ruangan = item.data(QtCore.Qt.UserRole)
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT nama, mac_id, id_grp FROM ruangan WHERE id=%s",
                        (self.sel_ruangan,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                self.editnama.setText(row[0])
                self.editmacid.setText(row[1])
                for i in range(self.pilihgroup.count()):
                    if self.pilihgroup.itemData(i) == row[2]:
                        self.pilihgroup.setCurrentIndex(i)
                        break
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def _get_form_ruangan(self):
        nama = self.editnama.text().strip()
        mac_id = self.editmacid.text().strip()
        id_grp = self.pilihgroup.currentData()
        if not nama:
            QtWidgets.QMessageBox.warning(self, "Peringatan", "Nama ruangan tidak boleh kosong!")
            return None
        if not mac_id:
            QtWidgets.QMessageBox.warning(self, "Peringatan", "MAC ID tidak boleh kosong!")
            return None
        if id_grp is None:
            QtWidgets.QMessageBox.warning(self, "Peringatan", "Pilih group terlebih dahulu!")
            return None
        return nama, mac_id, id_grp

    def tambah_ruangan(self):
        data = self._get_form_ruangan()
        if data is None:
            return
        nama, mac_id, id_grp = data
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("INSERT INTO ruangan (nama, mac_id, id_grp) VALUES (%s,%s,%s)",
                        (nama, mac_id, id_grp))
            conn.commit()
            cur.close()
            conn.close()
            self._clear_form_ruangan()
            self.load_ruangan()
            QtWidgets.QMessageBox.information(self, "Sukses", f"Ruangan '{nama}' ditambahkan!")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def update_ruangan(self):
        if self.sel_ruangan is None:
            QtWidgets.QMessageBox.warning(self, "Peringatan", "Pilih ruangan dari list!")
            return
        data = self._get_form_ruangan()
        if data is None:
            return
        nama, mac_id, id_grp = data
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("UPDATE ruangan SET nama=%s, mac_id=%s, id_grp=%s WHERE id=%s",
                        (nama, mac_id, id_grp, self.sel_ruangan))
            conn.commit()
            cur.close()
            conn.close()
            self._clear_form_ruangan()
            self.load_ruangan()
            QtWidgets.QMessageBox.information(self, "Sukses", "Ruangan diupdate!")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def hapus_ruangan(self):
        if self.sel_ruangan is None:
            QtWidgets.QMessageBox.warning(self, "Peringatan", "Pilih ruangan dari list!")
            return
        if QtWidgets.QMessageBox.question(
                self, "Konfirmasi", "Hapus ruangan ini?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        ) == QtWidgets.QMessageBox.Yes:
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute("DELETE FROM ruangan WHERE id=%s", (self.sel_ruangan,))
                conn.commit()
                cur.close()
                conn.close()
                self._clear_form_ruangan()
                self.load_ruangan()
                QtWidgets.QMessageBox.information(self, "Sukses", "Ruangan dihapus!")
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def _clear_form_ruangan(self):
        self.editnama.clear()
        self.editmacid.clear()
        self.pilihgroup.setCurrentIndex(0)
        self.sel_ruangan = None

    def closeEvent(self, event):
        self.stop_all_streams()
        if self.mqtt_client:
            try:
                self.mqtt_client.publish("audio/control/status", "offline", qos=1, retain=True)
            except:
                pass
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        if self.mqtt_reconnect_timer:
            self.mqtt_reconnect_timer.stop()
        if self.mqtt_status_timer:
            self.mqtt_status_timer.stop()
        event.accept()


# ============================================================
#  ENTRY POINT
# ============================================================
if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    if not init_database():
        sys.exit(1)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())