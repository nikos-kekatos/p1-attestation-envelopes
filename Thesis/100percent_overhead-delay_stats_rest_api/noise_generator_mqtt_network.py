#!/usr/bin/python3
import paho.mqtt.client as mqtt
import time
import sys

# --- CONFIGURATION ---
BROKER_IP = "127.0.0.1"
BROKER_PORT = 1883
NOISE_TOPIC = "v2x/network/noise"

# Size of each trash message in bytes (1024 bytes = 1 KB)
PAYLOAD_SIZE_BYTES = 1024 
TRASH_PAYLOAD = "X" * PAYLOAD_SIZE_BYTES

print(f"Preparing stress tester... Payload size: {PAYLOAD_SIZE_BYTES / 1024:.2f} KB per message.")

try:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "Network_Flooder")
except AttributeError:
    client = mqtt.Client("Network_Flooder")

try:
    client.connect(BROKER_IP, BROKER_PORT, 60)
except Exception as e:
    print(f"Failed to connect to broker: {e}")
    sys.exit(1)

# Start background network loop
client.loop_start()

print("Network flooder running. Press Ctrl+C to terminate.")
message_count = 0
start_time = time.time()

try:
    while True:
        # Publish with QoS 0 for maximum speed/flooding capacity
        client.publish(NOISE_TOPIC, TRASH_PAYLOAD, qos=0)
        message_count += 1
        
        # Every 50,000 messages, print a status update
        if message_count % 50000 == 0:
            elapsed = time.time() - start_time
            rate = message_count / elapsed
            print(f"[!] Sent {message_count} messages total. Current throughput rate: {rate:.2f} msg/sec")

except KeyboardInterrupt:
    print("\nStopping flood tester...")
    client.loop_stop()
    client.disconnect()
    print("Done. Network cleared.")