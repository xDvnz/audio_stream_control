/**
 * ================================================================
 *  ESP32 Wroover — MP3 Stream Player + MQTT Relay Control
 *  Dengan MQTT over TLS (HiveMQ Cloud - CA Signed)
 * ================================================================
 *  Hardware:
 *    - ESP32 Wroover (PSRAM 4 MB)
 *    - PCM5102 DAC via I2S
 *    - Relay pada GPIO 32 (active-HIGH)
 *
 *  I2S Pin:
 *    BCLK -> GPIO 26 | LRC -> GPIO 25 | DIN -> GPIO 22
 *
 *  Library (Arduino Library Manager):
 *    - ESP8266Audio  by Earle F. Philhower III
 *    - PubSubClient  by Nick O'Leary
 *
 *  Arduino IDE:
 *    Board            : ESP32 Wrover Module
 *    Partition Scheme : Huge APP (3MB No OTA)
 *    PSRAM            : Enabled
 *    CPU Frequency    : 240 MHz
 * ================================================================
 */

/**
 * ================================================================
 *  ESP32 Wroover — MP3 Stream Player + MQTT Relay Control
 *  Dengan MQTT over TLS - FIXED (mengacu ke kode yang berhasil)
 * ================================================================
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include "AudioFileSourceICYStream.h"
#include "AudioFileSourceBuffer.h"
#include "AudioGeneratorMP3.h"
#include "AudioOutputI2S.h"

// ================================================================
//  KONFIGURASI (SAMA PERSIS DENGAN YANG BERHASIL)
// ================================================================
static const char ssid[]       = "Wignyo Family 2.4G";
static const char password[]   = "11227171";
static const char streamURL[]  = "http://192.168.18.7:8000/live.mp3";

// MQTT Broker HiveMQ Cloud - SAME CREDENTIALS PATTERN
static const char mqtt_host[]   = "c0099a6e70884169bfc6b2f482c29e2b.s1.eu.hivemq.cloud";
static const uint16_t mqtt_port = 8883;
static const char* mqtt_user    = "streamaudio";        // Sama seperti kode berhasil
static const char* mqtt_pass    = "StreamAudio_2026";   // Sama seperti kode berhasil

static const int I2S_BCLK = 26;
static const int I2S_LRC  = 25;
static const int I2S_DOUT = 22;

const uint8_t RELAY_PIN = 32;
static const size_t BUFFER_SIZE = 80 * 1024;

// ================================================================
//  GLOBALS
// ================================================================
static uint8_t *psramBuffer = nullptr;

AudioFileSourceICYStream *audioSource = nullptr;
AudioFileSourceBuffer    *audioBuffer = nullptr;
AudioGeneratorMP3        *audioMP3    = nullptr;
AudioOutputI2S           *audioOutput = nullptr;

// KRUSIAL: WiFiClientSecure (sama seperti kode berhasil)
WiFiClientSecure wifiClient;
PubSubClient mqttClient(wifiClient);

char topicSubscribe[64];
bool relayState  = false;
bool needRestart = false;

unsigned long lastMqttAttempt = 0;
unsigned long lastWifiCheck   = 0;

// ================================================================
//  FUNGSI GENERATE RANDOM CLIENT ID (SAMA SEPERTI KODE BERHASIL)
// ================================================================
String generateRandomClientId() {
  String clientId = "ESP32Audio_";  // Bedakan dengan kode lain
  
  // Ambil MAC address
  clientId += WiFi.macAddress();
  clientId += "_";
  
  // Tambahkan random number (krusial untuk hindari clash)
  clientId += String(random(0, 10000));
  
  return clientId;
}

// ================================================================
//  FORWARD DECLARATIONS
// ================================================================
void connectWiFi();
void connectMQTT();
void mqttCallback(char *topic, byte *payload, unsigned int length);
void setRelay(bool on);
void startStream();
void stopStream();

// ================================================================
//  SETUP
// ================================================================
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println(F("\n=== ESP32 Wroover MP3 Player (HiveMQ TLS FIXED) ==="));

  // Random seed untuk client ID
  randomSeed(analogRead(0));
  
  pinMode(RELAY_PIN, OUTPUT);
  setRelay(false);

  Serial.printf("[MEM] Internal free : %u bytes\n", ESP.getFreeHeap());
  if (psramFound()) {
    Serial.printf("[MEM] PSRAM free    : %u bytes\n", ESP.getFreePsram());
    psramBuffer = (uint8_t*) ps_malloc(BUFFER_SIZE);
    if (psramBuffer) {
      Serial.printf("[MEM] Buffer PSRAM OK: %u bytes\n", BUFFER_SIZE);
    }
  }

  // --- WiFi ---
  connectWiFi();

  // --- Topic MQTT dari MAC ---
  snprintf(topicSubscribe, sizeof(topicSubscribe),
           "audio/control/%s", WiFi.macAddress().c_str());
  Serial.print(F("[MQTT] Topic: "));
  Serial.println(topicSubscribe);

  // ========== KRUSIAL: KONFIGURASI TLS SAMA SEPERTI KODE BERHASIL ==========
  Serial.println(F("[MQTT] Configuring TLS (insecure mode for testing)..."));
  wifiClient.setInsecure();  // <-- INI YANG MEMBUAT KONEKSI BERHASIL!
  
  // Setup PubSubClient
  mqttClient.setServer(mqtt_host, mqtt_port);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setKeepAlive(30);
  mqttClient.setSocketTimeout(10);
  
  // --- Connect MQTT (blocking seperti kode berhasil) ---
  connectMQTT();

  // --- Audio output I2S ---
  audioOutput = new AudioOutputI2S(0, AudioOutputI2S::EXTERNAL_I2S);
  audioOutput->SetPinout(I2S_BCLK, I2S_LRC, I2S_DOUT);
  audioOutput->SetRate(44100);
  audioOutput->SetChannels(2);
  //audioOutput->SetGain(1.0f);
  audioOutput->SetGain(0.5f); // atur gain di sini

  startStream();
}

// ================================================================
//  LOOP
// ================================================================
void loop() {
  unsigned long now = millis();

  // WiFi check setiap 10 detik
  if (now - lastWifiCheck > 10000) {
    lastWifiCheck = now;
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println(F("[WiFi] Disconnected — reconnecting..."));
      stopStream();
      connectWiFi();
      needRestart = true;
    }
  }

  // MQTT handling - SAMA SEPERTI KODE BERHASIL
  if (!mqttClient.connected()) {
    connectMQTT();  // Akan mencoba reconnect (blocking dengan delay)
  } else {
    mqttClient.loop();
  }

  if (needRestart) {
    needRestart = false;
    delay(2000);
    startStream();
    return;
  }

  if (audioMP3 && audioMP3->isRunning()) {
    if (!audioMP3->loop()) {
      Serial.println(F("[AUDIO] Stream stopped — restart scheduled"));
      stopStream();
      needRestart = true;
    }
  }
}

// ================================================================
//  WiFi (SAMA SEPERTI KODE BERHASIL)
// ================================================================
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  
  Serial.printf("[WiFi] Connecting to %s", ssid);
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  
  while (WiFi.status() != WL_CONNECTED) {  // Blocking seperti kode berhasil
    delay(500);
    Serial.print('.');
  }
  
  Serial.println("\n[WiFi] Connected!");
  Serial.printf("[WiFi] IP: %s\n", WiFi.localIP().toString().c_str());
  Serial.printf("[WiFi] MAC: %s\n", WiFi.macAddress().c_str());
}

// ================================================================
//  MQTT Connect (SAMA PERSEPTI KODE BERHASIL - BLOCKING DENGAN RETRY)
// ================================================================
void connectMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("[MQTT] Connecting to broker...");
    
    // Gunakan random client ID (sama seperti kode berhasil)
    String clientId = generateRandomClientId();
    
    Serial.printf(" Client ID: %s\n", clientId.c_str());
    
    // Attempt connection
    if (mqttClient.connect(clientId.c_str(), mqtt_user, mqtt_pass)) {
      Serial.println("[MQTT] ✓ Connected to HiveMQ Cloud!");
      
      // Subscribe ke topic control
      if (mqttClient.subscribe(topicSubscribe)) {
        Serial.printf("[MQTT] ✓ Subscribed to: %s\n", topicSubscribe);
      } else {
        Serial.printf("[MQTT] ✗ Failed to subscribe to: %s\n", topicSubscribe);
      }
      
      // Optional: publish status online
      // mqttClient.publish(topicSubscribe, "ONLINE", true);
      
    } else {
      Serial.print("[MQTT] ✗ Failed, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" retrying in 5 seconds...");
      delay(5000);  // Blocking retry seperti kode berhasil
    }
  }
}

// ================================================================
//  MQTT Callback
// ================================================================
void mqttCallback(char *topic, byte *payload, unsigned int length) {
  char msg[32] = {0};
  memcpy(msg, payload, min((unsigned int)(sizeof(msg) - 1), length));
  Serial.printf("[MQTT] Received on [%s]: %s\n", topic, msg);

  String s = String(msg);
  s.toUpperCase();
  s.trim();

  if (s == "ON" || s == "1" || s == "TRUE") {
    setRelay(true);
  } else if (s == "OFF" || s == "0" || s == "FALSE") {
    setRelay(false);
  } else {
    Serial.printf("[MQTT] Unknown payload: %s\n", msg);
  }
}

// ================================================================
//  Relay Control
// ================================================================
void setRelay(bool on) {
  relayState = on;
  digitalWrite(RELAY_PIN, on ? HIGH : LOW);
  Serial.printf("[RELAY] %s\n", on ? "ON - Speaker connected" : "OFF - Speaker disconnected");
  
  // Publish status balik ke MQTT (optional)
  if (mqttClient.connected()) {
    String statusTopic = String(topicSubscribe) + "/status";
    mqttClient.publish(statusTopic.c_str(), on ? "ON" : "OFF", true);
  }
}

// ================================================================
//  Audio Stream Functions
// ================================================================
void startStream() {
  Serial.println(F("[AUDIO] Starting stream..."));
  stopStream();

  audioSource = new AudioFileSourceICYStream(streamURL);
  if (!audioSource) {
    Serial.println(F("[AUDIO] Failed to create ICYStream"));
    return;
  }

  if (psramBuffer) {
    audioBuffer = new AudioFileSourceBuffer(audioSource, psramBuffer, BUFFER_SIZE);
    Serial.printf("[AUDIO] Using PSRAM buffer: %u bytes\n", BUFFER_SIZE);
  } else {
    audioBuffer = new AudioFileSourceBuffer(audioSource, 16 * 1024);
    Serial.println(F("[AUDIO] Using internal RAM buffer: 16KB"));
  }

  if (!audioBuffer) {
    Serial.println(F("[AUDIO] Failed to create buffer"));
    delete audioSource;
    audioSource = nullptr;
    return;
  }

  audioMP3 = new AudioGeneratorMP3();
  if (!audioMP3->begin(audioBuffer, audioOutput)) {
    Serial.println(F("[AUDIO] Failed to start MP3 decoder"));
    delete audioMP3;
    audioMP3 = nullptr;
    delete audioBuffer;
    audioBuffer = nullptr;
    delete audioSource;
    audioSource = nullptr;
    return;
  }

  Serial.println(F("[AUDIO] ✓ Stream is playing"));
}

void stopStream() {
  if (audioMP3) {
    if (audioMP3->isRunning()) audioMP3->stop();
    delete audioMP3;
    audioMP3 = nullptr;
  }
  if (audioBuffer) {
    delete audioBuffer;
    audioBuffer = nullptr;
  }
  if (audioSource) {
    delete audioSource;
    audioSource = nullptr;
  }
}
