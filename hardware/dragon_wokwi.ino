// ============================================================
//  Dragon Telemetry Station — ESP32
//  Sensores: DHT22 (temp/umidade), BMP180 (pressão), LDR (luz)
//  Atuadores: LED vermelho (GPIO 19), Buzzer (GPIO 27)
//  Protocolo: MQTT Ultralight 2.0 → FIWARE
// ============================================================

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <Wire.h>
#include <Adafruit_BMP085.h>

// WiFi (Wokwi usa essa rede)
const char* SSID     = "Wokwi-GUEST";
const char* PASSWORD = "";

// MQTT — substitua pelo IP real da sua VM Azure
const char* MQTT_BROKER = "SEU_IP_DA_VM";
const int   MQTT_PORT   = 1883;

// Tópicos FIWARE
const char* TOPIC_ATTRS = "/dragon2026/dragon-esp32-001/attrs";
const char* TOPIC_CMD   = "/dragon2026/dragon-esp32-001/cmd";

// Pinos
#define DHT_PIN    15
#define DHT_TYPE   DHT22
#define LDR_PIN    34
#define LED_PIN    19
#define BUZZER_PIN 27

DHT dht(DHT_PIN, DHT_TYPE);
Adafruit_BMP085 bmp;
WiFiClient espClient;
PubSubClient client(espClient);

// ============================================================
// Callback: recebe comandos do Python
// Comandos possíveis: alert | normal
// ============================================================
void onMessage(char* topic, byte* payload, unsigned int length) {
  String msg = "";
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  Serial.println("[CMD] " + msg);

  if (msg.indexOf("alert") >= 0) {
    // Alerta: LED vermelho aceso + buzzer intermitente
    digitalWrite(LED_PIN, HIGH);
    for (int i = 0; i < 3; i++) {
      tone(BUZZER_PIN, 1000, 300);
      delay(500);
    }
    noTone(BUZZER_PIN);
  } else if (msg.indexOf("normal") >= 0) {
    // Tudo normal: LED e buzzer apagados
    digitalWrite(LED_PIN, LOW);
    noTone(BUZZER_PIN);
  }
}

// ============================================================
void connectWiFi() {
  Serial.print("[WiFi] Conectando...");
  WiFi.begin(SSID, PASSWORD);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println(" OK");
}

void connectMQTT() {
  while (!client.connected()) {
    Serial.print("[MQTT] Conectando...");
    if (client.connect("dragon-esp32-001")) {
      Serial.println(" OK");
      client.subscribe(TOPIC_CMD);
    } else {
      Serial.print(" falhou, rc="); Serial.println(client.state());
      delay(3000);
    }
  }
}

// ============================================================
void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN,    OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  dht.begin();
  Wire.begin();
  if (!bmp.begin()) {
    Serial.println("[BMP180] Nao encontrado!");
  }

  connectWiFi();
  client.setServer(MQTT_BROKER, MQTT_PORT);
  client.setCallback(onMessage);
}

// ============================================================
void loop() {
  if (!client.connected()) connectMQTT();
  client.loop();

  // Leituras dos sensores
  float temp  = dht.readTemperature();
  float umid  = dht.readHumidity();
  float press = bmp.readPressure() / 100.0;  // hPa
  int   ldr   = analogRead(LDR_PIN);

  if (isnan(temp)) temp = 22.0;
  if (isnan(umid)) umid = 50.0;

  // Publica no formato Ultralight 2.0
  // t=temperatura | h=umidade | p=pressao | l=ldr
  String payload = "t|" + String(temp, 1)
                 + "|h|" + String(umid, 1)
                 + "|p|" + String(press, 1)
                 + "|l|" + String(ldr);

  client.publish(TOPIC_ATTRS, payload.c_str());
  Serial.println("[PUB] " + payload);

  delay(10000);  // publica a cada 10s
}
