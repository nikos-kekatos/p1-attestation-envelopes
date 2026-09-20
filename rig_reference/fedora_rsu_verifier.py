import paho.mqtt.client as mqtt
import json
import sys

TRUSTED_NODES = {"EC:E3:34:65:7F:6C": "PASTE_YOUR_NEW_HASH_HERE"}
TOPIC_REQUEST = "v2x/rsu/attestation_request"
TOPIC_CONTROL = "v2x/traffic_light/control"

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        mac = data.get("mac")
        received_hash = data.get("hash")
        print(f"\n[RSU] Incoming Request from OBU: {mac}")
        print(f"      Received Hash: {received_hash}")

        if mac in TRUSTED_NODES:
            if TRUSTED_NODES[mac] == received_hash:
                print("      VERDICT: TRUSTED. Sending GREEN to Windows.")
                client.publish(TOPIC_CONTROL, "GREEN")
            else:
                print("      VERDICT: UNTRUSTED (TAMPERED). Sending RED.")
                client.publish(TOPIC_CONTROL, "RED")
        else:
            print("      VERDICT: UNKNOWN IDENTITY. Sending RED.")
            client.publish(TOPIC_CONTROL, "RED")
    except Exception as e:
        print(f"Error: {e}")

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, "RSU_Judge")
    except AttributeError:
        client = mqtt.Client("RSU_Judge")
        
client.on_message = on_message
client.connect("127.0.0.1", 1883)
client.subscribe(TOPIC_REQUEST)
print("RSU Verifier Active on Fedora (IP: 192.168.1.15)...")
client.loop_forever()