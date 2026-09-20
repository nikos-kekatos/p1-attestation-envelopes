#!/usr/bin/python3
import paho.mqtt.client as mqtt
import json
import subprocess

# --- CONFIGURATION --
# The UUID of your Fedora Keylime Agent (from your documents)
KEYLIME_AGENT_UUID = "d432fbb3-d2f1-4a97-9ef7-75bd81c00000"

# Your lightweight IoT Database for the ESP32
TRUSTED_NODES = {
    "EC:E3:34:65:7F:6C": "d4bca3daddb953843162e1df7f89ddbd52e7c96e80f36d0815f393b9fd0bac1d"
}

TOPIC_REQUEST = "v2x/rsu/attestation_request" 
TOPIC_CONTROL = "v2x/traffic_light/control"
#comments
def check_keylime_status():
    """Asks Keylime if the Fedora RSU is currently secure."""
    print(" -> Checking local Keylime hardware attestation status...")
    try:
        # Runs the Keylime status command
        cmd = f"sudo keylime_tenant -c status -u {KEYLIME_AGENT_UUID}"
        result = subprocess.run(cmd.split(), capture_output=True, text=True)
        
        # Check if the output contains the "Trusted" state or "Get Quote" (normal operational states)
        if '"operational_state": "Trusted"' in result.stdout or '"operational_state": "Get Quote"' in result.stdout:
            return True
        else:
            return False
    except Exception as e:
        print(f" -> [ERROR] Failed to communicate with Keylime: {e}")
        return False

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        mac = data.get("mac")
        received_hash = data.get("hash")
        
        print(f"\n[RSU ATTESTATION ENGINE] Request from OBU: {mac}")

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
print("Initializing Dual-Pillar RSU Verifier on Fedora...")

try:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, "RSU_Attestation_Engine")
except AttributeError:
    client = mqtt.Client("RSU_Attestation_Engine")

client.on_message = on_message
client.connect("127.0.0.1", 1883)
client.subscribe(TOPIC_REQUEST)

print("RSU Active. Waiting for ESP32 approach...")
client.loop_forever()
