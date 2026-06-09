# ============================================================
#  Dragon Telemetry — Python Flask na VM Azure
#  Lê dados do ESP32 no FIWARE Orion
#  Decide alertas e envia comandos ao ESP32 via MQTT
#  Grava histórico em SQLite
#  Expõe API REST para o dashboard React
# ============================================================

import requests
import paho.mqtt.client as mqtt
import os
import time
import threading
import sqlite3
from datetime import datetime, timezone
from flask import Flask, jsonify, request
from flask_cors import CORS

# ============================================================
# CONFIGURAÇÕES
# ============================================================
ORION_HOST  = "http://localhost:1026"
ENTITY_ID   = "urn:ngsi-ld:DragonCapsule:001"
SERVICE     = "dragon"
SERVICEPATH = "/"

MQTT_BROKER = "localhost"
MQTT_PORT   = 1883
DEVICE_ID   = "dragon-esp32-001"
TOPIC_CMD   = f"/dragon2026/{DEVICE_ID}/cmd"

HEADERS_GET = {
    "fiware-service": SERVICE,
    "fiware-servicepath": SERVICEPATH
}
HEADERS_POST = {
    "fiware-service": SERVICE,
    "fiware-servicepath": SERVICEPATH,
    "Content-Type": "application/json"
}

# ============================================================
# LIMITES DE ALERTA (telemetria Dragon)
# Fora desses limites → alerta
# ============================================================
LIMITES = {
    "temp_max":  40.0,   # °C — acima disso é crítico
    "temp_min":  10.0,   # °C — abaixo disso é crítico
    "umid_max":  80.0,   # % — umidade alta = risco condensação
    "press_min": 950.0,  # hPa — pressão baixa = despressurização
    "ldr_max":   3500,   # raw — radiação solar intensa
}

# ============================================================
# HISTÓRICO — SQLite
# ============================================================
DB_PATH      = "/home/azureuser/dragon-python/history.db"
MIN_INTERVAL = 300  # grava a cada 5 minutos
_last_insert = 0

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS telemetry_history (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        ts       TEXT NOT NULL,
        temp     REAL,
        umidade  REAL,
        pressao  REAL,
        ldr      INTEGER,
        status   TEXT)""")
    con.commit()
    con.close()
    print("[HIST] Banco SQLite pronto.")

def save_history(temp, umid, press, ldr, status):
    global _last_insert
    now = time.time()
    if now - _last_insert < MIN_INTERVAL:
        return
    _last_insert = now
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT INTO telemetry_history (ts,temp,umidade,pressao,ldr,status) "
            "VALUES (?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), temp, umid, press, ldr, status))
        con.commit()
        con.close()
        print(f"  [HIST] gravado status={status}")
    except Exception as e:
        print(f"  [HIST] erro: {e}")

def get_history(limit=100):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT ts,temp,umidade,pressao,ldr,status "
        "FROM telemetry_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    rows = [dict(r) for r in rows][::-1]
    return {
        "index":  [r["ts"] for r in rows],
        "values": [r["temp"] for r in rows],
        "rows":   rows,
    }

# ============================================================
# Estado global
# ============================================================
state = {
    "temp": 22.0, "umidade": 50.0,
    "pressao": 1013.0, "ldr": 2048,
    "status": "normal",
    "alertas": [],
    "last_update": None,
}

# ============================================================
# Flask
# ============================================================
app = Flask(__name__)
CORS(app)
init_db()

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "Dragon Telemetry API"})

@app.route('/telemetry', methods=['GET'])
def get_telemetry():
    return jsonify({
        "temperatura":  state["temp"],
        "umidade":      state["umidade"],
        "pressao":      state["pressao"],
        "ldr":          state["ldr"],
        "status":       state["status"],
        "alertas":      state["alertas"],
        "lastUpdate":   state["last_update"],
    })

@app.route('/history', methods=['GET'])
def get_history_route():
    try:
        limit = int(request.args.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100
    return jsonify(get_history(limit=limit))

# ============================================================
# MQTT
# ============================================================
mqtt_client = mqtt.Client()
mqtt_client.on_connect = lambda c, u, f, rc: print(f"[MQTT] rc={rc}")

def connect_mqtt():
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        print(f"[MQTT] Erro: {e}")

def enviar_comando(cmd):
    mqtt_client.publish(TOPIC_CMD, f"{DEVICE_ID}@{cmd}|")
    print(f"  [CMD] {cmd}")

# ============================================================
# Lógica de alerta
# ============================================================
def verificar_alertas(temp, umid, press, ldr):
    alertas = []
    if temp > LIMITES["temp_max"]:
        alertas.append(f"TEMP CRITICA: {temp}°C > {LIMITES['temp_max']}°C")
    if temp < LIMITES["temp_min"]:
        alertas.append(f"TEMP BAIXA: {temp}°C < {LIMITES['temp_min']}°C")
    if umid > LIMITES["umid_max"]:
        alertas.append(f"UMIDADE ALTA: {umid}% > {LIMITES['umid_max']}%")
    if press < LIMITES["press_min"]:
        alertas.append(f"PRESSAO BAIXA: {press}hPa < {LIMITES['press_min']}hPa")
    if ldr > LIMITES["ldr_max"]:
        alertas.append(f"RADIACAO ALTA: LDR={ldr} > {LIMITES['ldr_max']}")
    return alertas

def get_entity_data():
    try:
        r = requests.get(
            f"{ORION_HOST}/v2/entities/{ENTITY_ID}",
            headers=HEADERS_GET, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [ORION] Falha: {e}")
    return None

def atualizar_orion(temp, umid, press, ldr, status):
    try:
        body = {
            "temperatura": {"type": "Number", "value": temp},
            "umidade":     {"type": "Number", "value": umid},
            "pressao":     {"type": "Number", "value": press},
            "ldr":         {"type": "Number", "value": ldr},
            "status":      {"type": "Text",   "value": status},
        }
        r = requests.patch(
            f"{ORION_HOST}/v2/entities/{ENTITY_ID}/attrs",
            headers=HEADERS_POST, json=body, timeout=5)
        if r.status_code in [200, 204]:
            print(f"  [ORION] atualizado status={status}")
        else:
            print(f"  [ORION] Erro {r.status_code}")
    except Exception as e:
        print(f"  [ORION] Falha: {e}")

# ============================================================
# Loop principal
# ============================================================
def main_loop():
    print("[LOOP] Iniciando...")
    connect_mqtt()
    time.sleep(2)

    while True:
        try:
            now = datetime.now().strftime("%H:%M:%S")
            print(f"\n[{now}] Ciclo...")

            data = get_entity_data()
            if data:
                def safe_float(key, default):
                    val = data.get(key, {}).get("value")
                    return float(val) if val is not None else default

                temp  = safe_float("temperature", 22.0)
                umid  = safe_float("humidity",    50.0)
                press = safe_float("pressure",    1013.0)
                ldr   = int(safe_float("ldrRaw",  2048))

                alertas = verificar_alertas(temp, umid, press, ldr)
                status  = "alerta" if alertas else "normal"

                state.update({
                    "temp": temp, "umidade": umid,
                    "pressao": press, "ldr": ldr,
                    "status": status, "alertas": alertas,
                    "last_update": now,
                })

                print(f"  temp={temp}°C  umid={umid}%  press={press}hPa  ldr={ldr}")
                print(f"  status={status}  alertas={alertas}")

                # Comando ao ESP32
                enviar_comando("alert" if alertas else "normal")

                # Atualiza Orion e histórico
                atualizar_orion(temp, umid, press, ldr, status)
                save_history(temp, umid, press, ldr, status)

            else:
                print("  Sem dados do Orion...")

        except Exception as e:
            print(f"[ERRO] {e}")

        time.sleep(10)

# ============================================================
if __name__ == "__main__":
    print("=" * 55)
    print("  Dragon Telemetry — Flask + FIWARE + SQLite")
    print("=" * 55)
    t = threading.Thread(target=main_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=False)
