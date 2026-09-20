#!/usr/bin/python3
import paho.mqtt.client as mqtt
import json
import requests
import time

# --- CONFIGURATION --
KEYLIME_AGENT_UUID = "d432fbb3-d2f1-4a97-9ef7-75bd81c00000"

# Keylime REST API Settings
VERIFIER_IP = "127.0.0.1"
VERIFIER_PORT = "8881"

# Paths to the Keylime mTLS certificates
# (Verify these paths match your installation, typically in /var/lib/keylime/cv_ca/)
CLIENT_CERT = '/var/lib/keylime/cv_ca/client-cert.crt'
CLIENT_KEY = '/var/lib/keylime/cv_ca/client-private.pem'
CA_CERT = '/var/lib/keylime/cv_ca/cacert.crt'

TRUSTED_NODES = {
    "EC:E3:34:65:7F:6C": "d4bca3daddb953843162e1df7f89ddbd52e7c96e80f36d0815f393b9fd0bac1d"
}

TOPIC_REQUEST = "v2x/rsu/attestation_request" 
TOPIC_CONTROL = "v2x/traffic_light/control"

def check_keylime_status():
    """Asks Keylime if the Fedora RSU is currently secure via REST API."""
    print(" -> Checking local Keylime hardware attestation status (API)...")
    url = f"https://{VERIFIER_IP}:{VERIFIER_PORT}/v2/agents/{KEYLIME_AGENT_UUID}"
    
    try:
        start_time = time.time()
        
        # Send the GET request directly to the Keylime Verifier using mTLS
        response = requests.get(
            url, 
            cert=(CLIENT_CERT, CLIENT_KEY), 
            verify=CA_CERT,
            timeout=2.0
        )
        
        latency = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            state = data.get('results', {}).get('operational_state', 'Unknown')
            print(f"    [API Result] State: {state} | Latency: {latency:.4f}s")
            trusted_states = ["Trusted", "Get Quote", "Saved", "Attested"]
            
            if state in trusted_states:
                return True
            else:
                return False
        else:
            print(f"    [API Error] HTTP {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"    [API Connection Failed] Is Keylime Verifier running? Error: {e}")
        return False

# --- MQTT CALLBACK ---
def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        mac = payload.get("mac")
        received_hash = payload.get("firmware_hash")
        
        print(f"\n[+] INTERSECTION PREEMPTION REQUEST RECEIVED")
        print(f" -> Approaching OBU: {mac}")

        # Step 1: Check the ESP32
        if mac in TRUSTED_NODES and TRUSTED_NODES[mac] == received_hash:
            print(" -> OBU VERDICT: TRUSTED. ESP32 firmware is valid.")
            
            # Step 2: Check Keylime
            if check_keylime_status():
                print(" -> KEYLIME VERDICT: TRUSTED. Fedora RSU hardware is secure.")
                print(" -> FINAL ACTION: Sending GREEN light command to Windows.")
                client.publish(TOPIC_CONTROL, "GREEN")
            else:
                print(" -> KEYLIME VERDICT: FAILED. Fedora RSU hardware is compromised!")
                print(" -> FINAL ACTION: Sending RED light command. Aborting.")
                client.publish(TOPIC_CONTROL, "RED")
                
        else:
            print(" -> OBU VERDICT: FAILED. ESP32 MAC/Hash invalid or tampered!")
            print(" -> FINAL ACTION: Sending RED light command. Access Denied.")
            client.publish(TOPIC_CONTROL, "RED")
            
    except Exception as e:
        print(f"\n[ERROR] Attestation failed: {e}")

# --- MQTT SETUP ---
print("Initializing Dual-Pillar RSU Verifier on Fedora (API Mode)...")

try:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, "RSU_Attestation_Engine")
except AttributeError:
    client = mqtt.Client("RSU_Attestation_Engine")

client.on_message = on_message
client.connect("127.0.0.1", 1883)
client.subscribe(TOPIC_REQUEST)

print("Listening for OBU beacons...")
client.loop_forever()