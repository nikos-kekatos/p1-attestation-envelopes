import sys
import time
import math
import paho.mqtt.client as mqtt

FEDORA_IP = "192.168.1.15"
TOPIC_BEACON = "v2x/rsu/beacon"
TOPIC_CONTROL = "v2x/traffic_light/control"

# CARLA EGG PATH
sys.path.insert(0, r"E:\WindowsNoEditor\PythonAPI\carla\dist\carla-0.9.8-py3.7-win-amd64.egg")
import carla

class CarlaV2IMaster:
    def __init__(self):
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, "CarlaMaster")
        except AttributeError:
            self.client = mqtt.Client("CarlaMaster")
            
        self.client.on_message = self.on_message
        self.target_light = None
        self.allow_green = False
        
        # --- NEW: Added a variable to track the start time ---
        self.start_time = 0.0

    def on_message(self, client, userdata, msg):
        command = msg.payload.decode()
        if msg.topic == TOPIC_CONTROL:
            # --- NEW: Stop the timer and calculate latency ---
            end_time = time.time()
            if self.start_time > 0:
                latency_ms = (end_time - self.start_time) * 1000
                print(f"\n[PERFORMANCE] End-to-End Attestation Latency: {latency_ms:.2f} ms")
                self.start_time = 0.0 # Reset timer
                
            print(f"[RSU COMMAND RECEIVED]: {command}")
            if command == "GREEN":
                self.allow_green = True
            else:
                print("[ALERT] Access Denied by RSU.")

    def run(self):
        print(f"Connecting to Fedora RSU at {FEDORA_IP}...")
        try:
            self.client.connect(FEDORA_IP, 1883, 60)
            self.client.subscribe(TOPIC_CONTROL)
            self.client.loop_start()
        except Exception as e:
            print(f"Connection Error: {e}")
            return

        try:
            client = carla.Client('localhost', 2000)
            client.set_timeout(10.0)
            world = client.get_world()
            
            # Find Traffic Light & Trigger Volume
            t_light = world.get_actors().filter('traffic.traffic_light')[0]
            self.target_light = t_light
            
            # Manual coordinate transformation for Stop Line
            loc = t_light.get_transform().location
            rot = t_light.get_transform().rotation
            rel = t_light.trigger_volume.location
            yaw = math.radians(rot.yaw)
            trigger_loc = loc + carla.Location(
                x = rel.x * math.cos(yaw) - rel.y * math.sin(yaw),
                y = rel.x * math.sin(yaw) + rel.y * math.cos(yaw),
                z = rel.z
            )
            
            # Spawn Ambulance
            map = world.get_map()
            spawn_loc = trigger_loc - 5.0 * map.get_waypoint(trigger_loc).transform.get_forward_vector()
            spawn_transform = map.get_waypoint(spawn_loc).transform
            spawn_transform.location.z += 1.5
            
            blueprint = world.get_blueprint_library().filter('vehicle.tesla.model3')[0]
            vehicle = world.spawn_actor(blueprint, spawn_transform)
            vehicle.set_autopilot(True)

            print("Simulation Running. Monitoring approach...")
            
            beacon_sent = False
            while True:
                dist = vehicle.get_location().distance(trigger_loc)
                
                # 1. 20m Mark: Trigger Beacon
                if dist < 20.0 and not beacon_sent:
                    print(f"--- 20m MARK: BROADCASTING V2X BEACON ---")
                    # --- NEW: Start the timer right before publishing ---
                    self.start_time = time.time() 
                    self.client.publish(TOPIC_BEACON, "RSU_NEARBY")
                    beacon_sent = True
                
                # 2. Receive RSU Command
                if self.allow_green:
                    t_light.set_state(carla.TrafficLightState.Green)
                    t_light.set_green_time(15.0)
                    print("[ACTION] Actuating Green Light per RSU Command.")
                    self.allow_green = False

                time.sleep(0.1)

        finally:
            self.client.loop_stop()
            if 'vehicle' in locals():
                vehicle.destroy()

if __name__ == "__main__":
    master = CarlaV2IMaster()
    master.run()